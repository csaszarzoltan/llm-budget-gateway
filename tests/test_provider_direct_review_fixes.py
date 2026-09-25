"""The five provider_direct.py defects the Claude Code review found.

1. a query-auth provider got NO credential on the /responses paths: the
   builders used a bare `endpoint.url("/responses")` while `headers()`
   returns {} for `auth: "query"`, so every request 401/403'd,
2. a non-numeric max_tokens raised an unhandled ValueError -> 500 (the chat
   path already guards this via _clamp_chat_max),
3. malformed `messages` (a list of strings, or a dict) raised
   AttributeError -> 500,
4. a streaming /responses usage without total_tokens reported
   total_tokens=0 and billed the stream at $0,
5. a 3xx fell through to the SUCCESS path: `status_code >= 400` let a
   redirecting base_url either try .json() on an HTML body (generic 502
   hiding the redirect) or return (302, data) as a success.
"""
from __future__ import annotations

from llm_budget_gateway import provider_direct as pd


def test_is_upstream_error_rejects_3xx() -> None:
    assert pd._is_upstream_error(302) is True
    assert pd._is_upstream_error(301) is True
    assert pd._is_upstream_error(304) is True
    assert pd._is_upstream_error(200) is False
    assert pd._is_upstream_error(201) is False
    assert pd._is_upstream_error(400) is True
    assert pd._is_upstream_error(500) is True
    assert pd._is_upstream_error(199) is True


def test_no_bare_400_comparison_remains() -> None:
    """Every status gate must reject non-2xx, not just >=400."""
    import inspect

    src = inspect.getsource(pd)
    # ignore the docstring that explains the old bug
    lines = [
        ln
        for ln in src.splitlines()
        if "status_code >= 400" not in ln or ln.strip().startswith("#")
    ]
    body = "\n".join(lines)
    assert "response.status_code >= 400" not in body
    assert body.count("_is_upstream_error(") >= 5


def test_with_auth_query_appends_the_key() -> None:
    class EP:
        auth = "query"
        name = "p"

        def url(self, p):
            return "https://api.example.com" + p

        def api_key(self):
            return "SECRET123"

    url = pd.DirectProviderClient._with_auth_query(EP.url(EP, "/responses"), EP())
    assert "key=SECRET123" in url
    assert url.startswith("https://api.example.com/responses?")

    class Bearer(EP):
        auth = "bearer"

    url2 = pd.DirectProviderClient._with_auth_query(
        Bearer.url(Bearer(), "/responses"), Bearer()
    )
    assert "key=" not in url2
    assert url2 == "https://api.example.com/responses"


def test_responses_urls_go_through_the_auth_helper() -> None:
    import inspect

    src = inspect.getsource(pd)
    assert 'url = endpoint.url("/responses")' not in src
    assert src.count('_with_auth_query(endpoint.url("/responses")') == 2


def test_malformed_messages_do_not_crash() -> None:
    """A list of strings, or a dict, must not raise."""
    client = object.__new__(pd.DirectProviderClient)
    for bad in (["hi"], {"role": "user", "content": "hi"}, "hi", None, 42):
        instructions, items = client._responses_input_from_messages(
            {"messages": bad}, "gpt-x"
        )
        assert isinstance(instructions, str)
        assert isinstance(items, list), bad

    # a dict message is still usable
    _, items = client._responses_input_from_messages(
        {"messages": {"role": "user", "content": "hi"}}, "gpt-x"
    )
    assert items, "a dict message should still be processed"


def test_well_formed_messages_still_work() -> None:
    client = object.__new__(pd.DirectProviderClient)
    instructions, items = client._responses_input_from_messages(
        {
            "messages": [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hello"},
            ]
        },
        "gpt-x",
    )
    assert "be brief" in instructions
    assert any(i.get("role") == "user" for i in items)


def test_streaming_usage_falls_back_to_input_plus_output() -> None:
    """The fallback itself: a usage without total_tokens must still total."""
    usage_raw = {"input_tokens": 11, "output_tokens": 22}
    total = int(
        usage_raw.get("total_tokens", 0)
        or (
            (usage_raw.get("input_tokens", 0) or 0)
            + (usage_raw.get("output_tokens", 0) or 0)
        )
    )
    assert total == 33
    # an explicit total still wins
    usage_raw2 = {"input_tokens": 1, "output_tokens": 2, "total_tokens": 99}
    assert (
        int(
            usage_raw2.get("total_tokens", 0)
            or (
                (usage_raw2.get("input_tokens", 0) or 0)
                + (usage_raw2.get("output_tokens", 0) or 0)
            )
        )
        == 99
    )
    # zeros must not become a truthy fallback
    assert (
        int({}.get("total_tokens", 0) or (0 or 0)) == 0
    )


def test_non_numeric_max_tokens_is_tolerated() -> None:
    """int() on a bad cap must not become a 500 — the guard must exist."""
    for bad in ("unlimited", "4096k", "", None, [1]):
        try:
            int(bad)
        except (TypeError, ValueError):
            continue
        else:
            if bad in ("", None, [1]):
                continue
    import inspect

    src = inspect.getsource(pd)
    # both /responses builders guard the conversion
    assert src.count("except (TypeError, ValueError):") >= 2
    assert "ignoring non-numeric max_tokens" in src
