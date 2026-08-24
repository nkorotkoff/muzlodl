"""SQLite connection primitives, shared by db.py and models/."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DB_DIR = Path(__file__).parent.parent.parent / "library"
DB_PATH = DB_DIR / "library.db"

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Thread-local connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Transaction context: commit on success, rollback on error."""
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
