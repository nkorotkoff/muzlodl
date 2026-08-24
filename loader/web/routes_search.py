"""Search and preview routes."""
from __future__ import annotations

import json
import logging
import tempfile
import threading
import uuid
from pathlib import Path

import flask
from flask import Blueprint, jsonify, request

from . import db
from .core import (
    _preview_lock, _preview_store, schedule_preview_cleanup,
)

log = logging.getLogger(__name__)

bp = Blueprint("search", __name__)


@bp.route("/api/search")
def api_search():
    """Search across enabled sources."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "query required"}), 400

    from ..config import Config
    from ..sources.registry import default_sources

    # Parse query: "Artist - Title" or "Artist - Title - Album"
    artist = ""
    title = q
    album = ""
    for sep in [" — ", " – ", " - ", " — ", " – "]:
        parts = q.split(sep, 2)
        if len(parts) == 3:
            artist, title, album = [p.strip() for p in parts]
            break
        if len(parts) == 2:
            artist, title = [p.strip() for p in parts]
            break

    cfg = Config.from_env()
    # Load settings from DB
    settings = db.get_all_settings()
    if settings.get("sources"):
        try:
            cfg.enabled_sources = json.loads(settings["sources"])
        except (json.JSONDecodeError, TypeError):
            pass

    sources = default_sources(cfg)
    results = []
    sources_searched = 0

    for src in sources:
        try:
            count = 0
            for info in src.search_iter(artist, title, album):
                if count >= 3:  # max 3 per source
                    break
                score = info.match_score or 0.0
                if score < 0.3:
                    continue
                results.append({
                    "artist": info.artist or artist,
                    "title": info.title or title,
                    "album": info.album or album,
                    "source": src.name,
                    "match_score": round(score, 3),
                    "duration": info.duration,
                    "year": info.year,
                    "url": info.url or "",
                })
                count += 1
            sources_searched += 1
        except Exception as e:
            log.debug("search error on %s: %s", src.name, e)
            continue

    # Sort by score descending
    results.sort(key=lambda r: r.get("match_score", 0), reverse=True)

    return jsonify({
        "results": results[:30],
        "sources_searched": sources_searched,
        "query": {"artist": artist, "title": title, "album": album},
    })


@bp.route("/api/preview", methods=["POST"])
def api_preview_start():
    """Start downloading a source URL to a cached temp file.
    Returns {"job_id": "...", "stream_url": "/api/preview/.../stream"}.
    """
    data = request.get_json() or {}
    url = data.get("url", "")
    if not url:
        return jsonify({"error": "url required"}), 400

    job_id = str(uuid.uuid4())[:12]
    tmp = tempfile.NamedTemporaryFile(suffix=".opus", delete=False)
    tmp_path = tmp.name
    tmp.close()

    with _preview_lock:
        _preview_store[job_id] = {
            "path": tmp_path,
            "ready": False,
            "mime": "audio/ogg",
            "error": None,
        }

    def _download():
        try:
            from ..sources.ytdlp_based import YouTubeSource
            from ..sources.base import TrackInfo
            from ..config import Config

            cfg = Config.from_env()
            src = YouTubeSource(cfg.quality)
            info = TrackInfo(
                source="youtube", url=url,
                artist="", title="", album="",
            )
            ok = src.download(info, Path(tmp_path))
            if not ok or not Path(tmp_path).exists() or Path(tmp_path).stat().st_size == 0:
                raise RuntimeError("download returned empty file")

            actual = Path(tmp_path)
            mime = "audio/ogg" if actual.suffix == ".opus" else "audio/mpeg"
            with _preview_lock:
                _preview_store[job_id]["ready"] = True
                _preview_store[job_id]["mime"] = mime
                _preview_store[job_id]["path"] = str(actual)
            schedule_preview_cleanup(actual)
        except Exception as e:
            with _preview_lock:
                _preview_store[job_id]["error"] = str(e)
                _preview_store[job_id]["ready"] = True

    threading.Thread(target=_download, daemon=True).start()

    return jsonify({
        "job_id": job_id,
        "stream_url": f"/api/preview/{job_id}/stream",
    })


@bp.route("/api/preview/<job_id>/status")
def api_preview_status(job_id: str):
    with _preview_lock:
        entry = _preview_store.get(job_id)
    if not entry:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "ready": entry["ready"],
        "error": entry.get("error"),
    })


@bp.route("/api/preview/<job_id>/stream")
def api_preview_stream(job_id: str):
    with _preview_lock:
        entry = _preview_store.get(job_id)
    if not entry:
        return jsonify({"error": "not found"}), 404
    if entry.get("error"):
        return jsonify({"error": entry["error"]}), 500
    path = entry.get("path", "")
    if not path or not Path(path).exists():
        return jsonify({"error": "file not ready or expired"}), 404
    return flask.send_file(
        path,
        mimetype=entry.get("mime", "audio/ogg"),
        as_attachment=False,
        conditional=True,
    )
