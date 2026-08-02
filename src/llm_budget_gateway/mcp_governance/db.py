"""SQLite connection helper for the mcp_governance stores.

Normative per docs/architecture/mcp-governance.md §5.1. Fully functional in
the RED phase. The connection is shared across stores and never closed by
them; the creator (the API factory or the test) closes it.
"""

import sqlite3


def open_mcp_db(db_path: str = ":memory:") -> sqlite3.Connection:
    """Open a connection for the mcp_governance stores.

    - row_factory = sqlite3.Row
    - check_same_thread=False
    - PRAGMA journal_mode=WAL when db_path is not ":memory:" (no-op otherwise)
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    return conn
