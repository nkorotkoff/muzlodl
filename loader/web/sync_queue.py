"""Cloud sync queue: per-file jobs + aggregate progress + 5-min watcher.

Design:
- `cloud_sync` table (see db.py schema) is the queue: one row per
  library-relative file path, status pending|uploading|done|failed.
- The watcher (`cloud_watcher`, every SYNC_INTERVAL) scans LIBRARY for
  audio files whose (path, size) isn't marked done, enqueues them, and
  spawns one small job per file (sequential chain, not parallel —
  Telegram Bot API has flood limits).
- Progress is aggregate: /api/cloud/sync/status sums the group's jobs
  (done/total across all per-file jobs of the current group).
- Changing file size re-queues the file (edited/re-encoded).
- Files deleted from disk are dropped from the queue.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .core import LIBRARY, SYNC_EXTS, SYNC_INTERVAL
from .core import _download_jobs, _jobs_lock

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(p: Path) -> str:
    return str(p.relative_to(Path(LIBRARY)))


def scan_unsynced() -> list[dict]:
    """Walk LIBRARY, enqueue new/changed files, drop vanished ones.

    Returns the list of pending rows (including previously pending).
    """
    lib = Path(LIBRARY)
    if not lib.exists():
        return []
    on_disk: dict[str, int] = {}
    for f in sorted(lib.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in SYNC_EXTS:
            continue
        if f.stat().st_size == 0:
            continue
        on_disk[_rel(f)] = f.stat().st_size
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT file_path, size, status FROM cloud_sync"
        ).fetchall()
        known = {r["file_path"]: dict(r) for r in rows}
        # Drop queue rows whose file vanished from disk.
        for path in list(known):
            if path not in on_disk and known[path]["status"] in ("pending", "failed"):
                conn.execute("DELETE FROM cloud_sync WHERE file_path=?", (path,))
        # Enqueue new files + files whose size changed (re-encoded/edited).
        # Done rows with a different size go back to pending.
        added = 0
        for path, size in on_disk.items():
            row = known.get(path)
            if row is None:
                conn.execute(
                    "INSERT INTO cloud_sync (file_path, size, status, updated_at)"
                    " VALUES (?, ?, 'pending', ?)",
                    (path, size, _now()),
                )
                added += 1
            elif row["size"] != size and row["status"] in ("done", "failed", "pending"):
                conn.execute(
                    "UPDATE cloud_sync SET size=?, status='pending',"
                    " job_id='', error='', updated_at=? WHERE file_path=?",
                    (size, _now(), path),
                )
                added += 1
        pending = [
            dict(r) for r in conn.execute(
                "SELECT file_path, size, status, job_id, error FROM cloud_sync"
                " WHERE status IN ('pending','failed') ORDER BY file_path"
            ).fetchall()
        ]
    if added:
        log.info("cloud sync: queued %d new/changed files", added)
    return pending


def sync_status() -> dict:
    """Aggregate progress across the current sync group.

    done/total come from the cloud_sync table (single source of truth),
    so the number stays correct even after a restart: done rows vs all
    rows of the current pass. Per-file jobs in _download_jobs are the
    live detail; the table is the durable total.
    """
    with db.tx() as conn:
        q = conn.execute(
            "SELECT status, COUNT(*) c FROM cloud_sync GROUP BY status"
        ).fetchall()
        queue = {r["status"]: r["c"] for r in q}
    group = _current_group()
    with _jobs_lock:
        jobs = [
            j for j in _download_jobs.values()
            if j.get("group_id") == group["id"]
        ] if group["id"] else []
        running = sum(1 for j in jobs if not j.get("done"))
    total = sum(queue.values())
    done = queue.get("done", 0)
    failed = queue.get("failed", 0)
    return {
        "group_id": group["id"],
        "done": done,
        "total": total,
        "failed": failed,
        "running": running,
        "queue": queue,
    }


def _current_group() -> dict:
    with db.tx() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='cloud_sync_group'"
        ).fetchone()
        total_row = conn.execute(
            "SELECT value FROM settings WHERE key='cloud_sync_group_total'"
        ).fetchone()
    gid = row["value"] if row else ""
    try:
        total = int(total_row["value"]) if total_row else 0
    except (ValueError, TypeError):
        total = 0
    return {"id": gid, "total": total}


def _set_group(gid: str, total: int) -> None:
    db.set_setting("cloud_sync_group", gid)
    db.set_setting("cloud_sync_group_total", str(total))


def enqueue_files(files: list[dict], group_id: str = "") -> str:
    """Create one small job per file, all sharing a group id.

    Returns the group id. Jobs run sequentially (chain): each job's
    thread starts the next one when it finishes, so Telegram never
    sees parallel sends.
    """
    from ..credentials import load_cloud
    from ..storage import make_storage

    config = load_cloud()
    if not config:
        raise RuntimeError("cloud not configured")
    gid = group_id or uuid.uuid4().hex[:8]
    _set_group(gid, len(files))
    with db.tx() as conn:
        for f in files:
            conn.execute(
                "UPDATE cloud_sync SET status='uploading', job_id='',"
                " error='', updated_at=? WHERE file_path=?",
                (_now(), f["file_path"]),
            )
    storage = make_storage(config)
    queue = list(files)
    first_id = _spawn_next(storage, queue, gid)
    log.info("cloud sync group %s: %d files, first job %s",
             gid, len(files), first_id)
    return gid


def _spawn_next(storage, queue: list, gid: str) -> str:
    """Pop the next file off the queue and spawn its job. Returns job_id."""
    if not queue:
        return ""
    item = queue.pop(0)
    job_id = str(uuid.uuid4())[:8]
    rel = item["file_path"]
    parts = rel.split("/")
    artist = parts[0] if parts else ""
    album = parts[1] if len(parts) > 2 else ""
    title = Path(rel).stem

    def _run(jid: str) -> None:
        lib = Path(LIBRARY)
        src = lib / rel
        try:
            if not src.exists():
                raise FileNotFoundError(f"vanished: {rel}")
            ok = storage.upload_single(src, artist, album, title)
            with db.tx() as conn:
                conn.execute(
                    "UPDATE cloud_sync SET status=?, error='', updated_at=?"
                    " WHERE file_path=?",
                    ("done" if ok else "failed",
                     _now(), rel),
                )
            with _jobs_lock:
                if jid in _download_jobs:
                    _download_jobs[jid]["progress"] = {
                        "ok": 1 if ok else 0, "failed": 0 if ok else 1,
                        "total": 1,
                    }
                    _download_jobs[jid]["done"] = True
                    if not ok:
                        _download_jobs[jid]["error"] = "upload failed"
        except Exception as e:
            log.warning("sync %s failed: %s", rel, e)
            with db.tx() as conn:
                conn.execute(
                    "UPDATE cloud_sync SET status='failed', error=?,"
                    " updated_at=? WHERE file_path=?",
                    (str(e)[:300], _now(), rel),
                )
            with _jobs_lock:
                if jid in _download_jobs:
                    _download_jobs[jid]["progress"] = {
                        "ok": 0, "failed": 1, "total": 1,
                    }
                    _download_jobs[jid]["done"] = True
                    _download_jobs[jid]["error"] = str(e)[:300]
        finally:
            # Chain: start the next file's job (sequential, flood-safe).
            if queue and not _cancelled(jid):
                _spawn_next(storage, queue, gid)

    def _cancelled(jid: str) -> bool:
        with _jobs_lock:
            return bool(_download_jobs.get(jid, {}).get("cancelled"))

    label = f"{artist} — {title}"[:60] if artist else title[:60]
    with _jobs_lock:
        _download_jobs[job_id] = {
            "id": job_id,
            "type": "cloud_sync",
            "group_id": gid,
            "title": f"☁ {label}",
            "done": False,
            "cancelled": False,
            "progress": {"ok": 0, "failed": 0, "total": 1},
            "error": None,
        }
    threading.Thread(target=_run, args=(job_id,), daemon=True).start()
    return job_id


def cloud_watcher(interval: int = SYNC_INTERVAL) -> None:
    """Every `interval` seconds: scan + enqueue unsynced files (auto mode)."""
    while True:
        time.sleep(interval)
        try:
            from ..credentials import load_cloud
            settings = db.get_all_settings()
            if settings.get("cloud_sync_mode", "manual") != "auto":
                continue
            if not load_cloud():
                continue
            pending = scan_unsynced()
            if not pending:
                continue
            enqueue_files(pending)
        except Exception as e:
            log.debug("cloud watcher failed: %s", e)
