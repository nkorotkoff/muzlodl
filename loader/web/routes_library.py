"""Library routes: track CRUD, stream, batch, export, re-encode, upload."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import uuid
from pathlib import Path

import flask
from flask import Blueprint, jsonify, request

from . import db
from .core import LIBRARY, _download_jobs, _jobs_lock, update_durations

log = logging.getLogger(__name__)

bp = Blueprint("library", __name__)


@bp.route("/api/library")
def api_library():
    query = request.args.get("q", "")
    artist = request.args.get("artist", "")
    album = request.args.get("album", "")
    status = request.args.get("status", "")
    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))
    sort = request.args.get("sort", "artist")
    order = request.args.get("order", "asc")
    # Library is for listening: only show tracks that have a file on disk
    tracks, total = db.search_tracks(
        query, artist, album, status, limit, offset, sort,
        order=order, only_downloaded=True, dedup=True,
    )
    return jsonify({"tracks": tracks, "total": total, "offset": offset, "limit": limit})


@bp.route("/api/library/<int:track_id>/stream")
def api_stream_track(track_id: int):
    """Stream an audio file (inline, for the player)."""
    track = db.get_track(track_id)
    if not track:
        return jsonify({"error": "not found"}), 404
    lib = Path(LIBRARY).resolve()
    file_path = (lib / track["file_path"]).resolve()
    # Defense in depth: DB paths must stay inside the library. Absolute
    # or ../-relative values (e.g. from older bugs) must not escape.
    if not file_path.is_relative_to(lib) or not file_path.exists():
        return jsonify({"error": "file not found"}), 404
    mime = "audio/ogg" if file_path.suffix == ".opus" else "audio/mpeg"
    return flask.send_file(
        str(file_path),
        mimetype=mime,
        as_attachment=False,
        conditional=True,  # enables Range requests for seeking
    )


@bp.route("/api/library/<int:track_id>/download")
def api_download_track(track_id: int):
    """Download an audio file (attachment)."""
    track = db.get_track(track_id)
    if not track:
        return jsonify({"error": "not found"}), 404
    lib = Path(LIBRARY).resolve()
    file_path = (lib / track["file_path"]).resolve()
    # Defense in depth: DB paths must stay inside the library.
    if not file_path.is_relative_to(lib) or not file_path.exists():
        return jsonify({"error": "file not found"}), 404
    mime = "audio/ogg" if file_path.suffix == ".opus" else "audio/mpeg"
    return flask.send_file(
        str(file_path),
        mimetype=mime,
        as_attachment=True,
        download_name=file_path.name,
    )


@bp.route("/api/library/<int:track_id>")
def api_track(track_id: int):
    track = db.get_track(track_id)
    if not track:
        return jsonify({"error": "not found"}), 404
    return jsonify(track)


@bp.route("/api/library/<int:track_id>", methods=["DELETE"])
def api_delete_track(track_id: int):
    ok = db.delete_track(track_id)
    return jsonify({"ok": ok})


@bp.route("/api/library/batch-delete", methods=["POST"])
def api_batch_delete():
    data = request.get_json() or {}
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"error": "no ids"}), 400
    removed = db.delete_tracks([int(i) for i in ids])
    return jsonify({"removed": removed})


@bp.route("/api/library/<int:track_id>", methods=["PATCH"])
def api_update_track(track_id: int):
    data = request.get_json() or {}
    artist = data.get("artist", "")
    title = data.get("title", "")
    album = data.get("album", "")
    ok, msg = db.update_track_metadata(track_id, artist, title, album)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"ok": True, "message": msg})


@bp.route("/api/library/export")
def api_export():
    """Export library as CSV or JSON."""
    fmt = request.args.get("format", "csv")
    tracks, _ = db.search_tracks(limit=99999, only_downloaded=True)
    if fmt == "json":
        payload = [
            {"artist": t["artist"], "title": t["title"], "album": t["album"],
             "duration": round(t.get("duration") or 0), "source": t["source_name"]}
            for t in tracks
        ]
        return flask.Response(
            json.dumps(payload, ensure_ascii=False, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=library.json"},
        )
    # CSV
    import csv as _csv, io
    buf = io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["artist", "title", "album", "duration", "source"])
    for t in tracks:
        w.writerow([t["artist"], t["title"], t["album"],
                    int(t.get("duration") or 0), t["source_name"]])
    return flask.Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=library.csv"},
    )


@bp.route("/api/library/reencode", methods=["POST"])
def api_reencode():
    """Re-encode all library files to a target Opus bitrate."""
    data = request.get_json() or {}
    try:
        bitrate = int(data.get("bitrate", 64))
    except (TypeError, ValueError):
        return jsonify({"error": "bitrate must be a number"}), 400
    if not 16 <= bitrate <= 512:
        return jsonify({"error": "bitrate must be between 16 and 512 kbps"}), 400
    job_id = str(uuid.uuid4())[:8]

    def _run(jid: str) -> None:
        lib = Path(LIBRARY)
        files = sorted(
            f for f in lib.rglob("*") if f.is_file() and f.suffix.lower() in (".opus", ".mp3")
        )
        total = len(files)
        done = 0
        failed = 0

        def _update(extra=None):
            with _jobs_lock:
                if jid in _download_jobs:
                    _download_jobs[jid].update({
                        "progress": {"ok": done, "failed": failed, "total": total},
                    })
                    if extra:
                        _download_jobs[jid].update(extra)

        with _jobs_lock:
            _download_jobs[jid] = {
                "id": jid, "type": "reencode",
                "title": f"Re-encode to {bitrate} kbps",
                "done": False, "cancelled": False,
                "progress": {"ok": 0, "failed": 0, "total": total},
            }

        for f in files:
            tmp = f.with_suffix(".tmp.opus")
            try:
                r = subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-i", str(f),
                     "-c:a", "libopus", "-b:a", f"{bitrate}k", "-vbr", "on",
                     "-application", "audio", str(tmp)],
                    capture_output=True, text=True, timeout=600,
                )
                if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                    # Content is now Opus — the name must match, or the
                    # browser serves it as MP3 and playback breaks.
                    out = f.with_suffix(".opus")
                    if out != f:
                        if out.exists():
                            out.unlink()
                        tmp.replace(out)
                        # Update any DB rows pointing at the old name.
                        old_rel = str(f.relative_to(lib))
                        new_rel = str(out.relative_to(lib))
                        with db.tx() as conn:
                            conn.execute(
                                "UPDATE tracks SET file_path=? WHERE file_path=?",
                                (new_rel, old_rel),
                            )
                        try:
                            f.unlink()
                        except OSError:
                            pass
                    else:
                        tmp.replace(f)
                    done += 1
                else:
                    failed += 1
                    if tmp.exists():
                        tmp.unlink()
            except Exception as e:
                failed += 1
                log.error("reencode failed %s: %s", f.name, e)
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
            _update()

        # Refresh durations since files changed
        update_durations()
        _update({"done": True})

    threading.Thread(target=_run, args=(job_id,), daemon=True).start()
    return jsonify({"job_id": job_id})


@bp.route("/api/library/<int:track_id>/retry", methods=["POST"])
def api_retry_track(track_id: int):
    """Re-download a single failed track."""
    track = db.get_track(track_id)
    if not track:
        return jsonify({"error": "not found"}), 404
    if track["status"] == "ok":
        return jsonify({"error": "track already downloaded"}), 400

    artist = track["artist"]
    title = track["title"]
    album = track["album"]

    job_id = str(uuid.uuid4())[:8]
    # Pass metadata directly — no text-line parsing, so titles/albums
    # containing " - " stay intact.
    tracks = [{"artist": artist, "title": title, "album": album}]
    session_id = db.create_session(
        source=f"retry: {artist} - {title}", source_name="Retry track", total=1,
    )
    for t in tracks:
        db.add_track(
            session_id=session_id, artist=t.get("artist", ""),
            title=t.get("title", ""), album=t.get("album", ""),
            status="pending",
        )

    settings = db.get_all_settings()
    overrides = {
        "parallel": 1,
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

    original_log = pipe._log

    def logging_log(track, status, source_name):
        a = track.get("artist", "")
        t = track.get("title", "")
        with db.tx() as conn:
            row = conn.execute(
                "SELECT id FROM tracks WHERE session_id=? AND artist=? AND title=? AND status='pending' LIMIT 1",
                (session_id, a, t),
            ).fetchone()
            if row:
                fp = track.get("_file_path", "")
                size = 0
                if fp and Path(fp).exists():
                    size = Path(fp).stat().st_size
                db_status = "failed"
                if status in ("ok", "cached"):
                    db_status = status
                elif status == "downloaded":
                    db_status = "ok"
                db.update_track_status(
                    row["id"], status=db_status, file_path=fp,
                    file_size=size, source_name=source_name or "",
                )
        original_log(track, status, source_name)

    pipe._log = logging_log

    def _run():
        try:
            pipe.process(tracks)
            db.update_session_stats(session_id)
        except Exception as e:
            log.error("retry single failed: %s", e)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id, "total": len(tracks)})


@bp.route("/api/library/<int:track_id>/reload", methods=["POST"])
def api_reload_track(track_id: int):
    """Re-download an already-downloaded track from the source chain.

    Unlike /retry (which only handles failed tracks), this replaces the
    existing file with a fresh download. The original track row is updated
    in place so no duplicate appears in the library.
    """
    track = db.get_track(track_id)
    if not track:
        return jsonify({"error": "not found"}), 404
    if not track["file_path"]:
        return jsonify({"error": "track has no file"}), 400

    artist = track["artist"]
    title = track["title"]
    album = track["album"]

    job_id = str(uuid.uuid4())[:8]
    # Pass metadata directly — no text-line parsing, so titles/albums
    # containing " - " stay intact.
    tracks = [{"artist": artist, "title": title, "album": album}]
    session_id = db.create_session(
        source=f"reload: {artist} - {title}", source_name="Reload track", total=1,
    )
    for t in tracks:
        db.add_track(
            session_id=session_id, artist=t.get("artist", ""),
            title=t.get("title", ""), album=t.get("album", ""),
            status="pending",
        )

    settings = db.get_all_settings()
    overrides = {
        "parallel": 1,
        "quality": settings.get("quality", "128"),
        "enrich": settings.get("enrich", "true") == "true",
        "max_path_len": int(settings.get("max_path_len", 0)),
        # Do not short-circuit on the file already existing — we are
        # explicitly re-downloading it from another source.
        "skip_existing": False,
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

    original_log = pipe._log

    # The pipeline downloads into the same artist/album/title path, which is
    # exactly the file this track already owns. Keep a backup so a failed
    # re-download can't leave a truncated file in place of a good one.
    old_rel = track["file_path"]
    old_path = Path(LIBRARY) / old_rel
    backup = old_path.with_suffix(old_path.suffix + ".bak")

    def logging_log(t, status, source_name):
        a = t.get("artist", "")
        tt = t.get("title", "")
        with db.tx() as conn:
            row = conn.execute(
                "SELECT id FROM tracks WHERE session_id=? AND artist=? AND title=? AND status='pending' LIMIT 1",
                (session_id, a, tt),
            ).fetchone()
            if row:
                fp = t.get("_file_path", "")
                size = 0
                if fp and Path(fp).exists():
                    size = Path(fp).stat().st_size
                db_status = "failed"
                if status in ("ok", "cached"):
                    db_status = status
                elif status == "downloaded":
                    db_status = "ok"
                db.update_track_status(
                    row["id"], status=db_status, file_path=fp,
                    file_size=size, source_name=source_name or "",
                )
        with _jobs_lock:
            if job_id in _download_jobs:
                _download_jobs[job_id]["progress"] = {
                    "ok": 1 if status in ("ok", "cached") else 0,
                    "failed": 1 if status == "failed" else 0,
                    "total": 1,
                }
        original_log(t, status, source_name)

    pipe._log = logging_log

    def _run():
        ok = False
        try:
            # Move the current file aside so the pipeline writes fresh.
            if old_path.exists():
                if backup.exists():
                    backup.unlink()
                old_path.rename(backup)

            pipe.process(tracks)

            # Session stats must be computed while the pending row still
            # exists (it is deleted below after being folded into the
            # original track).
            db.update_session_stats(session_id)

            # Propagate the fresh download onto the original track row.
            with db.tx() as conn:
                row = conn.execute(
                    "SELECT id, file_path, file_size, source_name FROM tracks "
                    "WHERE session_id=? AND status IN ('ok','cached') LIMIT 1",
                    (session_id,),
                ).fetchone()
            if row:
                # Pipeline stores absolute paths; DB convention is relative.
                try:
                    rel_path = str(Path(row["file_path"]).relative_to(Path(LIBRARY)))
                except ValueError:
                    rel_path = row["file_path"]
                db.update_track_status(
                    track_id, status="ok",
                    file_path=rel_path,
                    file_size=row["file_size"],
                    source_name=row["source_name"] or "",
                )
                # Drop the temporary pending row — it would duplicate the track.
                with db.tx() as conn:
                    conn.execute("DELETE FROM tracks WHERE id=?", (row["id"],))
                ok = True
                # Refresh duration — the new file may differ. Probe only
                # this track, not the whole library (1416 ffprobe runs).
                from .models.track import _probe_duration
                dur = _probe_duration(Path(LIBRARY) / rel_path)
                if dur:
                    with db.tx() as conn:
                        conn.execute(
                            "UPDATE tracks SET duration=? WHERE id=?",
                            (dur, track_id),
                        )
        except Exception as e:
            log.error("reload single failed: %s", e)
        finally:
            # Restore the original file if the re-download failed.
            if backup.exists():
                try:
                    if ok:
                        backup.unlink()
                    else:
                        if old_path.exists():
                            old_path.unlink()
                        backup.rename(old_path)
                except OSError as e:
                    log.warning("reload backup restore failed: %s", e)
            with _jobs_lock:
                if job_id in _download_jobs:
                    _download_jobs[job_id].update({"done": True})

    threading.Thread(target=_run, daemon=True).start()

    with _jobs_lock:
        _download_jobs[job_id] = {
            "id": job_id,
            "type": "reload",
            "title": f"Reload: {artist} - {title}",
            "done": False,
            "cancelled": False,
            "progress": {"ok": 0, "failed": 0, "total": 1},
        }

    return jsonify({"job_id": job_id, "total": 1})


@bp.route("/api/library/upload", methods=["POST"])
def api_upload_track():
    """Upload an audio file from the browser into the library."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "no file"}), 400

    artist = request.form.get("artist", "").strip()
    title = request.form.get("title", "").strip()
    album = request.form.get("album", "").strip()

    if not title:
        stem = Path(f.filename).stem
        title = stem.split(" - ", 1)[1] if " - " in stem else stem
    if not artist and " - " in Path(f.filename).stem:
        artist = Path(f.filename).stem.split(" - ", 1)[0]
    if not artist:
        artist = "Unknown Artist"
    if not album:
        album = "Singles"

    from ..pipeline import sanitize
    safe_artist = sanitize(artist) or "Unknown Artist"
    safe_album = sanitize(album) or "Singles"
    safe_title = sanitize(title, max_len=100)

    suffix = Path(f.filename).suffix.lower() or ".opus"
    if suffix not in (".opus", ".mp3", ".m4a", ".ogg", ".webm", ".flac"):
        return jsonify({"error": f"unsupported audio format: {suffix}"}), 400

    dst_dir = Path(LIBRARY) / safe_artist / safe_album
    dst_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_artist} - {safe_title}{suffix}" if safe_artist != "Unknown Artist" else f"{safe_title}{suffix}"
    dst = dst_dir / filename

    if dst.exists():
        base = dst.stem
        i = 1
        while dst.exists():
            dst = dst_dir / f"{base} ({i}){suffix}"
            i += 1

    f.save(str(dst))
    rel = str(dst.relative_to(Path(LIBRARY)))

    session_id = db.create_session(
        source="upload",
        source_name=f"Manual upload: {artist}",
        total=1,
    )
    db.add_track(
        session_id=session_id,
        artist=artist, title=title, album=album,
        file_path=rel, file_size=dst.stat().st_size,
        status="ok", source_name="upload",
    )
    return jsonify({"ok": True, "path": rel})


@bp.route("/api/library/scan", methods=["POST"])
def api_scan_library():
    added = db.scan_library(Path(LIBRARY))
    return jsonify({"added": added})


@bp.route("/api/library/import-log", methods=["POST"])
def api_import_log():
    log_path = Path(LIBRARY) / ".loader.log.jsonl"
    result = db.import_log(log_path)
    return jsonify(result)


@bp.route("/api/disk")
def api_disk():
    lib = Path(LIBRARY)
    total_files = sum(1 for f in lib.rglob("*") if f.is_file() and f.suffix in (".opus", ".mp3"))
    total_bytes = sum(f.stat().st_size for f in lib.rglob("*")
                      if f.is_file() and f.suffix in (".opus", ".mp3"))
    from .core import fmt_size
    return jsonify({
        "files": total_files,
        "size_bytes": total_bytes,
        "size_human": fmt_size(total_bytes),
        "artists": len([d for d in lib.iterdir() if d.is_dir() and not d.name.startswith(".")]),
    })
