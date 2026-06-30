"""Yandex OAuth 2.0 implicit flow to get an access token for Disk REST API.

Yandex supports the legacy implicit grant (response_type=token) for first-party
apps and trusted integrations. This is the simplest OAuth path: user authorizes
in browser, gets redirected to redirect_uri with `#access_token=...&token_type=bearer`
in the URL fragment, and we extract the token from that fragment.

The alternative (authorization_code + PKCE) is also supported but more steps.
We try implicit first, fall back to PKCE if the app doesn't have implicit enabled.

Tokens last ~1 year for Yandex; refresh tokens are not issued via implicit flow.
If your token expires, just re-run `music-loader yandex-oauth`.
"""
from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

log = logging.getLogger(__name__)

AUTH_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.ru/token"
DEFAULT_REDIRECT = "https://oauth.yandex.com/verification_code"
# Note: the app-folder scope is "cloud_api:disk.app_folder" (with
# "_folder" suffix), NOT "cloud_api:disk.app". Getting this wrong
# returns "invalid_scope" from Yandex.
DEFAULT_SCOPES = "cloud_api:disk.app_folder cloud_api:disk.read cloud_api:disk.write"


@dataclass
class YandexToken:
    access_token: str = ""
    expires_in: int = 0
    token_type: str = "bearer"
    scope: str = ""


# ---------- Implicit flow (no PKCE, no client_secret needed) ----------

def build_implicit_url(client_id: str, redirect_uri: str = DEFAULT_REDIRECT,
                       scopes: str = DEFAULT_SCOPES,
                       state: str = "music-loader") -> str:
    """Build the URL the user opens in their browser to authorize."""
    return (
        f"{AUTH_URL}?response_type=token"
        f"&client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scopes}"
        f"&state={state}"
    )


def extract_token_from_redirect(url: str) -> Optional[YandexToken]:
    """Pull access_token out of a Yandex redirect URL.

    The redirect URL looks like:
      https://oauth.yandex.com/verification_code#access_token=AQA...&token_type=bearer
    or (on error):
      https://oauth.yandex.com/verification_code#error=...&error_description=...

    We handle both query-style and fragment-style token placement.
    """
    if not url:
        return None
    parsed = urlparse(url.strip())
    # Token usually lives in the fragment (#access_token=...)
    frag = parsed.fragment
    qs = parse_qs(frag) if frag else {}
    # Also check query string (in case redirect uses ?)
    if not qs:
        qs = parse_qs(parsed.query)

    if "error" in qs:
        err = qs.get("error_description", qs.get("error", ["unknown"]))[0]
        log.error(f"Yandex OAuth error: {err}")
        return None

    token = qs.get("access_token", [None])[0]
    if not token:
        # Fallback: regex over the whole URL (handles malformed pastes)
        m = re.search(r"access_token=([A-Za-z0-9_\-\.]+)", url)
        token = m.group(1) if m else None
    if not token:
        return None
    return YandexToken(
        access_token=token,
        token_type=qs.get("token_type", ["bearer"])[0] or "bearer",
        expires_in=int(qs.get("expires_in", ["0"])[0] or 0),
        scope=qs.get("scope", [""])[0] or "",
    )


# ---------- Authorization code flow (with PKCE) ----------

def build_pkce_url(client_id: str, code_challenge: str, state: str,
                   redirect_uri: str = DEFAULT_REDIRECT,
                   scopes: str = DEFAULT_SCOPES) -> str:
    return (
        f"{AUTH_URL}?response_type=code"
        f"&client_id={client_id}"
        f"&scope={scopes}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE flow."""
    import base64, hashlib
    verifier_bytes = secrets.token_bytes(64)
    code_verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return code_verifier, challenge


def exchange_code_for_token(client_id: str, client_secret: str,
                           code: str, code_verifier: str,
                           redirect_uri: str = DEFAULT_REDIRECT,
                           state: str = "music-loader") -> YandexToken:
    """Trade an authorization code (PKCE flow) for an access token."""
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "state": state,
    }
    r = requests.post(TOKEN_URL, data=payload, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Yandex OAuth: {data.get('error_description', data['error'])}")
    return YandexToken(
        access_token=data.get("access_token", ""),
        expires_in=int(data.get("expires_in", 0)),
        token_type=data.get("token_type", "bearer"),
        scope=data.get("scope", ""),
    )


# ---------- Token validation ----------

def test_token(token: str) -> bool:
    """Return True if the token is valid by calling a cheap Yandex API."""
    try:
        r = requests.get(
            "https://cloud-api.yandex.net/v1/disk/",
            headers={"Authorization": f"OAuth {token}"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        log.debug(f"token test failed: {e}")
        return False
