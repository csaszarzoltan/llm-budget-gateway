"""Secure named provider connections with provider-native model discovery."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PROVIDER_TYPES: list[dict[str, Any]] = [
    {
        "id": "openai",
        "name": "OpenAI",
        "description": "OpenAI API account",
        "default_base_url": "https://api.openai.com/v1",
        "discovery": "openai",
        "fields": [
            {"name": "api_key", "label": "API key", "type": "secret", "required": True},
            {"name": "base_url", "label": "Base URL", "type": "url", "required": True},
            {
                "name": "organization",
                "label": "Organization ID",
                "type": "text",
                "required": False,
            },
        ],
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "description": "Anthropic Messages API",
        "default_base_url": "https://api.anthropic.com/v1",
        "discovery": "anthropic",
        "fields": [
            {"name": "api_key", "label": "API key", "type": "secret", "required": True},
            {"name": "base_url", "label": "Base URL", "type": "url", "required": True},
        ],
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "description": "Google AI Studio Generative Language API",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "discovery": "gemini",
        "fields": [
            {"name": "api_key", "label": "API key", "type": "secret", "required": True},
            {"name": "base_url", "label": "Base URL", "type": "url", "required": True},
        ],
    },
    {
        "id": "azure_openai",
        "name": "Azure OpenAI",
        "description": "Azure OpenAI resource and deployment catalog",
        "default_base_url": "",
        "discovery": "azure",
        "fields": [
            {"name": "api_key", "label": "API key", "type": "secret", "required": True},
            {
                "name": "base_url",
                "label": "Resource endpoint",
                "type": "url",
                "required": True,
            },
            {
                "name": "api_version",
                "label": "API version",
                "type": "text",
                "required": True,
            },
        ],
    },
    {
        "id": "openai_compatible",
        "name": "OpenAI-compatible",
        "description": "Any endpoint exposing GET /v1/models",
        "default_base_url": "",
        "discovery": "openai",
        "fields": [
            {"name": "api_key", "label": "API key", "type": "secret", "required": True},
            {"name": "base_url", "label": "Base URL", "type": "url", "required": True},
            {
                "name": "user_agent",
                "label": "Client user-agent (emulation)",
                "type": "text",
                "required": False,
            },
        ],
    },
    {
        "id": "custom",
        "name": "Custom provider",
        "description": "Any HTTP model catalog with configurable authentication and fields",
        "default_base_url": "",
        "discovery": "custom",
        "fields": [
            {"name": "api_key", "label": "API key or token", "type": "secret", "required": False},
            {"name": "base_url", "label": "Base URL", "type": "url", "required": True},
            {"name": "model_list_path", "label": "Model-list path", "type": "text", "required": True},
            {"name": "auth_header", "label": "Authentication header", "type": "text", "required": False},
            {"name": "auth_prefix", "label": "Authentication prefix", "type": "text", "required": False},
            {"name": "extra_headers_json", "label": "Extra headers JSON", "type": "text", "required": False},
            {"name": "user_agent", "label": "Client user-agent (emulation)", "type": "text", "required": False},
            {"name": "models_field", "label": "Models array field", "type": "text", "required": True},
            {"name": "model_id_field", "label": "Model ID field", "type": "text", "required": True},
        ],
    },
    {
        "id": "vertex_ai",
        "name": "Google Vertex AI",
        "description": "Google Cloud Vertex AI project connection",
        "default_base_url": "",
        "discovery": "manual",
        "fields": [
            {
                "name": "service_account_json",
                "label": "Service account JSON",
                "type": "secret_multiline",
                "required": True,
            },
            {
                "name": "project_id",
                "label": "Project ID",
                "type": "text",
                "required": True,
            },
            {"name": "location", "label": "Location", "type": "text", "required": True},
            {
                "name": "base_url",
                "label": "API endpoint",
                "type": "url",
                "required": False,
            },
        ],
    },
]


class CredentialVault:
    """Encrypt provider connection secrets with a local AES-GCM master key."""

    def __init__(self, key_path: Path) -> None:
        self.key_path = key_path
        self.key = self._load_key()

    def _load_key(self) -> bytes:
        if self.key_path.exists():
            return base64.urlsafe_b64decode(self.key_path.read_bytes())
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = AESGCM.generate_key(bit_length=256)
        self.key_path.write_bytes(base64.urlsafe_b64encode(key))
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    def encrypt(self, value: dict[str, Any]) -> str:
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self.key).encrypt(
            nonce, json.dumps(value, sort_keys=True).encode(), None
        )
        return base64.urlsafe_b64encode(nonce + ciphertext).decode()

    def decrypt(self, token: str) -> dict[str, Any]:
        raw = base64.urlsafe_b64decode(token.encode())
        return json.loads(AESGCM(self.key).decrypt(raw[:12], raw[12:], None))


class ProviderConnectionStore:
    """Persist named provider accounts and their discovered model catalog."""

    def __init__(self, db: sqlite3.Connection, vault: CredentialVault) -> None:
        self.db, self.vault = db, vault
        db.executescript("""
CREATE TABLE IF NOT EXISTS provider_connections(id TEXT PRIMARY KEY,name TEXT NOT NULL,slug TEXT UNIQUE NOT NULL,provider_type TEXT NOT NULL,region TEXT NOT NULL,status TEXT NOT NULL,encrypted_config TEXT NOT NULL,last_sync TEXT,last_error TEXT,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS provider_models(provider_id TEXT NOT NULL,model_id TEXT NOT NULL,display_name TEXT,owned_by TEXT,capabilities TEXT NOT NULL,raw_json TEXT NOT NULL,last_seen TEXT NOT NULL,PRIMARY KEY(provider_id,model_id));
""")
        db.commit()

    def provider_types(self) -> list[dict[str, Any]]:
        return PROVIDER_TYPES

    def create(self, config: dict[str, Any]) -> dict[str, Any]:
        provider_type = str(config.get("provider_type", ""))
        schema = next(
            (item for item in PROVIDER_TYPES if item["id"] == provider_type), None
        )
        if not schema:
            raise ValueError("unsupported provider type")
        name, slug = (
            str(config.get("name", "")).strip(),
            str(config.get("slug", "")).strip().lower(),
        )
        if not name or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", slug):
            raise ValueError("name and a URL-safe unique slug are required")
        merged = dict(config)
        merged.setdefault("base_url", schema["default_base_url"])
        if provider_type == "custom":
            merged.setdefault("model_list_path", "/models")
            merged.setdefault("auth_header", "Authorization")
            merged.setdefault("auth_prefix", "Bearer ")
            merged.setdefault("extra_headers_json", "{}")
            merged.setdefault("models_field", "data")
            merged.setdefault("model_id_field", "id")
        missing = [
            field["label"]
            for field in schema["fields"]
            if field["required"] and not str(merged.get(field["name"], "")).strip()
        ]
        if missing:
            raise ValueError("missing connection fields: " + ", ".join(missing))
        base_url = str(merged.get("base_url", ""))
        if base_url and not base_url.startswith(("https://", "http://")):
            raise ValueError("base URL must use HTTP or HTTPS")
        public = {
            "name": name,
            "slug": slug,
            "provider_type": provider_type,
            "region": str(config.get("region", "global")),
        }
        secret_fields = {field["name"] for field in schema["fields"]}
        protected = {key: merged.get(key, "") for key in secret_fields}
        pid = "provider_" + secrets.token_hex(6)
        try:
            self.db.execute(
                "INSERT INTO provider_connections VALUES(?,?,?,?,?,'pending',?,NULL,NULL,?)",
                (
                    pid,
                    name,
                    slug,
                    provider_type,
                    public["region"],
                    self.vault.encrypt(protected),
                    _now(),
                ),
            )
            self.db.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("provider slug already exists") from exc
        return self.get(pid)

    def update(self, provider_id: str, config: dict[str, Any]) -> dict[str, Any]:
        """Update a provider connection, keeping unspecified secret fields.

        Secret fields (api_key and friends) are only replaced when the payload
        actually carries a non-empty value for them — an empty api_key in the
        edit form means "keep the stored key". base_url/name/slug/region are
        replaced from the payload (falling back to the stored value).
        """
        existing = self.get(provider_id)  # KeyError if unknown
        provider_type = str(config.get("provider_type", existing["provider_type"]))
        schema = next(
            (item for item in PROVIDER_TYPES if item["id"] == provider_type), None
        )
        if not schema:
            raise ValueError("unsupported provider type")
        name = str(config.get("name", existing["name"])).strip()
        slug = str(config.get("slug", existing["slug"])).strip().lower()
        if not name or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", slug):
            raise ValueError("name and a URL-safe unique slug are required")
        secret_fields = {field["name"] for field in schema["fields"]}
        current = self.connection_secret(provider_id)
        protected: dict[str, Any] = {}
        for key in secret_fields:
            new_value = config.get(key)
            if isinstance(new_value, str) and new_value.strip():
                protected[key] = new_value.strip()
            else:
                protected[key] = current.get(key, "")
        base_url = str(protected.get("base_url", ""))
        if base_url and not base_url.startswith(("https://", "http://")):
            raise ValueError("base URL must use HTTP or HTTPS")
        region = str(config.get("region", existing["region"]))
        try:
            self.db.execute(
                "UPDATE provider_connections SET name=?, slug=?, provider_type=?, "
                "region=?, encrypted_config=? WHERE id=?",
                (
                    name,
                    slug,
                    provider_type,
                    region,
                    self.vault.encrypt(protected),
                    provider_id,
                ),
            )
            self.db.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("provider slug already exists") from exc
        return self.get(provider_id)

    def get(self, provider_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT id,name,slug,provider_type,region,status,last_sync,last_error,created FROM provider_connections WHERE id=?",
            (provider_id,),
        ).fetchone()
        if not row:
            raise KeyError(provider_id)
        result: dict[str, Any] = {
            "id": row[0],
            "name": row[1],
            "slug": row[2],
            "provider_type": row[3],
            "region": row[4],
            "status": row[5],
            "credential_status": "configured",
            "last_sync": row[6],
            "last_error": row[7],
            "created_at": row[8],
            "model_count": self.db.execute(
                "SELECT COUNT(*) FROM provider_models WHERE provider_id=?",
                (provider_id,),
            ).fetchone()[0],
        }
        # base_url is not secret material — expose it so the UI can prefill
        # the edit form without ever round-tripping the api_key.
        try:
            result["base_url"] = self.connection_secret(provider_id).get(
                "base_url", ""
            )
        except Exception:
            result["base_url"] = ""
        return result

    def list(self) -> list[dict[str, Any]]:
        return [
            self.get(row[0])
            for row in self.db.execute(
                "SELECT id FROM provider_connections ORDER BY name"
            )
        ]

    def connection_secret(self, provider_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT encrypted_config FROM provider_connections WHERE id=?",
            (provider_id,),
        ).fetchone()
        if not row:
            raise KeyError(provider_id)
        return self.vault.decrypt(row[0])

    def raw_encrypted_value(self, provider_id: str) -> str:
        return self.db.execute(
            "SELECT encrypted_config FROM provider_connections WHERE id=?",
            (provider_id,),
        ).fetchone()[0]

    def save_models(self, provider_id: str, models: list[dict[str, Any]]) -> None:
        now = _now()
        with self.db:
            for model in models:
                capabilities = model.get("capabilities") or _capabilities(
                    str(model["id"])
                )
                self.db.execute(
                    "INSERT INTO provider_models VALUES(?,?,?,?,?,?,?) ON CONFLICT(provider_id,model_id) DO UPDATE SET display_name=excluded.display_name,owned_by=excluded.owned_by,capabilities=excluded.capabilities,raw_json=excluded.raw_json,last_seen=excluded.last_seen",
                    (
                        provider_id,
                        model["id"],
                        model.get("display_name") or model["id"],
                        model.get("owned_by"),
                        json.dumps(capabilities),
                        json.dumps(model.get("raw", model), sort_keys=True),
                        now,
                    ),
                )
            self.db.execute(
                "UPDATE provider_connections SET status='healthy',last_sync=?,last_error=NULL WHERE id=?",
                (now, provider_id),
            )

    def mark_error(self, provider_id: str, message: str) -> None:
        self.db.execute(
            "UPDATE provider_connections SET status='error',last_error=? WHERE id=?",
            (message, provider_id),
        )
        self.db.commit()

    def models(self, provider_id: str) -> list[dict[str, Any]]:
        provider = self.get(provider_id)
        rows = self.db.execute(
            "SELECT model_id,display_name,owned_by,capabilities,raw_json,last_seen FROM provider_models WHERE provider_id=? ORDER BY model_id",
            (provider_id,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "display_name": r[1],
                "owned_by": r[2],
                "capabilities": json.loads(r[3]),
                "context_length": _extract_context_length(json.loads(r[4])),
                "last_seen": r[5],
                "gateway_model": f"@{provider['slug']}/{r[0]}",
            }
            for r in rows
        ]


class ProviderDiscovery:
    """Discover models using the provider account's own model-list endpoint."""

    def __init__(
        self,
        store: ProviderConnectionStore,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.store, self.transport = store, transport

    async def sync(self, provider_id: str) -> dict[str, Any]:
        provider, config = (
            self.store.get(provider_id),
            self.store.connection_secret(provider_id),
        )
        try:
            request = _discovery_request(provider["provider_type"], config)
            async with httpx.AsyncClient(
                transport=self.transport, timeout=15.0
            ) as client:
                response = await client.request(**request)
            if response.status_code in {401, 403}:
                raise ValueError(
                    "provider authentication failed; verify the stored credential"
                )
            response.raise_for_status()
            models = _parse_models(provider["provider_type"], response.json(), config)
            self.store.save_models(provider_id, models)
            return {
                **self.store.get(provider_id),
                "models": self.store.models(provider_id),
            }
        except (httpx.HTTPError, ValueError) as exc:
            message = str(exc)
            self.store.mark_error(provider_id, message)
            raise ValueError(message) from exc


def _discovery_request(provider_type: str, config: dict[str, Any]) -> dict[str, Any]:
    base = str(config.get("base_url", "")).rstrip("/")
    if provider_type == "custom":
        path = str(config.get("model_list_path", "/models")).strip()
        if not path.startswith("/"):
            path = "/" + path
        try:
            extra_headers = json.loads(str(config.get("extra_headers_json", "{}")) or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("extra headers must be a JSON object") from exc
        if not isinstance(extra_headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in extra_headers.items()
        ):
            raise ValueError("extra headers must be a JSON object of string values")
        headers = dict(extra_headers)
        api_key = str(config.get("api_key", ""))
        auth_header = str(config.get("auth_header", "Authorization")).strip()
        if api_key and auth_header:
            headers[auth_header] = str(config.get("auth_prefix", "Bearer ")) + api_key
        return {"method": "GET", "url": base + path, "headers": headers}
    if provider_type in {"openai", "openai_compatible"}:
        headers = {"Authorization": f"Bearer {config['api_key']}"}
        if config.get("organization"):
            headers["OpenAI-Organization"] = str(config["organization"])
        return {"method": "GET", "url": base + "/models", "headers": headers}
    if provider_type == "anthropic":
        return {
            "method": "GET",
            "url": base + "/models",
            "headers": {
                "x-api-key": config["api_key"],
                "anthropic-version": "2023-06-01",
            },
        }
    if provider_type == "gemini":
        return {
            "method": "GET",
            "url": base + "/models",
            "params": {"key": config["api_key"], "pageSize": 1000},
        }
    if provider_type == "azure_openai":
        return {
            "method": "GET",
            "url": base + "/openai/models",
            "params": {"api-version": config["api_version"]},
            "headers": {"api-key": config["api_key"]},
        }
    raise ValueError(
        "automatic model discovery is not available for this provider type"
    )


def _parse_models(
    provider_type: str,
    payload: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    config = config or {}
    if provider_type == "custom":
        source = payload.get(str(config.get("models_field", "data")), [])
    else:
        source = (
            payload.get("models", [])
            if provider_type == "gemini"
            else payload.get("data", payload.get("value", []))
        )
    models = []
    for raw in source:
        configured_id = (
            raw.get(str(config.get("model_id_field", "id")))
            if provider_type == "custom"
            else None
        )
        model_id = str(
            configured_id or raw.get("id") or raw.get("name") or raw.get("model") or ""
        ).removeprefix("models/")
        if not model_id:
            continue
        models.append(
            {
                "id": model_id,
                "display_name": raw.get("display_name")
                or raw.get("displayName")
                or raw.get("name")
                or model_id,
                "owned_by": raw.get("owned_by") or raw.get("publisher"),
                "capabilities": _capabilities(model_id, raw),
                "raw": raw,
            }
        )
    return sorted(models, key=lambda item: item["id"])


def _capabilities(model_id: str, raw: dict[str, Any] | None = None) -> list[str]:
    text = (model_id + " " + json.dumps(raw or {})).lower()
    caps = ["chat"]
    if any(x in text for x in ("gpt", "claude", "gemini", "mistral", "llama")):
        caps.extend(["tools", "structured_output"])
    if any(x in text for x in ("vision", "gpt-4o", "gemini", "claude-3")):
        caps.append("vision")
    if "embed" in text:
        caps = ["embeddings"]
    return sorted(set(caps))


def _extract_context_length(raw: dict[str, Any] | None) -> int | None:
    """Pull a concrete context window from a provider's model entry.

    Provider model-list endpoints use a variety of field names; accept the
    common ones (context_length, max_context_length, context_window,
    max_model_len, ctx_len). Returns None when the provider did not publish
    a window for this model — the client then falls back to its own
    registry or leaves the target at "auto".
    """
    if not isinstance(raw, dict):
        return None
    for key in (
        "context_length",
        "max_context_length",
        "context_window",
        "max_model_len",
        "ctx_len",
        "contextSize",
    ):
        value = raw.get(key)
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat()
