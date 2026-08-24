"""Cloud storage routes: status, config, upload."""
from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

from ..credentials import load_cloud
from ..storage import make_storage

from . import db
from .core import LIBRARY, _download_jobs, _jobs_lock, count_albums

log = logging.getLogger(__name__)

bp = Blueprint("cloud", __name__)


@bp.route("/api/cloud/status")
def api_cloud_status():
    config = load_cloud()
    if not config:
        return jsonify({"configured": False})
    try:
        storage = make_storage(config)
        # Reachable if we can make a client without error
        reachable = bool(storage.client is not None)
        return jsonify({
            "configured": True,
            "backend": config.backend,
            "reachable": reachable,
            "root": config.root,
        })
    except Exception as e:
        return jsonify({
            "configured": True,
            "backend": config.backend,
            "reachable": False,
            "error": str(e),
        })


@bp.route("/api/cloud/config", methods=["GET", "POST", "DELETE"])
def api_cloud_config():
    if request.method == "GET":
        config = load_cloud()
        if not config:
            return jsonify({"configured": False})
        return jsonify({
            "configured": True,
            "backend": config.backend,
            "login": config.login,
            "root": config.root,
        })

    if request.method == "DELETE":
        from ..credentials import clear_cloud
        clear_cloud()
        return jsonify({"ok": True})

    # POST: save config
    data = request.get_json()
    if not data:
        return jsonify({"error": "no JSON body"}), 400

    backend = data.get("backend", "yandex")
    login = data.get("login", "")
    password = data.get("password", "")
    root = data.get("root", "music")

    if not password:
        return jsonify({"error": "password/token required"}), 400

    # yandex_rest: OAuth token, validate via the REST API
    if backend == "yandex_rest":
        from ..yandex_oauth import test_token
        if not test_token(password):
            return jsonify({"error": "token doesn't work (check scopes)"}), 400
        from ..credentials import save_cloud
        from ..storage import CloudConfig
        save_cloud(CloudConfig(backend="yandex_rest", login="", password=password, root=root))
        return jsonify({"ok": True, "backend": "yandex_rest", "root": root})

    # Test connection before saving (WebDAV backends)
    from ..webdav import WebDAVClient, WebDAVError
    backends = {
        "yandex": "https://webdav.yandex.ru",
        "mailru": "https://webdav.cloud.mail.ru",
    }
    endpoint = backends.get(backend)
    if not endpoint:
        return jsonify({"error": f"unknown backend: {backend}"}), 400

    try:
        client = WebDAVClient(endpoint, login, password)
        if not client.exists("/"):
            return jsonify({"error": "cannot connect — check login and password"}), 400
        client.mkdir(root)
    except WebDAVError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    from ..credentials import save_cloud
    from ..storage import CloudConfig
    config = CloudConfig(backend=backend, login=login, password=password, root=root)
    save_cloud(config)
    return jsonify({"ok": True, "backend": backend, "root": root})


@bp.route("/api/cloud/upload", methods=["POST"])
def api_cloud_upload():
    config = load_cloud()
    if not config:
        return jsonify({"error": "cloud not configured"}), 400

    job_id = str(uuid.uuid4())[:8]

    def _run(jid: str) -> None:
        try:
            storage = make_storage(config)
            total_albums = count_albums()
            with _jobs_lock:
                if jid in _download_jobs:
                    _download_jobs[jid]["progress"]["total"] = total_albums

            def _cb(done, total):
                with _jobs_lock:
                    if jid in _download_jobs:
                        _download_jobs[jid]["progress"]["ok"] = done
                        _download_jobs[jid]["progress"]["total"] = total

            storage.upload_library(Path(LIBRARY), max_workers=4, progress_cb=_cb)
            with _jobs_lock:
                if jid in _download_jobs:
                    _download_jobs[jid]["done"] = True
                    _download_jobs[jid]["progress"]["ok"] = total_albums
        except Exception as e:
            with _jobs_lock:
                if jid in _download_jobs:
                    _download_jobs[jid]["done"] = True
                    _download_jobs[jid]["error"] = str(e)

    with _jobs_lock:
        _download_jobs[job_id] = {
            "id": job_id,
            "type": "cloud_upload",
            "title": "Cloud upload",
            "done": False,
            "cancelled": False,
            "progress": {"ok": 0, "failed": 0, "total": 1},
            "error": None,
        }

    threading.Thread(target=_run, args=(job_id,), daemon=True).start()

    return jsonify({"job_id": job_id})
