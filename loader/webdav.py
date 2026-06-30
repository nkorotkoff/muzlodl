"""Generic WebDAV client. Works with Yandex.Disk, Cloud.Mail.ru, and any
RFC 4918 compliant WebDAV server.

Uses only `requests` — no extra deps.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator, List, Optional, Tuple
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)


class WebDAVError(Exception):
    pass


class WebDAVClient:
    """Minimal WebDAV client.

    Auth: HTTP Basic (login + password or app password).
    Path: always starts with `/`, server-relative (e.g. `/music/Artist/Album.mp3`).
    """

    def __init__(self, url: str, login: str, password: str,
                 verify_ssl: bool = True, timeout: int = 300):
        self.base = url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (login, password)
        self.session.headers["User-Agent"] = "music-loader/0.1"
        self.session.verify = verify_ssl
        self.timeout = timeout

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        joined = "/".join(quote(p, safe="") for p in path.split("/") if p)
        return self.base.rstrip("/") + "/" + joined

    def _request(self, method: str, path: str, **kw) -> requests.Response:
        url = self._url(path)
        try:
            r = self.session.request(method, url, timeout=self.timeout, **kw)
        except requests.RequestException as e:
            raise WebDAVError(f"{method} {path}: {e}") from e
        return r

    # ---- Operations ----

    def exists(self, path: str) -> bool:
        r = self._request("PROPFIND", path, headers={"Depth": "0"},
                          data=b"")
        return r.status_code in (200, 207)

    def is_dir(self, path: str) -> bool:
        r = self._request("PROPFIND", path, headers={"Depth": "0"},
                          data=b"")
        if r.status_code not in (200, 207):
            return False
        return "<resourcetype><collection" in r.text or "<D:collection" in r.text

    def mkdir(self, path: str) -> bool:
        """Create a directory (and parents). Returns True if it exists at end."""
        if self.is_dir(path):
            return True
        # Recursively create parents
        parts = path.strip("/").split("/")
        current = ""
        for p in parts:
            current += "/" + p
            r = self._request("MKCOL", current)
            if r.status_code not in (201, 405, 301, 302):
                # 301/302 = already exists in some implementations
                if r.status_code >= 400:
                    log.debug(f"MKCOL {current} -> {r.status_code}: {r.text[:120]}")
            if not self.is_dir(current):
                # Some servers return 405 Method Not Allowed for existing dirs
                if r.status_code not in (201, 204, 301, 302, 405):
                    raise WebDAVError(f"mkdir {current}: HTTP {r.status_code} {r.text[:200]}")
        return self.is_dir(path)

    def upload(self, local_path: Path, remote_path: str) -> bool:
        """PUT a local file to remote_path. Creates parent dirs."""
        parent = "/".join(remote_path.strip("/").split("/")[:-1])
        if parent:
            self.mkdir(parent)
        with open(local_path, "rb") as f:
            r = self._request("PUT", remote_path, data=f.read(),
                              headers={"Content-Type": "audio/mpeg",
                                       "Content-Length": str(local_path.stat().st_size)})
        if r.status_code not in (200, 201, 204):
            raise WebDAVError(f"upload {remote_path}: HTTP {r.status_code} {r.text[:200]}")
        return True

    def upload_streaming(self, local_path: Path, remote_path: str,
                         chunk_size: int = 256 * 1024) -> bool:
        """Stream-upload a local file (better for large files)."""
        parent = "/".join(remote_path.strip("/").split("/")[:-1])
        if parent:
            self.mkdir(parent)
        size = local_path.stat().st_size
        with open(local_path, "rb") as f:
            r = self.session.put(
                self._url(remote_path),
                data=f,
                timeout=self.timeout,
                headers={"Content-Type": "audio/mpeg", "Content-Length": str(size)},
            )
        if r.status_code not in (200, 201, 204):
            raise WebDAVError(f"upload {remote_path}: HTTP {r.status_code} {r.text[:200]}")
        return True

    def delete(self, remote_path: str) -> bool:
        r = self._request("DELETE", remote_path)
        return r.status_code in (204, 404)

    def list(self, path: str = "/") -> List[Tuple[str, bool, int]]:
        """List directory. Returns [(name, is_dir, size), ...]."""
        r = self._request("PROPFIND", path, headers={"Depth": "1"},
                          data=b"")
        if r.status_code not in (200, 207):
            return []
        return _parse_propfind(r.text, base_path=path)

    def walk(self, root: str = "/") -> Iterator[Tuple[str, bool]]:
        """Recursively walk. Yields (path, is_dir) for everything under root."""
        for name, is_dir, _ in self.list(root):
            if name in (".", ""):
                continue
            full = (root.rstrip("/") + "/" + name) if root != "/" else "/" + name
            yield full, is_dir
            if is_dir:
                yield from self.walk(full)


def _parse_propfind(xml: str, base_path: str) -> List[Tuple[str, bool, int]]:
    """Crude PROPFIND XML response parser. Returns [(name, is_dir, size)]."""
    import re
    # Strip namespace prefix to simplify matching
    xml = re.sub(r"<(/?)D:", r"<\1", xml)
    xml = re.sub(r'xmlns:[A-Za-z]+="[^"]*"', "", xml)
    results: List[Tuple[str, bool, int]] = []
    for m in re.finditer(r"<response[^>]*>(.*?)</response>", xml, re.DOTALL | re.IGNORECASE):
        body = m.group(1)
        href_m = re.search(r"<href[^>]*>([^<]+)</href>", body, re.IGNORECASE)
        if not href_m:
            continue
        href = href_m.group(1)
        from urllib.parse import urlparse, unquote
        path = unquote(urlparse(href).path)
        name = path.rstrip("/").rsplit("/", 1)[-1] if "/" in path else path.lstrip("/")
        is_dir = bool(re.search(r"<resourcetype>\s*<collection", body, re.IGNORECASE))
        size_m = re.search(r"<getcontentlength[^>]*>(\d+)</getcontentlength>",
                           body, re.IGNORECASE)
        size = int(size_m.group(1)) if size_m else 0
        if path.rstrip("/") == base_path.rstrip("/"):
            continue
        results.append((name, is_dir, size))
    return results
