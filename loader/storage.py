"""Cloud storage abstraction + concrete backends.

WebDAV backends (Yandex.Disk classic, Cloud.Mail.ru):
  - Simple auth: login + app password
  - Yandex.Disk WebDAV has a known bug for files >2MB (server hangs
    on PUT response). Bulk upload still works because the whole tree
    is small per request.
  - Recommended for: Mail.ru Cloud, small libraries

REST API backend (Yandex.Disk REST):
  - OAuth 2.0 token-based auth (separate from app password)
  - Supports chunked upload (the bug-free path)
  - Recommended for: Yandex.Disk with large files / streaming upload
"""
from __future__ import annotations

import logging
import os
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from .webdav import WebDAVClient, WebDAVError

log = logging.getLogger(__name__)

# Per-backend metadata
BACKENDS = {
    "yandex": {
        "name": "Yandex.Disk (WebDAV)",
        "endpoint": "https://webdav.yandex.ru",
        "howto_url": "https://id.yandex.ru/security/app-passwords",
        "howto": (
            "1. Go to https://id.yandex.ru/security/app-passwords\n"
            "2. Click 'Create new password'\n"
            "3. Type: 'music-loader', scope: 'WebDAV'\n"
            "4. Copy the generated password (you won't see it again)\n"
            "5. Your login is your Yandex login (e.g. user@yandex.ru)"
        ),
        "root": "music",
    },
    "yandex_rest": {
        "name": "Yandex.Disk (REST API)",
        "endpoint": "https://cloud-api.yandex.net/v1/disk",
        "howto_url": "https://oauth.yandex.ru",
        "howto": (
            "1. Run `music-loader yandex-oauth` and follow the wizard\n"
            "2. Or get a token manually at oauth.yandex.ru\n"
            "3. Recommended for streaming upload (bypasses WebDAV bug)"
        ),
        "root": "music",
    },
    "mailru": {
        "name": "Cloud.Mail.ru",
        "endpoint": "https://webdav.cloud.mail.ru",
        "howto_url": "https://cloud.mail.ru",
        "howto": (
            "1. Go to https://cloud.mail.ru and sign in\n"
            "2. In settings, enable WebDAV and create an app password\n"
            "3. Your login is your full email (e.g. user@mail.ru)\n"
            "4. The password is the app password from step 2"
        ),
        "root": "music",
    },
}


@dataclass
class CloudConfig:
    backend: str = ""
    login: str = ""
    password: str = ""   # app password for WebDAV, or OAuth token for REST
    root: str = "music"  # subfolder under the storage root


class CloudStorage(ABC):
    """Abstract cloud storage. Each backend implements upload_library."""

    name: str = "base"

    def __init__(self, config: CloudConfig):
        self.config = config
        self.client = self._make_client()

    @abstractmethod
    def _make_client(self) -> WebDAVClient:
        ...

    # ---- bulk upload (post-run) ----
    def upload_library(self, library_dir: Path) -> int:
        library_dir = Path(library_dir)
        if not library_dir.exists():
            log.error(f"library dir not found: {library_dir}")
            return 0
        count = 0
        for artist_dir in sorted(library_dir.iterdir()):
            if not artist_dir.is_dir() or artist_dir.name.startswith("."):
                continue
            artist = artist_dir.name
            for album_dir in sorted(artist_dir.iterdir()):
                if not album_dir.is_dir():
                    continue
                album = album_dir.name
                if self._upload_album(album_dir, artist, album):
                    count += 1
        log.info(f"uploaded {count} albums")
        return count

    def _upload_album(self, album_dir: Path, artist: str, album: str) -> bool:
        tracks = sorted(p for p in album_dir.glob("*.mp3") if p.stat().st_size > 0)
        if not tracks:
            return False
        album_remote = f"{self.config.root}/{_safe(artist)}/{_safe(album)}"
        log.info("uploading %d tracks: %s/%s", len(tracks), artist, album)
        for t in tracks:
            remote = f"{album_remote}/{_safe(t.stem)}.mp3"
            try:
                self.client.upload_streaming(t, remote)
            except WebDAVError as e:
                log.error("  failed %s: %s", t.name, e)
                return False
        log.info("  ok: %s/%s", artist, album)
        return True

    # ---- single-file upload (streaming mode) ----
    def upload_single(self, local_path: Path, artist: str, album: str,
                      title: str, retries: int = 2) -> bool:
        """Upload one file immediately. Returns True on success.

        Used by the streaming `--upload-after-download` flow: each track
        is pushed to the cloud the moment it's downloaded, so the user
        doesn't need to keep a local copy of 1500 files.
        """
        local_path = Path(local_path)
        if not local_path.exists() or local_path.stat().st_size == 0:
            return False
        remote = (f"{self.config.root}/{_safe(artist)}/"
                  f"{_safe(album) if album else 'Singles'}/{_safe(title)}.mp3")
        # Use a longer timeout for big files
        old_timeout = self.client.timeout
        size_mb = local_path.stat().st_size / (1024 * 1024)
        # 10 sec per MB, min 120s, max 900s
        self.client.timeout = max(120, min(900, int(size_mb * 10)))
        try:
            for attempt in range(retries + 1):
                try:
                    self.client.upload_streaming(local_path, remote)
                    return True
                except WebDAVError as e:
                    if attempt < retries:
                        wait = 2 ** attempt
                        log.warning(f"  cloud upload retry {attempt+1}/{retries} "
                                    f"after {wait}s: {e}")
                        # Force a fresh connection for the next attempt —
                        # sessions can keep broken keep-alive connections.
                        try:
                            self.client.session.close()
                        except Exception:
                            pass
                        time.sleep(wait)
                    else:
                        log.error(f"  cloud upload failed after {retries+1} attempts: {e}")
            return False
        finally:
            self.client.timeout = old_timeout


class YandexDiskStorage(CloudStorage):
    name = "yandex"

    def _make_client(self) -> WebDAVClient:
        return WebDAVClient(
            url=BACKENDS["yandex"]["endpoint"],
            login=self.config.login,
            password=self.config.password,
        )


class MailRuStorage(CloudStorage):
    name = "mailru"

    def _make_client(self) -> WebDAVClient:
        return WebDAVClient(
            url=BACKENDS["mailru"]["endpoint"],
            login=self.config.login,
            password=self.config.password,
        )


# ---------------------------------------------------------------------------
# Yandex.Disk REST API backend
# ---------------------------------------------------------------------------

class YandexRestClient:
    """Thin wrapper around the Yandex.Disk REST API.

    Used for chunked uploads (the WebDAV PUT for files >2MB hangs
    indefinitely on Yandex's server). The REST flow:
      1. POST /v1/disk/resources/upload?path=...&overwrite=true -> {href, method}
      2. PUT the file to the returned href
      3. Server returns 201 Created (server processes async but responds)
    """

    def __init__(self, token: str, timeout: int = 300):
        self.token = token
        self.base = BACKENDS["yandex_rest"]["endpoint"]
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"OAuth {token}",
            "User-Agent": "music-loader/0.1",
        })

    def _url(self, path: str) -> str:
        # path looks like "/music/Artist/Album/track.mp3"
        from urllib.parse import quote
        if not path.startswith("/"):
            path = "/" + path
        # Don't url-encode slashes; encode other special chars
        parts = [quote(p, safe="") for p in path.split("/") if p]
        return f"{self.base}/" + "/".join(parts)

    def get_upload_url(self, remote_path: str, overwrite: bool = True) -> str:
        """Ask Yandex for a one-shot upload URL. Returns the href to PUT to.

        Per Yandex docs, this is a GET (not POST). Returns a 30-min
        one-shot URL that accepts the file via PUT.

        Path must be URL-encoded with %20 for spaces (not +). Yandex
        is strict: a `+` in the path is interpreted as a literal +,
        not as a space, leading to "path doesn't exist" 409 errors.
        """
        # Pass the path raw in params; requests encodes it once with
        # urllib.parse.quote, which turns spaces into %20. Pre-encoding
        # here would double-encode the % to %25 and break any path
        # containing spaces or non-ASCII chars.
        url = f"{self.base}/resources/upload"
        r = self.session.get(
            url,
            params={"path": remote_path, "overwrite": "true" if overwrite else "false"},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if "href" not in data:
            raise RuntimeError(f"yandex REST: no href in {data}")
        return data["href"]

    def upload(self, local_path: Path, remote_path: str) -> bool:
        """Upload a file via the REST API. Returns True on success.

        Before asking for an upload URL, ensures all parent directories
        exist (mkdir is idempotent on 409 Already Exists).
        """
        local_path = Path(local_path)
        if not local_path.exists() or local_path.stat().st_size == 0:
            return False
        # Ensure parent dirs exist. mkdir is recursive + idempotent.
        parent = "/".join(remote_path.strip("/").split("/")[:-1])
        if parent:
            self.mkdir(parent)
        # Ask Yandex where to PUT
        upload_url = self.get_upload_url(remote_path)
        size = local_path.stat().st_size
        with open(local_path, "rb") as f:
            r = requests.put(
                upload_url,
                data=f,
                headers={
                    "Authorization": f"OAuth {self.token}",
                    "Content-Type": "audio/mpeg",
                    "Content-Length": str(size),
                },
                timeout=self.timeout,
            )
        if r.status_code not in (200, 201, 202):
            raise RuntimeError(
                f"yandex REST PUT {remote_path}: HTTP {r.status_code} {r.text[:200]}"
            )
        return True

    def mkdir(self, remote_path: str) -> bool:
        """Create a folder. Yandex does NOT do recursive mkdir — we have
        to walk the path and create each level. Already-exists is fine.
        """
        remote_path = remote_path.strip("/")
        if not remote_path:
            return True
        # Walk the path, creating each level
        parts = remote_path.split("/")
        accumulated = ""
        for i, part in enumerate(parts):
            accumulated = f"{accumulated}/{part}" if accumulated else part
            # Try to create this level
            from urllib.parse import quote
            encoded = quote(accumulated, safe="/:")
            url = f"{self.base}/resources?path={encoded}"
            r = self.session.put(url, timeout=30)
            if r.status_code in (201, 409):
                # Created or already exists - move on
                continue
            if r.status_code == 507:
                # Insufficient storage / parent missing — shouldn't happen
                # since we walk in order, but just in case
                log.debug(f"yandex mkdir {accumulated}: 507 {r.text[:100]}")
                continue
            if r.status_code >= 400:
                log.debug(f"yandex mkdir {accumulated}: {r.status_code} {r.text[:120]}")
                return False
        return True

    def delete(self, remote_path: str) -> bool:
        r = self.session.delete(
            f"{self.base}/resources",
            params={"path": remote_path, "permanently": "false"},
            timeout=30,
        )
        return r.status_code in (200, 202, 204, 404)

    def list(self, remote_path: str = "/") -> list:
        """List a directory. Yandex REST supports proper listings (unlike WebDAV)."""
        r = self.session.get(
            f"{self.base}/resources",
            params={"path": remote_path, "limit": 1000},
            timeout=30,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        items = data.get("_embedded", {}).get("items", [])
        return [
            (item.get("name", ""),
             item.get("type") == "dir",
             item.get("size", 0) if item.get("type") != "dir" else 0)
            for item in items
        ]


class YandexDiskRESTStorage(CloudStorage):
    """Yandex.Disk via the REST API (avoids the WebDAV PUT bug for large files)."""
    name = "yandex_rest"

    def _make_client(self) -> "YandexRestClient":
        # The `client` here is a YandexRestClient (different interface from
        # WebDAVClient). We override upload helpers to use it directly.
        return YandexRestClient(self.config.password)

    # --- override the WebDAV-based _upload_album to use the REST client ---
    def _upload_album(self, album_dir: Path, artist: str, album: str) -> bool:
        tracks = sorted(p for p in album_dir.glob("*.mp3") if p.stat().st_size > 0)
        if not tracks:
            return False
        album_remote = f"{self.config.root}/{_safe(artist)}/{_safe(album)}"
        log.info("uploading %d tracks: %s/%s", len(tracks), artist, album)
        # Ensure the album folder exists
        self.client.mkdir(album_remote)
        for t in tracks:
            remote = f"{album_remote}/{_safe(t.stem)}.mp3"
            try:
                self.client.upload(t, remote)
            except Exception as e:
                log.error("  failed %s: %s", t.name, e)
                return False
        log.info("  ok: %s/%s", artist, album)
        return True

    def upload_single(self, local_path: Path, artist: str, album: str,
                      title: str, retries: int = 2) -> bool:
        local_path = Path(local_path)
        if not local_path.exists() or local_path.stat().st_size == 0:
            return False
        remote = (f"{self.config.root}/{_safe(artist)}/"
                  f"{_safe(album) if album else 'Singles'}/{_safe(title)}.mp3")
        for attempt in range(retries + 1):
            try:
                self.client.upload(local_path, remote)
                return True
            except Exception as e:
                if attempt < retries:
                    wait = 2 ** attempt
                    log.warning(f"  cloud upload retry {attempt+1}/{retries} "
                                f"after {wait}s: {e}")
                    time.sleep(wait)
                else:
                    log.error(f"  cloud upload failed after {retries+1} attempts: {e}")
        return False


def make_storage(config: CloudConfig) -> CloudStorage:
    if config.backend == "yandex":
        return YandexDiskStorage(config)
    if config.backend == "yandex_rest":
        return YandexDiskRESTStorage(config)
    if config.backend == "mailru":
        return MailRuStorage(config)
    raise ValueError(f"unknown backend: {config.backend}")


def _safe(name: str) -> str:
    """Make a name safe for cloud storage paths."""
    import re
    if not name:
        return "_"
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    s = s.strip(" .") or "_"
    return s[:200]
