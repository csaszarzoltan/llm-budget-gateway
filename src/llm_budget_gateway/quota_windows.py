"""Provider plan-limit windows (weekly, monthly, 5-hour) read from cooldowns.

Subscription and flat-rate providers answer 429 with a usage-limit error that
names the window and when it resets ("Weekly usage limit reached. Resets in
1hr 18min."). The proxy stores the raw answer as the cooldown reason; this
module turns it into a structured, display-safe entry so a quota wall is
visible before the next request hits it.

Rejected alternative: querying each provider's usage page. Those pages are
not an API for most plans and change without notice; the 429 body is the one
signal every provider already sends.
"""

from __future__ import annotations

import json
import re
from typing import Any

_QUOTA_ERROR_TYPES = ("usagelimit", "usage_limit", "quota")
_WINDOW_WORDS = (
    ("5-hour", re.compile(r"\b5[- ]?hours?\b|\bfive[- ]hour\b", re.I)),
    ("weekly", re.compile(r"\bweek(ly)?\b", re.I)),
    ("monthly", re.compile(r"\bmonth(ly)?\b", re.I)),
    ("daily", re.compile(r"\bdaily\b|\bper day\b", re.I)),
    ("hourly", re.compile(r"\bhourly\b", re.I)),
)
_RESET = re.compile(r"resets?\s+in\s+([^.]*)", re.I)
_UNITS = (
    (re.compile(r"(\d+)\s*d(?:ays?)?\b", re.I), 86400),
    (re.compile(r"(\d+)\s*h(?:rs?|ours?)?\b", re.I), 3600),
    (re.compile(r"(\d+)\s*m(?:in(?:ute)?s?)?\b", re.I), 60),
)
_URL = re.compile(r"\s*(?:To continue[^:]*:\s*)?https?://\S+", re.I)
_DANGLING = re.compile(r"\s+(?:at|on|here)\s*[.:]?$", re.I)
_CREDITS = re.compile(r"no credits|insufficient[_ ]quota|out of credits", re.I)


def _loads(value: object) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return None


def _reset_seconds(message: str) -> int | None:
    match = _RESET.search(message)
    if not match:
        return None
    text = match.group(1)
    total = sum(
        int(m.group(1)) * unit for pattern, unit in _UNITS for m in [pattern.search(text)] if m
    )
    return total or None


def _safe_message(message: str) -> str:
    """Keep the sentence about the limit; drop billing links and workspace ids."""
    cut = re.split(r"\s+To continue", message, maxsplit=1)[0]
    return _DANGLING.sub("", _URL.sub("", cut).strip()).strip()


def parse_quota_reason(reason: str) -> dict[str, object] | None:
    """Return ``{window, message, reset_hint_seconds}`` for a usage-limit
    reason, or ``None`` when the reason is not a plan-limit answer."""
    outer = _loads(reason)
    if not isinstance(outer, dict):
        return None
    body = _loads(outer.get("body")) if outer.get("body") else None
    body = body if isinstance(body, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    message = str(error.get("message") or "")
    err_type = str(error.get("type") or "").lower()
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    limit_name = str(metadata.get("limitName") or "").lower()
    is_quota = any(t in err_type for t in _QUOTA_ERROR_TYPES) or bool(
        re.search(r"usage limit|quota", message, re.I)
    )
    if not is_quota:
        return None
    window = limit_name or next(
        (name for name, pattern in _WINDOW_WORDS if pattern.search(message)),
        "credits" if _CREDITS.search(message + " " + err_type) else "unknown",
    )
    return {
        "window": window,
        "message": _safe_message(message),
        "reset_hint_seconds": _reset_seconds(message),
    }


def quota_windows(cooldowns: list[dict[str, object]]) -> list[dict[str, object]]:
    """Plan-limit entries from active cooldowns, one per model and window.

    The cooldown end (``remaining_seconds``) is the gateway's own retry time,
    so it is reported; the provider's hint is kept beside it. When several
    routes park the same model, the longest remaining time wins and every
    route is listed.
    """
    merged: dict[tuple[str, str], dict[str, object]] = {}
    for cooldown in cooldowns:
        parsed = parse_quota_reason(str(cooldown.get("reason") or ""))
        if parsed is None:
            continue
        key = (str(cooldown["model"]), str(parsed["window"]))
        remaining = int(cooldown.get("remaining_seconds") or 0)
        entry = merged.get(key)
        if entry is None:
            merged[key] = {
                "model": cooldown["model"],
                "window": parsed["window"],
                "message": parsed["message"],
                "reset_hint_seconds": parsed["reset_hint_seconds"],
                "remaining_seconds": remaining,
                "until": cooldown.get("until"),
                "routes": [cooldown["route"]],
            }
        else:
            entry["routes"].append(cooldown["route"])  # type: ignore[attr-defined]
            if remaining > int(entry["remaining_seconds"]):  # type: ignore[call-overload]
                entry["remaining_seconds"] = remaining
                entry["until"] = cooldown.get("until")
    return sorted(merged.values(), key=lambda e: int(e["remaining_seconds"]))  # type: ignore[call-overload]
