"""Shared state and helpers for the web UI."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from . import db

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
STATIC = HERE / "static"
TEMPLATES = HERE / "templates"
LIBRARY = HERE.parent.parent / "library"

# Background job registry (downloads, re-encodes, cloud uploads)
_download_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# Preview temp files
_PREVIEW_CLEANUP_TTL = 600  # 10 minutes
_preview_store: dict = {}
_preview_lock = threading.Lock()
_preview_files: dict[str, float] = {}
_preview_cleanup_lock = threading.Lock()


def schedule_preview_cleanup(path: Path) -> None:
    """Register a temp file for deferred deletion."""
    with _preview_cleanup_lock:
        _preview_files[str(path)] = time.time()


def cleanup_old_previews() -> None:
    """Remove preview files older than TTL."""
    while True:
        time.sleep(60)
        now = time.time()
        with _preview_cleanup_lock:
            stale = [p for p, t in _preview_files.items() if now - t > _PREVIEW_CLEANUP_TTL]
            for p in stale:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass
                del _preview_files[p]


def count_albums() -> int:
    """Count album dirs the same way upload_library does:
    every Artist/Album pair (two levels deep).
    """
    lib = Path(LIBRARY)
    if not lib.exists():
        return 1
    count = 0
    for artist_dir in lib.iterdir():
        if not artist_dir.is_dir() or artist_dir.name.startswith("."):
            continue
        count += sum(1 for d in artist_dir.iterdir() if d.is_dir())
    return count or 1


def update_durations() -> None:
    """Re-probe durations for tracks whose file changed (after re-encode)."""
    from .db import _probe_duration
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT id, file_path FROM tracks WHERE file_path != ''"
        ).fetchall()
    lib = Path(LIBRARY)
    for r in rows:
        fp = lib / r["file_path"]
        if fp.exists():
            dur = _probe_duration(fp)
            if dur:
                with db.tx() as conn:
                    conn.execute(
                        "UPDATE tracks SET duration=? WHERE id=?", (dur, r["id"])
                    )


def auto_scan() -> None:
    """Index existing library files on first run."""
    with db.tx() as conn:
        count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    if count == 0:
        log.info("DB is empty, scanning library...")
        added = db.scan_library(Path(LIBRARY))
        log.info("indexed %d existing tracks", added)


def auto_import_log() -> None:
    """Import failed tracks from .loader.log.jsonl."""
    log_path = Path(LIBRARY) / ".loader.log.jsonl"
    if not log_path.exists():
        return
    with db.tx() as conn:
        failed_count = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE status='failed'"
        ).fetchone()[0]
    if failed_count == 0:
        result = db.import_log(log_path)
        if result["added"]:
            log.info("imported %d failed tracks from log", result["added"])


def fmt_size(b: int) -> str:
    if b > 10 ** 9:
        return f"{b / 10 ** 9:.1f}GB"
    if b > 10 ** 6:
        return f"{b / 10 ** 6:.1f}MB"
    return f"{b / 10 ** 3:.0f}KB"
