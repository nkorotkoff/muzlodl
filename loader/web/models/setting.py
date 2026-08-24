"""Settings model + .loader.log.jsonl import."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..dbconn import tx

log = logging.getLogger(__name__)

DEFAULTS = {
    "quality": "128",
    "sources": json.dumps([
        "youtube", "soundcloud", "lightaudio", "mp3party", "archiveorg",
    ]),
    "max_path_len": "0",
    "enrich": "true",
    "parallel": "4",
    "cloud_backend": "",
    "password": "",
    "acoustid_api_key": "",
    "acoustid_verify": "false",
    "acoustid_min_score": "0.5",
}


def seed_defaults(conn) -> None:
    for k, v in DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
        )


def get(key: str, default: str = "") -> str:
    with tx() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set(key: str, value: str) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )


def all() -> dict:
    with tx() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def set_all(settings: dict) -> None:
    with tx() as conn:
        for k, v in settings.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v))
            )


# ---- Historical log import ----

_LOG_SESSION_ID: int | None = None


def import_log(log_path: Path) -> dict[str, int]:
    """Import failed/cached entries from .loader.log.jsonl into DB.

    Creates one session for log-derived tracks and adds missing ones.
    Returns counts: {added, skipped, total}.
    """
    if not log_path.exists():
        return {"added": 0, "skipped": 0, "total": 0}

    from .track import add as add_track
    from .session import create as create_session

    added = 0
    skipped = 0
    total = 0

    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            artist = rec.get("artist", "") or ""
            title = rec.get("title", "") or ""
            album = rec.get("album", "") or ""
            status = rec.get("status", "failed") or "failed"
            source = rec.get("source") or ""

            # Skip if already in DB (matched by artist+title)
            with tx() as conn:
                existing = conn.execute(
                    "SELECT id FROM tracks WHERE artist=? AND title=? LIMIT 1",
                    (artist, title),
                ).fetchone()
            if existing:
                skipped += 1
                continue

            sid = _ensure_log_session()
            add_track(
                session_id=sid,
                artist=artist,
                title=title,
                album=album,
                status=status,
                source_name=source,
            )
            added += 1

    return {"added": added, "skipped": skipped, "total": total}


def _ensure_log_session() -> int:
    global _LOG_SESSION_ID
    if _LOG_SESSION_ID is not None:
        return _LOG_SESSION_ID
    with tx() as conn:
        row = conn.execute(
            "SELECT id FROM import_sessions WHERE source='log' LIMIT 1"
        ).fetchone()
        if row:
            _LOG_SESSION_ID = row["id"]
        else:
            from .session import create as create_session
            _LOG_SESSION_ID = create_session(
                source="log", source_name="Historical log (pre-web)", total=0,
            )
    return _LOG_SESSION_ID
