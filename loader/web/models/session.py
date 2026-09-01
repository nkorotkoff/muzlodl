"""Import session model."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..dbconn import tx


def create(source: str, source_name: str = "", total: int = 0) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO import_sessions (source, source_name, total, created_at) "
            "VALUES (?, ?, ?, ?)",
            (source, source_name, total, now),
        )
        return cur.lastrowid


def update_stats(session_id: int) -> None:
    with tx() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tracks WHERE session_id=? GROUP BY status",
            (session_id,),
        ).fetchall()
        ok = sum(r["cnt"] for r in rows if r["status"] in ("ok", "cached"))
        fail = sum(r["cnt"] for r in rows if r["status"] == "failed")
        conn.execute(
            "UPDATE import_sessions SET downloaded=?, failed=? WHERE id=?",
            (ok, fail, session_id),
        )


def list_recent(limit: int = 50) -> list[dict]:
    with tx() as conn:
        rows = conn.execute(
            "SELECT * FROM import_sessions WHERE source != 'scan' ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get(session_id: int) -> Optional[dict]:
    with tx() as conn:
        row = conn.execute(
            "SELECT * FROM import_sessions WHERE id=?", (session_id,)
        ).fetchone()
        return dict(row) if row else None
