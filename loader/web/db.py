"""SQLite connection + model facade.

Connection and schema init live here (via dbconn). Data access is
delegated to `models/` (Session, Track, Setting); this module re-exports
their functions so existing callers can keep `from . import db; db.*`.
"""
from __future__ import annotations

import logging
import sqlite3

from .dbconn import _get_conn, tx
from .models import plays, session, setting, track

log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


def init_db() -> None:
    from .dbconn import DB_DIR
    DB_DIR.mkdir(parents=True, exist_ok=True)
    with tx() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS import_sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                source      TEXT    NOT NULL,
                source_name TEXT    NOT NULL DEFAULT '',
                total       INTEGER NOT NULL DEFAULT 0,
                downloaded  INTEGER NOT NULL DEFAULT 0,
                failed      INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS download_jobs (
                id          TEXT PRIMARY KEY,
                session_id  INTEGER NOT NULL,
                title       TEXT    NOT NULL DEFAULT '',
                overrides   TEXT    NOT NULL DEFAULT '{}',
                status      TEXT    NOT NULL DEFAULT 'running',
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tracks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER REFERENCES import_sessions(id) ON DELETE CASCADE,
                artist      TEXT    NOT NULL DEFAULT '',
                title       TEXT    NOT NULL DEFAULT '',
                album       TEXT    NOT NULL DEFAULT '',
                file_path   TEXT    NOT NULL DEFAULT '',
                file_size   INTEGER NOT NULL DEFAULT 0,
                duration    REAL    NOT NULL DEFAULT 0,
                status      TEXT    NOT NULL DEFAULT 'pending',
                source_name TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plays (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id   INTEGER NOT NULL,
                played_at  TEXT    NOT NULL
            );
        """)

        # Schema version check
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if not row:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            setting.seed_defaults(conn)

        # Column migration (existing DBs without duration)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()]
        if cols and "duration" not in cols:
            log.info("migrating: adding duration column")
            conn.execute("ALTER TABLE tracks ADD COLUMN duration REAL NOT NULL DEFAULT 0")


# ---- Model facade (keeps `db.*` call sites working) ----

# Session
create_session = session.create
update_session_stats = session.update_stats
list_sessions = session.list_recent
get_session = session.get

# Track
add_track = track.add
update_track_status = track.update_status
search_tracks = track.search
get_track = track.get
delete_track = track.delete
delete_tracks = track.delete_many
update_track_metadata = track.update_metadata
find_duplicates = track.duplicates
scan_library = track.scan_library

# Setting
get_setting = setting.get
set_setting = setting.set
get_all_settings = setting.all
set_all_settings = setting.set_all
import_log = setting.import_log

# Download jobs (crash/restart-resilient)
def create_job(job_id: str, session_id: int, title: str, overrides: dict) -> None:
    from datetime import datetime, timezone
    import json as _json
    now = datetime.now(timezone.utc).isoformat()
    with tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO download_jobs "
            "(id, session_id, title, overrides, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (job_id, session_id, title, _json.dumps(overrides), now, now),
        )


def update_job_status(job_id: str, status: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with tx() as conn:
        conn.execute(
            "UPDATE download_jobs SET status=?, updated_at=? WHERE id=?",
            (status, now, job_id),
        )


def list_running_jobs() -> list[dict]:
    with tx() as conn:
        rows = conn.execute(
            "SELECT * FROM download_jobs WHERE status='running' ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


# Plays
record_play = plays.record
top_tracks = plays.top_tracks
top_artists = plays.top_artists
recent_plays = plays.recent
plays_summary = plays.summary
