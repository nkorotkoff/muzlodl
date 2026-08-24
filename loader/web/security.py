"""Web security: CSRF protection, login rate limiting, security headers.

The UI may be exposed to the internet, so mutating API calls are
protected with a double-submit CSRF token (cookie + X-CSRF-Token
header), login/setup endpoints are rate-limited per IP, and responses
carry hardening headers.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time

from flask import jsonify, make_response, request

log = logging.getLogger(__name__)

CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"

#: Methods that change state and therefore require a CSRF token.
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: Login/setup rate limit: at most MAX_ATTEMPTS per IP per WINDOW seconds.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 900  # 15 min

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    # Scripts/styles use inline handlers (onclick etc.), so 'unsafe-inline'
    # is required; everything else stays locked to the origin.
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "media-src 'self' blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    ),
}


# ---------------------------------------------------------------------------
# CSRF (double-submit cookie)
# ---------------------------------------------------------------------------

def _new_token() -> str:
    return secrets.token_urlsafe(32)


def ensure_csrf_cookie(response) -> None:
    """Set the CSRF cookie if missing (or on login/setup)."""
    if response is None:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    if not cookie or len(cookie) < 16:
        response.set_cookie(
            CSRF_COOKIE,
            _new_token(),
            max_age=60 * 60 * 24 * 30,  # 30 days
            httponly=False,  # JS must read it to set the header
            samesite="Lax",
            secure=request.is_secure or request.headers.get("X-Forwarded-Proto") == "https",
        )


def check_csrf() -> bool:
    """Verify the X-CSRF-Token header against the cookie (double-submit).

    An attacker's page cannot read the victim's cookie (same-origin policy)
    and cannot set custom headers cross-origin without a CORS preflight,
    so a matching header proves the request came from our own JS.
    """
    header = request.headers.get(CSRF_HEADER, "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not header or not cookie:
        return False
    return secrets.compare_digest(header, cookie)


def csrf_error():
    return jsonify({"error": "CSRF token missing or invalid"}), 403


# ---------------------------------------------------------------------------
# Login rate limiting (per IP, sliding window)
# ---------------------------------------------------------------------------

class _RateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allowed(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(hits) >= self.max_attempts:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


login_limiter = _RateLimiter(LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_SECONDS)


def client_ip() -> str:
    """Best-effort client IP, honoring a reverse proxy."""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def apply_security_headers(response) -> None:
    for k, v in _SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
