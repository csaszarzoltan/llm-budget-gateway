"""Anthropic Messages API <-> OpenAI chat-completions translation.

Claude Code speaks the Anthropic Messages API (``POST /v1/messages`` with
``x-api-key`` / ``ANTHROPIC_AUTH_TOKEN`` auth). The gateway's routing,
fallback, cooldown and cost machinery all works on the OpenAI shape, so
this module translates at the EDGE: inbound Anthropic -> OpenAI before
``handle_chat_completion``, outbound OpenAI (+SSE) -> Anthropic after.

Stateless pure functions — every toolkit the gateway supports (tools,
vision, streaming) is mapped; anything unmappable degrades loudly (400),
never silently.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any


# ---------------------------------------------------------------------------
# inbound: Anthropic Messages -> OpenAI chat completion
# ---------------------------------------------------------------------------

_ANTHROPIC_STOP_MAP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
}

#: OpenAI finish_reason -> Anthropic stop_reason.
_OPENAI_FINISH_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "stop_sequence",
}


def _content_to_text(content: Any) -> str:
    """Anthropic content (str | list[blocks]) -> plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("type")
                if t in ("text",):
                    parts.append(str(block.get("text", "")))
                elif t == "tool_result":
                    c = block.get("content", "")
                    parts.append(_content_to_text(c))
                elif t in ("image", "document"):
                    parts.append(f"[{t} block omitted in text conversion]")
        return "\n".join(p for p in parts if p)
    return str(content)


def _anthropic_block_to_openai(block: dict) -> dict:
    """One Anthropic content block -> OpenAI chat content part."""
    t = block.get("type")
    if t == "text":
        return {"type": "text", "text": str(block.get("text", ""))}
    if t == "image":
        src = block.get("source", {})
        if src.get("type") == "base64":
            return {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                },
            }
        if src.get("type") == "url":
            return {"type": "image_url", "image_url": {"url": str(src.get("url", ""))}}
        raise ValueError(f"unsupported image source type: {src.get('type')!r}")
    raise ValueError(f"unsupported Anthropic content block type: {t!r}")


def _tool_result_to_openai_message(msg: dict) -> dict:
    """Anthropic user msg carrying tool_result blocks -> OpenAI tool message(s).

    Returns a LIST-carrier: the caller splices it into the message stream.
    """
    out: list[dict] = []
    others: list[dict] = []
    for block in msg.get("content", []):
        if isinstance(block, dict) and block.get("type") == "tool_result":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": str(block.get("tool_use_id", "")),
                    "content": _content_to_text(block.get("content", "")),
                }
            )
        elif isinstance(block, dict):
            others.append(_anthropic_block_to_openai(block))
        elif isinstance(block, str):
            others.append({"type": "text", "text": block})
    if others:
        out.append({"role": "user", "content": others})
    return {"_splice": out}  # type: ignore[return-value]


def anthropic_to_openai(body: dict) -> dict:
    """Translate a ``POST /v1/messages`` body to ``/v1/chat/completions`` shape.

    Raises ``ValueError`` on anything that cannot be faithfully mapped.
    """
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    messages_in = body.get("messages")
    if not isinstance(messages_in, list) or not messages_in:
        raise ValueError("'messages' must be a non-empty array")
    model = body.get("model", "")
    if not model:
        raise ValueError("'model' is required")

    # Claude Code sends its own model id (e.g. a Sonnet/Opus slug); the route
    # name is resolved from ANTHROPIC_MODEL env client-side, so an unknown
    # literal here is passed through — the gateway's _model_known decides.
    out: dict[str, Any] = {"model": model}
    max_tokens = body.get("max_tokens")
    if max_tokens is not None:
        try:
            out["max_tokens"] = int(max_tokens)
        except (TypeError, ValueError):
            raise ValueError("'max_tokens' must be an integer")

    system = body.get("system")
    messages: list[dict] = []
    if system is not None:
        messages.append({"role": "system", "content": _content_to_text(system)})

    for msg in messages_in:
        if not isinstance(msg, dict):
            raise ValueError("each message must be an object")
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            # Claude Code puts the system prompt INSIDE messages (Anthropic
            # accepts only the top-level `system` field, but several clients
            # — and Claude Code itself — send a system-role row instead).
            # OpenAI has no system role after the first message, so fold it
            # into a leading system message (merging with a top-level one).
            sys_text = _content_to_text(content)
            if sys_text:
                messages.append({"role": "system", "content": sys_text})
        elif role == "user":
            blocks = content if isinstance(content, list) else [content]
            has_tool_result = any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks
            )
            if has_tool_result and isinstance(content, list):
                spliced = _tool_result_to_openai_message(msg)["_splice"]
                messages.extend(spliced)
            elif isinstance(content, str):
                messages.append({"role": "user", "content": content})
            else:
                messages.append(
                    {
                        "role": "user",
                        "content": [_anthropic_block_to_openai(b) for b in blocks if isinstance(b, dict)],
                    }
                )
                # plain-string blocks mixed in
                for b in blocks:
                    if isinstance(b, str):
                        messages[-1]["content"].append({"type": "text", "text": b})
        elif role == "assistant":
            if isinstance(content, str):
                messages.append({"role": "assistant", "content": content})
                continue
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for block in content if isinstance(content, list) else []:
                if isinstance(block, str):
                    text_parts.append(block)
                elif isinstance(block, dict):
                    bt = block.get("type")
                    if bt == "text":
                        text_parts.append(str(block.get("text", "")))
                    elif bt == "tool_use":
                        try:
                            args = block.get("input", {})
                            args_s = json.dumps(args) if isinstance(args, dict) else str(args)
                        except Exception:
                            args_s = "{}"
                        tool_calls.append(
                            {
                                "id": str(block.get("id", "")),
                                "type": "function",
                                "function": {
                                    "name": str(block.get("name", "")),
                                    "arguments": args_s,
                                },
                            }
                        )
                    else:
                        raise ValueError(
                            f"unsupported assistant block type: {bt!r}"
                        )
            amsg: dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(text_parts) if text_parts else None,
            }
            if tool_calls:
                amsg["tool_calls"] = tool_calls
            messages.append(amsg)
        else:
            raise ValueError(f"unsupported role: {role!r}")

    out["messages"] = messages

    tools = body.get("tools")
    if tools is not None:
        if not isinstance(tools, list):
            raise ValueError("'tools' must be an array")
        out_tools: list[dict] = []
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("type", "custom") != "custom":
                # server_tool / computer-use shapes have no OpenAI equivalent
                name = tool.get("name", "?") if isinstance(tool, dict) else "?"
                raise ValueError(
                    f"unsupported tool type for tool {name!r} (only custom tools map)"
                )
            out_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": str(tool.get("name", "")),
                        "description": str(tool.get("description", "")),
                        "parameters": tool.get("input_schema", {}),
                    },
                }
            )
        out["tools"] = out_tools

    tool_choice = body.get("tool_choice")
    if tool_choice is not None:
        if isinstance(tool_choice, dict):
            t = tool_choice.get("type")
            if t == "auto":
                out["tool_choice"] = "auto"
            elif t == "any":
                out["tool_choice"] = "required"
            elif t == "tool":
                out["tool_choice"] = {
                    "type": "function",
                    "function": {"name": str(tool_choice.get("name", ""))},
                }
            elif t == "none":
                out["tool_choice"] = "none"
            else:
                raise ValueError(f"unsupported tool_choice type: {t!r}")
        else:
            raise ValueError("'tool_choice' must be an object")

    for key in ("temperature", "top_p", "top_k", "stop_sequences"):
        if body.get(key) is not None:
            if key == "top_k":
                continue  # no OpenAI equivalent — dropped deliberately
            if key == "stop_sequences":
                out["stop"] = body[key]
            else:
                out[key] = body[key]

    stream = body.get("stream")
    if stream is not None:
        out["stream"] = bool(stream)

    metadata = body.get("metadata")
    if isinstance(metadata, dict) and metadata.get("user_id"):
        out["user"] = str(metadata["user_id"])
    return out


# ---------------------------------------------------------------------------
# outbound: OpenAI (+SSE) -> Anthropic Messages
# ---------------------------------------------------------------------------


def _openai_tool_calls_to_anthropic(tool_calls: list[dict]) -> list[dict]:
    blocks: list[dict] = []
    for tc in tool_calls:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError, AttributeError):
            args = {}
        blocks.append(
            {
                "type": "tool_use",
                "id": str(tc.get("id", f"toolu_{uuid.uuid4().hex[:8]}")),
                "name": str(fn.get("name", "")),
                "input": args if isinstance(args, dict) else {},
            }
        )
    return blocks


def openai_to_anthropic(
    response: dict, *, model: str, usage_in: dict | None = None
) -> dict:
    """Translate an OpenAI non-stream chat completion to Messages shape."""
    if not isinstance(response, dict):
        raise ValueError("upstream response must be an object")
    choices = response.get("choices") or []
    if not choices:
        raise ValueError("upstream response has no choices")
    choice = choices[0]
    message = choice.get("message", {}) or {}
    content: list[dict] = []
    text = message.get("content")
    if text:
        content.append({"type": "text", "text": str(text)})
    tcs = message.get("tool_calls") or []
    if tcs:
        content.extend(_openai_tool_calls_to_anthropic(tcs))
    if not content:
        content.append({"type": "text", "text": ""})

    finish = choice.get("finish_reason") or "stop"
    stop_reason = _OPENAI_FINISH_MAP.get(finish, "end_turn")

    usage = response.get("usage") or usage_in or {}
    return {
        "id": response.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "output_tokens": int(usage.get("completion_tokens", 0) or 0),
        },
    }


def openai_sse_to_anthropic_sse(
    openai_event: dict, *, message_id: str, model: str, emit_done: bool = False
) -> list[str]:
    """Translate ONE OpenAI SSE chunk dict to 0..n Anthropic SSE lines.

    The endpoint wrapper owns ``message_start``/``ping`` framing; this maps
    the per-chunk deltas (text + tool-call streaming).
    """
    lines: list[str] = []
    if emit_done:
        lines.append("event: message_stop")
        lines.append(json.dumps({"type": "message_stop"}))
        return lines
    choices = openai_event.get("choices") or []
    if not choices:
        return lines
    delta = choices[0].get("delta", {}) or {}
    text = delta.get("content")
    if text:
        lines.append("event: content_block_delta")
        lines.append(
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": str(text)},
                }
            )
        )
    for tc in delta.get("tool_calls") or []:
        fn = tc.get("function", {}) or {}
        lines.append("event: content_block_delta")
        lines.append(
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": str(fn.get("arguments") or ""),
                    },
                }
            )
        )
    return lines


def anthropic_message_start(*, message_id: str, model: str) -> list[str]:
    return [
        "event: message_start",
        json.dumps(
            {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            }
        ),
    ]


def anthropic_error(status_code: int, message: str) -> dict:
    kind = "authentication_error" if status_code in (401, 403) else (
        "not_found_error" if status_code == 404 else "invalid_request_error"
    )
    return {"type": "error", "error": {"type": kind, "message": str(message)}}


def extract_anthropic_key(headers: dict) -> str:
    """Accept ``x-api-key`` (native) + ``Authorization: Bearer`` (fallback)."""
    if not isinstance(headers, dict):
        return ""
    lowered = {str(k).lower(): v for k, v in headers.items()}
    xkey = str(lowered.get("x-api-key", "") or "").strip()
    if xkey:
        return xkey
    auth = str(lowered.get("authorization", "") or "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth.strip()


def current_ms_id(prefix: str = "msg") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
