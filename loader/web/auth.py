"""Auth helpers: password hashing, admin setup state."""
from __future__ import annotations

from werkzeug.security import check_password_hash, generate_password_hash

from . import db

#: Minimum password length enforced at setup / password change.
#: 8+ because the UI may be reachable from the internet.
MIN_PASSWORD_LEN = 8


def hash_password(pwd: str) -> str:
    return generate_password_hash(pwd)


def verify_password(pwd: str, stored: str) -> bool:
    """Verify a password against the stored value.

    Stored value may be a werkzeug hash (new) or a plaintext legacy value
    (pre-hash versions stored the raw password in settings).
    """
    if not stored:
        return False
    try:
        if stored.startswith(("pbkdf2:", "scrypt:", "argon2:")):
            return check_password_hash(stored, pwd)
    except ValueError:
        return False
    # Legacy plaintext comparison (constant-ish time via hmac.compare_digest)
    import hmac

    return hmac.compare_digest(pwd, stored)


def setup_done() -> bool:
    """True once an admin password has been set (first-run wizard done)."""
    return bool(db.get_setting("password", ""))


def admin_username() -> str:
    return db.get_setting("admin_username", "") or "admin"
