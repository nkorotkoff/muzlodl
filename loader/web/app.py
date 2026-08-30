"""Flask app factory: auth, static pages, blueprint registration."""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import flask
from flask import Flask, jsonify, render_template, request, send_from_directory, session

from . import auth, db, security
from .core import LIBRARY, STATIC, TEMPLATES, _download_jobs, _jobs_lock, auto_import_log, auto_scan

log = logging.getLogger(__name__)


def _load_secret_key() -> str:
    """Persistent random session key (created on first run).

    A fixed dev default would let anyone forge session cookies (authed=True)
    when the UI is bound to 0.0.0.0.
    """
    env_key = os.environ.get("FLASK_SECRET", "")
    if env_key:
        return env_key
    key_file = Path(LIBRARY) / ".secret_key"
    if key_file.exists():
        return key_file.read_text().strip()
    import secrets

    key = secrets.token_hex(32)
    try:
        key_file.write_text(key, encoding="utf-8")
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
    except OSError as e:
        log.warning("could not persist secret key: %s", e)
    return key


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(STATIC), template_folder=str(TEMPLATES))
    app.secret_key = _load_secret_key()

    # Session cookie hardening: HttpOnly (default), SameSite=Lax so the
    # cookie is not sent on cross-site requests; Secure when behind TLS.
    https = os.environ.get("FLASK_FORCE_HTTPS", "") == "1"
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=https,
        PERMANENT_SESSION_LIFETIME=30 * 24 * 3600,  # 30 days
    )

    db.init_db()

    # ---- Auth ----
    # Public paths: setup wizard, login, static assets
    PUBLIC_PATHS = ("/setup", "/api/setup", "/login", "/api/login")

    @app.before_request
    def _check_auth():
        if request.path.startswith("/static/"):
            return None
        # CSRF: every state-changing request must carry a token matching
        # the csrf_token cookie (double-submit). Login/setup are included —
        # they change state too.
        if request.method in security.MUTATING_METHODS and not security.check_csrf():
            return security.csrf_error()
        if request.path in PUBLIC_PATHS:
            return None
        if not auth.setup_done():
            # First run: force admin registration before anything else
            if request.path.startswith("/api/"):
                return jsonify({"error": "setup required"}), 401
            return flask.redirect("/setup")
        if session.get("authed"):
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return flask.redirect("/login")

    @app.after_request
    def _hardening(response):
        security.apply_security_headers(response)
        security.ensure_csrf_cookie(response)
        return response

    @app.route("/setup")
    def setup_page():
        if auth.setup_done():
            return flask.redirect("/login")
        return send_from_directory(str(STATIC), "setup.html")

    @app.route("/api/setup", methods=["POST"])
    def api_setup():
        if auth.setup_done():
            return jsonify({"error": "already set up"}), 400
        if not security.login_limiter.allowed(security.client_ip()):
            return jsonify({"error": "too many attempts, try later"}), 429
        data = request.get_json() or {}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        if not username:
            return jsonify({"error": "username is required"}), 400
        if len(password) < auth.MIN_PASSWORD_LEN:
            return jsonify({"error": f"password must be at least {auth.MIN_PASSWORD_LEN} chars"}), 400
        db.set_setting("admin_username", username)
        db.set_setting("password", auth.hash_password(password))
        # Fresh session id (cookie contents) — session fixation defense.
        session.clear()
        session["authed"] = True
        security.login_limiter.reset(security.client_ip())
        return jsonify({"ok": True})

    @app.route("/login")
    def login_page():
        return send_from_directory(str(STATIC), "login.html")

    @app.route("/api/login", methods=["POST"])
    def api_login():
        if not security.login_limiter.allowed(security.client_ip()):
            return jsonify({"error": "too many attempts, try later"}), 429
        data = request.get_json() or {}
        if not auth.setup_done():
            # Setup wizard hasn't run — treat login as setup
            session.clear()
            session["authed"] = True
            security.login_limiter.reset(security.client_ip())
            return jsonify({"ok": True, "setup": True})
        username = (data.get("username") or "").strip() or auth.admin_username()
        pwd = db.get_setting("password", "")
        if username == auth.admin_username() and auth.verify_password(data.get("password") or "", pwd):
            # Fresh session contents — session fixation defense.
            session.clear()
            session["authed"] = True
            security.login_limiter.reset(security.client_ip())
            return jsonify({"ok": True})
        return jsonify({"error": "wrong username or password"}), 401

    @app.route("/api/logout", methods=["POST"])
    def api_logout():
        session.pop("authed", None)
        return jsonify({"ok": True})

    # ---- App pages (Jinja layout) ----
    @app.route("/")
    def index():
        return render_template("index.html", active="library")

    @app.route("/search")
    def search_page():
        return render_template("search.html", active="search")

    @app.route("/import")
    def import_page():
        return render_template("import.html", active="import")

    @app.route("/history")
    def history_page():
        return render_template("history.html", active="history")

    @app.route("/settings")
    def settings_page():
        return render_template("settings.html", active="settings")

    @app.route("/stats")
    def stats_page():
        return render_template("stats.html", active="stats")

    # ---- Blueprints ----
    from .routes_library import bp as library_bp
    from .routes_download import bp as download_bp
    from .routes_search import bp as search_bp
    from .routes_settings import bp as settings_bp
    from .routes_cloud import bp as cloud_bp
    from .routes_stats import bp as stats_bp

    app.register_blueprint(library_bp)
    app.register_blueprint(download_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(cloud_bp)
    app.register_blueprint(stats_bp)

    # Auto-index existing files and log on first run
    auto_scan()
    auto_import_log()

    return app


def _run_download(job_id: str) -> None:
    """Run download pipeline in background."""
    with _jobs_lock:
        job = _download_jobs.get(job_id)
    if not job:
        return

    from ..config import Config
    from ..pipeline import Pipeline
    from ..sources.registry import default_sources, default_enrichers

    cfg = Config.from_env()
    cfg.merge(**job["overrides"])
    # AcoustID verification from saved settings
    settings = db.get_all_settings()
    cfg.acoustid_api_key = settings.get("acoustid_api_key", "")
    cfg.acoustid_verify = settings.get("acoustid_verify", "false") == "true"
    try:
        cfg.acoustid_min_score = float(settings.get("acoustid_min_score", "0.5"))
    except ValueError:
        pass
    # Point output at the real library
    cfg.output_dir = str(LIBRARY)

    sources = default_sources(cfg)
    enrichers = default_enrichers(cfg)
    pipe = Pipeline(cfg, sources, enrichers)

    # Monkey-patch _log to update DB
    original_log = pipe._log

    def logging_log(track, status, source_name):
        # Find the matching DB record for this track
        artist = track.get("artist", "")
        title = track.get("title", "")
        session_id = job["session_id"]

        with db.tx() as conn:
            row = conn.execute(
                "SELECT id FROM tracks WHERE session_id=? AND artist=? AND title=? AND status='pending' LIMIT 1",
                (session_id, artist, title),
            ).fetchone()
            if row:
                abs_path = track.get("_file_path", "")
                file_size = int(track.get("_file_size", 0) or 0)
                # Store path relative to the library root
                file_path = ""
                if abs_path:
                    try:
                        file_path = str(Path(abs_path).relative_to(Path(LIBRARY)))
                    except ValueError:
                        file_path = abs_path
                if not file_size and abs_path and Path(abs_path).exists():
                    file_size = Path(abs_path).stat().st_size

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
                    duration=float(track.get("_duration") or 0),
                )
                # Dedup: each track must have exactly ONE completed record.
                # Old runs leave cached/ok rows behind; drop them so the
                # library list doesn't show the same track N times.
                if db_status in ("ok", "cached"):
                    conn.execute(
                        "DELETE FROM tracks WHERE LOWER(artist)=LOWER(?) "
                        "AND LOWER(title)=LOWER(?) AND status IN ('ok','cached') "
                        "AND id != ?",
                        (artist, title, row["id"]),
                    )
                # Live session counters: the imports page shows
                # import_sessions.downloaded/failed, which previously only
                # updated when the whole job finished — a running job looked
                # like 0% forever. Update them incrementally here.
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

        # Update progress
        with _jobs_lock:
            if job_id in _download_jobs:
                prog = _download_jobs[job_id]["progress"]
                if status in ("ok", "cached", "downloaded"):
                    prog["ok"] += 1
                elif status == "failed":
                    prog["failed"] += 1

        # Call original
        original_log(track, status, source_name)

    pipe._log = logging_log

    try:
        stats = pipe.process(job["tracks"], is_cancelled=lambda: bool(job.get("cancelled")))
        db.update_session_stats(job["session_id"])
    except Exception as e:
        log.error("download job %s failed: %s", job_id, e)
    finally:
        with _jobs_lock:
            if job_id in _download_jobs:
                _download_jobs[job_id]["done"] = True
        try:
            db.update_job_status(job_id, "done")
        except Exception as e:
            log.debug("job status update failed: %s", e)
        if job.get("tmp_file"):
            try:
                import os
                os.unlink(job["tmp_file"])
            except OSError:
                pass


def _resume_interrupted_jobs() -> None:
    """Re-create download jobs that were running when the server died.

    Each job's session holds per-track status in the DB; we re-collect the
    still-pending tracks and continue from where we left off (finished
    tracks stay ok/cached, files are not re-downloaded via skip_existing).
    """
    from .routes_download import _jobs_lock, _download_jobs  # shared registry

    for row in db.list_running_jobs():
        job_id = row["id"]
        sid = row["session_id"]
        try:
            import json as _json
            overrides = _json.loads(row.get("overrides") or "{}")
        except (ValueError, TypeError):
            overrides = {}
        with _jobs_lock:
            if job_id in _download_jobs:
                continue  # already live
        with db.tx() as conn:
            rows = conn.execute(
                "SELECT artist, title, album FROM tracks "
                "WHERE session_id=? AND status='pending' ORDER BY id",
                (sid,),
            ).fetchall()
            counts = conn.execute(
                "SELECT status, COUNT(*) c FROM tracks WHERE session_id=? GROUP BY status",
                (sid,),
            ).fetchall()
        pending = [dict(r) for r in rows]
        cnt = {r["status"]: r["c"] for r in counts}
        if not pending:
            db.update_job_status(job_id, "done")
            continue
        job = {
            "id": job_id,
            "type": "download",
            "title": row.get("title") or "Resumed download",
            "session_id": sid,
            "tmp_file": "",
            "tracks": pending,
            "overrides": overrides,
            "cancelled": False,
            "done": False,
            "progress": {
                "ok": cnt.get("ok", 0) + cnt.get("cached", 0),
                "failed": cnt.get("failed", 0),
                "total": cnt.get("ok", 0) + cnt.get("cached", 0)
                + cnt.get("failed", 0) + len(pending),
            },
            "thread": None,
        }
        with _jobs_lock:
            _download_jobs[job_id] = job
        t = threading.Thread(target=_run_download, args=(job_id,), daemon=True)
        t.start()
        job["thread"] = t
        log.info("resumed download job %s (%d pending tracks)", job_id, len(pending))


# Background threads (started once at import)
from .core import cleanup_old_previews, library_watcher

_thread = threading.Thread(target=cleanup_old_previews, daemon=True)
_thread.start()
_sync_thread = threading.Thread(target=library_watcher, daemon=True)
_sync_thread.start()
# Resume jobs that were running before the previous server died.
_resume_thread = threading.Thread(target=_resume_interrupted_jobs, daemon=True)
_resume_thread.start()
