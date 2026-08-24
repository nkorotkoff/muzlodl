"""Settings and doctor routes."""
from __future__ import annotations

import json
import logging

from flask import Blueprint, jsonify, request

from . import auth, db

log = logging.getLogger(__name__)

bp = Blueprint("settings", __name__)


@bp.route("/api/settings")
def api_get_settings():
    settings = db.get_all_settings()
    # Never expose the password hash; username is the account identity.
    settings.pop("password", None)
    settings["admin_username"] = auth.admin_username()
    return jsonify(settings)


@bp.route("/api/settings", methods=["PUT"])
def api_set_settings():
    data = request.get_json()
    if not data:
        return jsonify({"error": "no JSON body"}), 400
    # Password is managed separately: non-empty means "change it", and it is
    # stored hashed. Username is a distinct setting.
    pwd = data.pop("password", "")
    if pwd:
        if len(pwd) < auth.MIN_PASSWORD_LEN:
            return jsonify({"error": f"password must be at least {auth.MIN_PASSWORD_LEN} chars"}), 400
        db.set_setting("password", auth.hash_password(pwd))
    if data.get("admin_username"):
        username = str(data["admin_username"]).strip()
        if not username:
            return jsonify({"error": "username cannot be empty"}), 400
        db.set_setting("admin_username", username)
        data.pop("admin_username")
    db.set_all_settings(data)
    return jsonify({"ok": True})


@bp.route("/api/doctor")
def api_doctor():
    """Run source health check (doctor). Only tests sources in the
    current chain (plus itunes, used as metadata enricher).
    """
    from ..config import Config
    from ..doctor import run_doctor

    cfg = Config.from_env()
    settings = db.get_all_settings()
    enabled = []
    if settings.get("sources"):
        try:
            enabled = json.loads(settings["sources"])
        except (json.JSONDecodeError, TypeError):
            enabled = []
    if not enabled:
        enabled = list(cfg.enabled_sources)
    # Always include itunes — it's the metadata enricher, not an audio source
    test_names = list(dict.fromkeys([*enabled, "itunes"]))

    results = run_doctor(cfg, only=test_names)
    data = []
    for name, h in sorted(results.items()):
        data.append({
            "name": name,
            "available": h.available,
            "can_download": h.can_download,
            "latency_ms": h.latency_ms,
            "status": h.status,
            "reason": h.reason or "",
        })
    return jsonify({"sources": data})
