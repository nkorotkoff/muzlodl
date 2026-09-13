"""Cloud storage routes: status, config, upload."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from ..credentials import load_cloud
from ..storage import make_storage

from . import db

log = logging.getLogger(__name__)

bp = Blueprint("cloud", __name__)


@bp.route("/api/cloud/status")
def api_cloud_status():
    config = load_cloud()
    if not config:
        return jsonify({"configured": False})
    try:
        storage = make_storage(config)
        # Reachable if we can make a client without error;
        # telegram also gets a live check (getMe) on save, so here
        # a client instance is enough for the status dot.
        reachable = bool(storage.client is not None)
        return jsonify({
            "configured": True,
            "backend": config.backend,
            "login": config.login,
            "root": config.root,
            "reachable": reachable,
            "sync_mode": db.get_all_settings().get("cloud_sync_mode", "manual"),
        })
    except Exception as e:
        return jsonify({
            "configured": True,
            "backend": config.backend,
            "login": config.login,
            "root": config.root,
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

    # telegram: bot token (password) + channel id (login), validate via Bot API
    if backend == "telegram":
        from ..telegram import TelegramClient, TelegramError
        chat_id = login.strip()
        if not chat_id:
            return jsonify({"error": "channel id required (e.g. -1001234567890)"}), 400
        try:
            client = TelegramClient(password, chat_id)
            info = client.check_access()
        except TelegramError as e:
            return jsonify({"error": str(e)}), 400
        from ..credentials import save_cloud
        from ..storage import CloudConfig
        save_cloud(CloudConfig(backend="telegram", login=chat_id, password=password, root=root))
        return jsonify({"ok": True, "backend": "telegram",
                        "root": root, "bot": info.get("username", "")})

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

    # Optional ?mode=auto|manual — persists the sync mode for future
    # downloads (auto = sync after every download job, manual = button only).
    # dry=true only saves the mode without queueing (mode switcher).
    data = request.get_json(silent=True) or {}
    mode = (request.args.get("mode") or data.get("mode") or "").strip()
    if mode in ("auto", "manual"):
        db.set_setting("cloud_sync_mode", mode)
    if data.get("dry"):
        return jsonify({"ok": True, "sync_mode": mode})

    # Manual upload = same per-file queue as the watcher: scan for
    # unsynced files, create one small job per file under one group id.
    # The ☁ button still persists ?mode= for future auto passes.
    # No placeholder job: per-file jobs share the group id, and the
    # panel shows a single "Sync N files" aggregate for the group.
    from . import sync_queue
    pending = sync_queue.scan_unsynced()
    if not pending:
        return jsonify({"job_id": None, "total": 0, "message": "everything synced"})

    try:
        gid = sync_queue.enqueue_files(pending)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"job_id": None, "group_id": gid, "total": len(pending)})


@bp.route("/api/cloud/sync/status")
def api_sync_status():
    """Aggregate sync progress: done/total across the current group."""
    from . import sync_queue
    return jsonify(sync_queue.sync_status())
