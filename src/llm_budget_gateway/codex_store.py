"""Encrypted OAuth store for Codex credentials (gateway-owned, independent from Hermes).

Same AES-GCM vault pattern as provider_connections.CredentialVault, but a
dedicated SQLite DB so OAuth rotation never touches providers.db rows.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CodexCredentialVault:
    def __init__(self, key_path: Path) -> None:
        self.key_path = Path(key_path)
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
        ct = AESGCM(self.key).encrypt(nonce, json.dumps(value, sort_keys=True).encode(), None)
        return base64.urlsafe_b64encode(nonce + ct).decode()

    def decrypt(self, token: str) -> dict[str, Any]:
        raw = base64.urlsafe_b64decode(token.encode())
        return json.loads(AESGCM(self.key).decrypt(raw[:12], raw[12:], None))


class CodexOAuthStore:
    """Persist one OAuth pair per provider_id, encrypted at rest."""

    def __init__(self, db_path: Path | str, key_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.vault = CodexCredentialVault(Path(key_path))
        self.db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.db.executescript(
            """
CREATE TABLE IF NOT EXISTS codex_oauth(
  provider_id TEXT PRIMARY KEY,
  encrypted_tokens TEXT NOT NULL,
  account_id TEXT,
  updated_at TEXT NOT NULL
);
"""
        )
        self.db.commit()

    def save(self, *, provider_id: str, access_token: str, refresh_token: str, account_id: str | None = None) -> None:
        now = datetime.now(UTC).isoformat()
        payload = {"access_token": access_token, "refresh_token": refresh_token}
        if account_id:
            payload["account_id"] = account_id
        enc = self.vault.encrypt(payload)
        self.db.execute(
            "INSERT INTO codex_oauth(provider_id, encrypted_tokens, account_id, updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(provider_id) DO UPDATE SET encrypted_tokens=excluded.encrypted_tokens, account_id=excluded.account_id, updated_at=excluded.updated_at",
            (provider_id, enc, account_id, now),
        )
        self.db.commit()

    def load(self, provider_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT encrypted_tokens FROM codex_oauth WHERE provider_id=?", (provider_id,)).fetchone()
        if not row:
            return None
        try:
            return self.vault.decrypt(row[0])
        except Exception:
            return None

    def delete(self, provider_id: str) -> None:
        self.db.execute("DELETE FROM codex_oauth WHERE provider_id=?", (provider_id,))
        self.db.commit()

    def list_ids(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT provider_id FROM codex_oauth").fetchall()]
