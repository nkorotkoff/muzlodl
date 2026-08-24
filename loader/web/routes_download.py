"""Download/import routes: start downloads, session tracking, job status."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

from . import db
from .core import LIBRARY, _download_jobs, _jobs_lock

log = logging.getLogger(__name__)

bp = Blueprint("download", __name__)


@bp.route("/api/imports")
def api_imports():
    sessions = db.list_sessions()
    # Sessions whose job died (server restart, cancel) stay pending
    # forever in the DB. Flag them so the UI doesn't show a stuck 0%.
    with _jobs_lock:
        active = {j.get("session_id") for j in _download_jobs.values()}
    for s in sessions:
        done = (s.get("downloaded") or 0) + (s.get("failed") or 0)
        if s.get("id") not in active and done < (s.get("total") or 0):
            s["status"] = "interrupted"
    return jsonify({"sessions": sessions})


@bp.route("/api/imports/<int:sid>")
def api_import_detail(sid: int):
    session_data = db.get_session(sid)
    if not session_data:
        return jsonify({"error": "not found"}), 404
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT id, artist, title, album, status, source_name "
            "FROM tracks WHERE session_id=? ORDER BY id",
            (sid,),
        ).fetchall()
    return jsonify({"session": session_data, "tracks": [dict(r) for r in rows]})


@bp.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json()
    if not data:
        return jsonify({"error": "no JSON body"}), 400

    source_type = data.get("source", "text")
    options = data.get("options", {})
    job_id = str(uuid.uuid4())[:8]

    # Persist the chosen download options as future defaults
    for key in ("quality", "parallel", "max_path_len", "enrich"):
        if key in options and options[key] is not None:
            db.set_setting(key, str(options[key]))

    # Structured input (array of {artist,title,album}) beats text parsing:
    # titles/albums containing " - " survive intact.
    tracks = data.get("tracks")
    if isinstance(tracks, list) and tracks:
        tracks = [
            {
                "artist": str(t.get("artist", "")).strip(),
                "title": str(t.get("title", "")).strip(),
                "album": str(t.get("album", "")).strip(),
            }
            for t in tracks
            if str(t.get("title", "")).strip()
        ]
    else:
        content = data.get("content", "").strip()
        if not content:
            return jsonify({"error": "empty content"}), 400
        # Write content to a temp file with the right extension so the
        # loader can detect CSV/JSON/plain-text by suffix.
        suffix = ".csv" if source_type == "csv" else ".json" if source_type == "json" else ".txt"
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, prefix="music_import_",
        )
        tmp.write(content)
        tmp.close()

        # Parse tracks to get count upfront
        from ..loaders import load_input
        try:
            tracks = load_input(tmp.name)
        except Exception as e:
            os.unlink(tmp.name)
            return jsonify({"error": f"parse failed: {e}"}), 400
        os.unlink(tmp.name)

    if not tracks:
        return jsonify({"error": "no tracks parsed"}), 400

    # The CSV itself contains repeated artist+title rows; each duplicate
    # costs a full pipeline pass (enrich + 6 searches + downloads). Keep
    # the first occurrence only.
    seen = set()
    uniq = []
    for t in tracks:
        key = (t["artist"].lower().strip(), t["title"].lower().strip())
        if key in seen:
            continue
        seen.add(key)
        uniq.append(t)
    if len(uniq) != len(tracks):
        log.info("dedup: %d -> %d tracks", len(tracks), len(uniq))
        tracks = uniq

    total = len(tracks)
    source_desc = data.get("content", "").strip()[:60] or f"{total} tracks"
    session_id = db.create_session(
        source=f"{source_type}: {source_desc}",
        source_name=options.get("name", ""),
        total=total,
    )

    # Add placeholder records
    for t in tracks:
        db.add_track(
            session_id=session_id,
            artist=t.get("artist", ""),
            title=t.get("title", ""),
            album=t.get("album", ""),
            status="pending",
        )

    # Build config overrides
    overrides = {
        "parallel": int(options.get("parallel", 4)),
        "quality": options.get("quality", "128"),
        "enrich": str(options.get("enrich", True)).lower() == "true",
        "max_path_len": int(options.get("max_path_len", 0)),
    }
    if options.get("sources"):
        overrides["enabled_sources"] = options["sources"]

    tmp_file = locals().get("tmp", None)
    job = {
        "id": job_id,
        "type": "download",
        "title": options.get("name") or "Download",
        "session_id": session_id,
        "tmp_file": tmp_file.name if tmp_file else "",
        "tracks": tracks,
        "overrides": overrides,
        "cancelled": False,
        "done": False,
        "progress": {"ok": 0, "failed": 0, "total": total},
        "thread": None,
    }

    with _jobs_lock:
        _download_jobs[job_id] = job

    # Persist so the job survives a server restart (resumed on boot).
    db.create_job(job_id, session_id, job["title"], overrides)

    # Start in background thread
    from .app import _run_download
    t = threading.Thread(target=_run_download, args=(job_id,), daemon=True)
    t.start()
    job["thread"] = t

    return jsonify({"job_id": job_id, "session_id": session_id, "total": total})


@bp.route("/api/jobs")
def api_jobs():
    """List all background jobs (running + recent finished)."""
    with _jobs_lock:
        jobs = []
        for j in _download_jobs.values():
            jobs.append({
                "id": j.get("id", ""),
                "type": j.get("type", "task"),
                "title": j.get("title", "Task"),
                "done": j.get("done", False),
                "cancelled": j.get("cancelled", False),
                "error": j.get("error"),
                "progress": j.get("progress", {"ok": 0, "failed": 0, "total": 0}),
            })
    # Newest first
    jobs.reverse()
    running = [j for j in jobs if not j["done"]]
    return jsonify({"jobs": jobs, "running": len(running)})


@bp.route("/api/download/<job_id>")
def api_download_status(job_id: str):
    with _jobs_lock:
        job = _download_jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify({
        "done": job["done"],
        "cancelled": job["cancelled"],
        "progress": job["progress"],
    })


@bp.route("/api/download/<job_id>", methods=["DELETE"])
def api_cancel_download(job_id: str):
    with _jobs_lock:
        job = _download_jobs.get(job_id)
        if job:
            job["cancelled"] = True
    db.update_job_status(job_id, "cancelled")
    return jsonify({"ok": True})


@bp.route("/api/library/retry-failed", methods=["POST"])
def api_retry_failed():
    """Re-submit all failed tracks as a new download."""
    failed, _ = db.search_tracks(status="failed", limit=9999)
    if not failed:
        return jsonify({"job_id": None, "total": 0})
    # Pass the structured rows straight to the pipeline — a text round-trip
    # would mangle titles/albums containing " - ".
    tracks = [
        {"artist": t["artist"], "title": t["title"], "album": t["album"]}
        for t in failed
    ]

    job_id = str(uuid.uuid4())[:8]
    session_id = db.create_session(
        source=f"retry: {len(tracks)} failed tracks",
        source_name="Retry failed",
        total=len(tracks),
    )
    for t in tracks:
        db.add_track(
            session_id=session_id,
            artist=t.get("artist", ""),
            title=t.get("title", ""),
            album=t.get("album", ""),
            status="pending",
        )

    # Load current settings
    settings = db.get_all_settings()
    overrides = {
        "parallel": int(settings.get("parallel", 4)),
        "quality": settings.get("quality", "128"),
        "enrich": settings.get("enrich", "true") == "true",
        "max_path_len": int(settings.get("max_path_len", 0)),
    }
    if settings.get("sources"):
        try:
            overrides["enabled_sources"] = json.loads(settings["sources"])
        except (json.JSONDecodeError, TypeError):
            pass

    from ..config import Config
    from ..pipeline import Pipeline
    from ..sources.registry import default_sources, default_enrichers

    cfg = Config.from_env()
    cfg.merge(**overrides)
    cfg.output_dir = str(LIBRARY)

    sources = default_sources(cfg)
    enrichers = default_enrichers(cfg)
    pipe = Pipeline(cfg, sources, enrichers)

    # Wire logging to DB
    original_log = pipe._log

    def logging_log(track, status, source_name):
        artist = track.get("artist", "")
        title = track.get("title", "")
        with db.tx() as conn:
            row = conn.execute(
                "SELECT id FROM tracks WHERE session_id=? AND artist=? AND title=? AND status='pending' LIMIT 1",
                (session_id, artist, title),
            ).fetchone()
            if row:
                file_path = track.get("_file_path", "")
                file_size = 0
                if file_path and Path(file_path).exists():
                    file_size = Path(file_path).stat().st_size
                db_status = "failed"
                if status in ("ok", "cached"):
                    db_status = status
                elif status == "downloaded":
                    db_status = "ok"
                db.update_track_status(
                    row["id"],
                    status=db_status,
                    file_path=file_path,
                    file_size=file_size,
                    source_name=source_name or "",
                )
                # Keep exactly one completed record per track (see app.py).
                if db_status in ("ok", "cached"):
                    conn.execute(
                        "DELETE FROM tracks WHERE LOWER(artist)=LOWER(?) "
                        "AND LOWER(title)=LOWER(?) AND status IN ('ok','cached') "
                        "AND id != ?",
                        (artist, title, row["id"]),
                    )
                if db_status in ("ok", "cached"):
                    conn.execute(
                        "UPDATE import_sessions SET downloaded = downloaded + 1 WHERE id=?",
                        (session_id,),
                    )
                else:
                    conn.execute(
                        "UPDATE import_sessions SET failed = failed + 1 WHERE id=?",
                        (session_id,),
                    )
        original_log(track, status, source_name)

    pipe._log = logging_log

    def _run():
        try:
            pipe.process(tracks)
            db.update_session_stats(session_id)
        except Exception as e:
            log.error("retry job failed: %s", e)

    threading.Thread(target=_run, daemon=True).start()

    return jsonify({"job_id": job_id, "session_id": session_id, "total": len(tracks)})
