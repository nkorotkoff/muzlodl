"""Play history and statistics routes."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from . import db

log = logging.getLogger(__name__)

bp = Blueprint("stats", __name__)


@bp.route("/api/plays", methods=["POST"])
def api_record_play():
    """Record a listen. body: {track_id: int}"""
    data = request.get_json() or {}
    track_id = data.get("track_id")
    if not track_id:
        return jsonify({"error": "track_id required"}), 400
    if not db.get_track(track_id):
        return jsonify({"error": "track not found"}), 404
    db.record_play(track_id)
    return jsonify({"ok": True})


@bp.route("/api/stats")
def api_stats():
    """Overall listening stats + top tracks/artists."""
    return jsonify({
        "summary": db.plays_summary(),
        "top_tracks": db.top_tracks(20),
        "top_artists": db.top_artists(20),
        "recent": db.recent_plays(30),
    })
