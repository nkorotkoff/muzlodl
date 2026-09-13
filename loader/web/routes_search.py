"""Search and preview routes."""
from __future__ import annotations

import json
import logging
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import flask
from flask import Blueprint, jsonify, request

from . import db
from .core import (
    _preview_lock, _preview_store, schedule_preview_cleanup,
)

log = logging.getLogger(__name__)

bp = Blueprint("search", __name__)


def _prefixed(*parts: str) -> str:
    """stream_url relative to the app root (APP_PREFIX-aware)."""
    from flask import current_app

    base = (current_app.config.get("APP_PREFIX") or "").rstrip("/")
    return base + "/" + "/".join(p.strip("/") for p in parts)


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
    # Load settings from DB — auto (default) keeps canonical chain
    settings = db.get_all_settings()
    if settings.get("sources_auto", "true") != "true" and settings.get("sources"):
        try:
            cfg.enabled_sources = json.loads(settings["sources"])
        except (json.JSONDecodeError, TypeError):
            pass

    sources = default_sources(cfg)
    results = []

    def _search_one(src):
        out = []
        try:
            count = 0
            for info in src.search_iter(artist, title, album):
                if count >= 3:
                    break
                score = info.match_score or 0.0
                if score < 0.3:
                    continue
                # sleymp3 keeps its playable URL in extra.audio_data and
                # needs a server-side vkparser resolve for preview/play.
                # raw_* in extra are the site's canonical names (without
                # score heuristics truncation).
                extra = getattr(info, "extra", None) or {}
                out.append({
                    "artist": info.artist or artist,
                    "title": info.title or title,
                    "album": info.album or album,
                    "source": src.name,
                    "match_score": round(score, 3),
                    "duration": info.duration,
                    "year": info.year,
                    "url": info.url or "",
                    "audio_data": extra.get("audio_data") or "",
                    "raw_title": extra.get("raw_title") or info.title or "",
                    "raw_artist": extra.get("raw_artist") or info.artist or "",
                })
                count += 1
        except Exception as e:
            log.debug("search error on %s: %s", src.name, e)
        return out

    # Parallelize across 7 sources: was sequential ~8s (youtube 1.2s +
    # soundcloud 2.6s + mp3party 1.1s + archiveorg 2.8s), now bounded by
    # the slowest one (~2.5s).
    with ThreadPoolExecutor(max_workers=len(sources) or 1) as pool:
        futs = {pool.submit(_search_one, s): s for s in sources}
        for f in as_completed(futs):
            results.extend(f.result())
    sources_searched = len(sources)

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
    url = data.get("url", "") or ""
    audio_data = (data.get("audio_data") or "").strip()
    raw_title = (data.get("raw_title") or "").strip()
    raw_artist = (data.get("raw_artist") or "").strip()
    # sleymp3 search rows have url="" until vkparser resolves the id
    if not url and audio_data:
        try:
            from ..sources.sleymp3 import Sleymp3Source
            from ..sources.base import TrackInfo as _TI
            _src = Sleymp3Source()
            _ti = _TI(source="sleymp3", url="", artist=raw_artist, title=raw_title,
                      album="", extra={"audio_data": audio_data, "raw_title": raw_title, "raw_artist": raw_artist})
            url = _src._resolve_url(audio_data, _ti) or ""
        except Exception as e:
            log.debug("[preview] sleymp3 resolve %s: %s", audio_data, e)
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
            actual = Path(tmp_path)
            # Direct MP3/opus links (sleymp3, mp3party, lightaudio, …) are
            # already playable — just fetch them with requests. yt-dlp is
            # only needed for youtube/soundcloud/etc.
            is_direct = url.startswith("http") and any(
                k in url.lower() for k in (".mp3", ".opus", ".m4a", ".ogg", "storage.yandex.net", "dl2.mp3party", "get-mp3")
            )
            ok = False
            if is_direct:
                import requests as _rq
                h = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0"}
                with _rq.get(url, stream=True, timeout=30, headers=h) as r:
                    r.raise_for_status()
                    actual.write_bytes(b"")  # ensure exists
                    with open(actual, "wb") as f:
                        for chunk in r.iter_content(8192):
                            f.write(chunk)
                ok = actual.exists() and actual.stat().st_size > 0
            else:
                from ..sources.ytdlp_based import YouTubeSource
                from ..sources.base import TrackInfo
                from ..config import Config
                cfg = Config.from_env()
                src = YouTubeSource(cfg.quality)
                info = TrackInfo(source="youtube", url=url, artist="", title="", album="")
                ok = src.download(info, actual)
            if not ok or not actual.exists() or actual.stat().st_size == 0:
                raise RuntimeError("download returned empty file")

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
        # stream_url is built from the request path so it works
        # both directly (:8080/...) and behind a prefix (/music/...).
        "stream_url": _prefixed("/api/preview", job_id, "stream"),
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
