"""Play history model: record listens, compute stats."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..dbconn import tx

log = logging.getLogger(__name__)


def record(track_id: int) -> None:
    """Record a play of a track. No-op if the track does not exist."""
    now = datetime.now(timezone.utc).isoformat()
    with tx() as conn:
        exists = conn.execute(
            "SELECT 1 FROM tracks WHERE id=?", (track_id,)
        ).fetchone()
        if not exists:
            return
        conn.execute(
            "INSERT INTO plays (track_id, played_at) VALUES (?, ?)", (track_id, now)
        )


def top_tracks(limit: int = 20) -> list[dict]:
    """Most played tracks, joined with track info."""
    with tx() as conn:
        rows = conn.execute(
            "SELECT t.id, t.artist, t.title, t.album, t.duration, t.file_size, "
            "COUNT(p.id) AS plays, MAX(p.played_at) AS last_played "
            "FROM plays p JOIN tracks t ON t.id = p.track_id "
            "GROUP BY t.id ORDER BY plays DESC, last_played DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def top_artists(limit: int = 20) -> list[dict]:
    """Most played artists (by play count)."""
    with tx() as conn:
        rows = conn.execute(
            "SELECT t.artist, COUNT(*) AS plays, "
            "COUNT(DISTINCT t.id) AS tracks, MAX(p.played_at) AS last_played "
            "FROM plays p JOIN tracks t ON t.id = p.track_id "
            "GROUP BY LOWER(t.artist) ORDER BY plays DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def recent(limit: int = 30) -> list[dict]:
    """Recently played tracks."""
    with tx() as conn:
        rows = conn.execute(
            "SELECT t.id, t.artist, t.title, t.album, p.played_at "
            "FROM plays p JOIN tracks t ON t.id = p.track_id "
            "ORDER BY p.played_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def summary() -> dict:
    """Overall stats: total plays, unique tracks, plays in last 7/30 days."""
    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()
    month_ago = (now - timedelta(days=30)).isoformat()
    with tx() as conn:
        total = conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
        unique = conn.execute("SELECT COUNT(DISTINCT track_id) FROM plays").fetchone()[0]
        week = conn.execute(
            "SELECT COUNT(*) FROM plays WHERE played_at >= ?", (week_ago,)
        ).fetchone()[0]
        month = conn.execute(
            "SELECT COUNT(*) FROM plays WHERE played_at >= ?", (month_ago,)
        ).fetchone()[0]
    return {
        "total_plays": total,
        "unique_tracks": unique,
        "last_7_days": week,
        "last_30_days": month,
    }
