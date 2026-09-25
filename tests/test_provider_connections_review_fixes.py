"""The six provider_connections.py defects the Claude Code review found.

1. SECURITY: a failed sync stored `str(exc)` verbatim, and httpx embeds the
   full request URL — Gemini carries the API key in the query string
   (`/models?key=sk-secret`). So the live credential landed in plaintext in
   provider_connections.last_error AND was served back verbatim by get().
2. MODEL DISCOVERY NEVER WORKED for the 15 OpenAI-compatible presets
   (deepinfra, together, fireworks, nebius, siliconflow, moonshot, minimax,
   dashscope, volcengine_ark, xiaomi_mimo, xiaomi_coding_plan, zai,
   zai_coding_plan): _discovery_request matched a hardcoded two-element set
   while create() had already given them the OpenAI defaults.
3. save_models inserted ORPHAN rows for an unknown provider id (no FOREIGN
   KEY) and the trailing UPDATE silently matched 0 rows — a failed write
   that looked successful.
4. A corrupt/replaced vault master key made get() return 200 with
   base_url:"" and credential_status STILL "configured"; the UI prefilled
   the edit form from that and an operator saved the empties over a working
   connection.
5. update() skipped create()'s required-field check, so
   PUT {"provider_type": "azure_openai"} with no api_version succeeded and
   left a connection that could never sync.
6. raw_encrypted_value chained .fetchone()[0] on a missing row -> TypeError
   (500) instead of KeyError (404).
"""
from __future__ import annotations

import sqlite3

import pytest

from llm_budget_gateway import provider_connections as pc
from llm_budget_gateway.provider_connections import (
    PROVIDER_TYPES,
    CredentialVault,
    ProviderConnectionStore,
    _discovery_request,
    _redact_secrets,
    _uses_openai_discovery,
)


@pytest.fixture()
def store(tmp_path) -> ProviderConnectionStore:
    # The store takes a CONNECTION + a vault, matching the shared-handle
    # shape main.py uses (it is not constructed from a path).
    return ProviderConnectionStore(
        sqlite3.connect(":memory:"), CredentialVault(tmp_path / "master.key")
    )


def _create(store: ProviderConnectionStore, **over) -> dict:
    cfg = {
        "name": "Test",
        "slug": "test-conn",
        "provider_type": "openai",
        "api_key": "sk-secret-value-123",
        "base_url": "https://api.openai.com/v1",
    }
    cfg.update(over)
    return store.create(cfg)


# --- 1. the secret leak ------------------------------------------------------

def test_redact_secrets_strips_a_query_key() -> None:
    msg = (
        "Client error '500 Server Error' for url "
        "'https://generativelanguage.googleapis.com/v1beta/models"
        "?key=AIzaSySECRETVALUE123&pageSize=1000'\n"
        "For more information check: ..."
    )
    out = _redact_secrets(msg)
    assert "AIzaSySECRETVALUE123" not in out, out
    assert "<redacted>" in out
    # the diagnostic detail is kept
    assert "generativelanguage.googleapis.com" in out
    assert "500" in out


def test_redact_secrets_strips_bearer() -> None:
    out = _redact_secrets("auth failed: Bearer sk-proj-ABCDEFGH12345678")
    assert "sk-proj-ABCDEFGH12345678" not in out
    assert "redacted" in out


def test_redact_secrets_keeps_ordinary_text() -> None:
    msg = "connection refused on https://api.example.com/v1/models"
    assert _redact_secrets(msg) == msg
    assert _redact_secrets("") == ""


def test_mark_error_does_not_persist_the_key(store: ProviderConnectionStore) -> None:
    created = _create(store, provider_type="gemini", base_url="https://g/v1beta")
    pid = created["id"]
    store.mark_error(
        pid,
        "500 for url 'https://g/v1beta/models?key=AIzaSyTOPSECRET'",
    )
    row = store.get(pid)
    assert "AIzaSyTOPSECRET" not in (row.get("last_error") or "")
    assert "AIzaSyTOPSECRET" not in str(row)
    assert row["status"] == "error"


# --- 2. model discovery for the OpenAI-compatible presets --------------------

def test_every_openai_discovery_preset_can_build_a_request() -> None:
    """The parse branch already handled these; the request branch did not."""
    failing: list[str] = []
    for preset in PROVIDER_TYPES:
        pid = preset["id"]
        if not _uses_openai_discovery(pid):
            continue
        try:
            req = _discovery_request(
                pid, {"base_url": "https://x.example/v1", "api_key": "k"}
            )
            assert req["url"].endswith("/models"), req
            assert "Authorization" in req["headers"], req
        except Exception as exc:  # noqa: BLE001 - that is the finding
            failing.append(f"{pid}: {type(exc).__name__}: {exc}")
    assert not failing, "discovery unavailable for: " + "; ".join(failing)


def test_the_known_13_now_work() -> None:
    expected = {
        "deepinfra", "together", "fireworks", "nebius", "siliconflow",
        "moonshot", "minimax", "dashscope", "volcengine_ark",
        "xiaomi_mimo", "xiaomi_coding_plan", "zai", "zai_coding_plan",
    }
    covered = {p["id"] for p in PROVIDER_TYPES if _uses_openai_discovery(p["id"])}
    missing = expected - covered
    assert not missing, f"still not openai-discovery: {sorted(missing)}"


def test_gemini_and_anthropic_keep_their_own_shapes() -> None:
    g = _discovery_request("gemini", {"base_url": "https://g/v1beta", "api_key": "k"})
    assert g["params"]["key"] == "k"
    a = _discovery_request("anthropic", {"base_url": "https://a", "api_key": "k"})
    assert a["headers"]["x-api-key"] == "k"


def test_unknown_provider_type_is_not_openai() -> None:
    assert _uses_openai_discovery("does_not_exist") is False


# --- 3. no orphan model rows ------------------------------------------------

def test_save_models_rejects_an_unknown_provider(store: ProviderConnectionStore) -> None:
    with pytest.raises(KeyError):
        store.save_models("provider_nonexistent", [{"id": "m"}])


def test_save_models_still_works_for_a_real_provider(
    store: ProviderConnectionStore,
) -> None:
    pid = _create(store)["id"]
    store.save_models(pid, [{"id": "gpt-x", "display_name": "X"}])
    ids = [m["id"] for m in store.models(pid)]
    assert "gpt-x" in ids


# --- 4. an unreadable vault must not look configured ------------------------

def test_unreadable_vault_is_reported_not_faked(store: ProviderConnectionStore) -> None:
    pid = _create(store)["id"]
    # break decryption
    store.vault.decrypt = lambda token: (_ for _ in ()).throw(  # type: ignore[method-assign]
        ValueError("InvalidTag")
    )
    row = store.get(pid)
    assert row["credential_status"] != "configured", row
    assert row["credential_status"] == "unreadable"
    assert row["base_url"] is None, row
    assert "base_url" in row["credential_error"] or row["credential_error"]


# --- 5. update() must validate a provider_type change -----------------------

def test_update_validates_required_fields_of_the_new_type(
    store: ProviderConnectionStore,
) -> None:
    pid = _create(store)["id"]
    with pytest.raises(ValueError, match="missing connection fields"):
        store.update(pid, {"provider_type": "azure_openai"})


def test_update_still_works_for_a_no_op_edit(
    store: ProviderConnectionStore,
) -> None:
    pid = _create(store)["id"]
    out = store.update(pid, {"name": "Renamed"})
    assert out["name"] == "Renamed"
    assert out["provider_type"] == "openai"


# --- 6. a missing id is a 404, not a 500 -----------------------------------

def test_raw_encrypted_value_raises_key_error(store: ProviderConnectionStore) -> None:
    with pytest.raises(KeyError):
        store.raw_encrypted_value("provider_bogus")


def test_raw_encrypted_value_returns_the_ciphertext(
    store: ProviderConnectionStore,
) -> None:
    pid = _create(store)["id"]
    assert isinstance(store.raw_encrypted_value(pid), str)
