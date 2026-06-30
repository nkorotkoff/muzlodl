"""Audius source. Free streaming, no auth for reads.

Audius is a decentralized music platform. We pick a healthy discovery
host on first use and use it for the session. Falls back through a
small list of public nodes if the primary is down.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator, List, Optional

from .base import Source, TrackInfo, score_match

log = logging.getLogger(__name__)

HOSTS: List[str] = [
    "https://audius.co",
    "https://audius.discoho.be",
    "https://audius.metalune.xyz",
    "https://audius.wombo.li",
]


class AudiusSource(Source):
    name = "audius"
    _cached_host: Optional[str] = None

    @classmethod
    def _pick_host(cls) -> Optional[str]:
        if cls._cached_host:
            return cls._cached_host
        import requests
        for host in HOSTS:
            try:
                # Health check: any HTTP response (not network error) means the host is up.
                # Audius returns 200/403/404 on different endpoints, but never a connection error.
                r = requests.get(
                    f"{host}/v1/health",
                    timeout=5,
                )
                if r.status_code < 500:
                    cls._cached_host = host
                    return host
            except Exception:
                continue
        return None

    def is_available(self) -> bool:
        return self._pick_host() is not None

    def search(self, artist: str, title: str, album: str = "") -> Optional[TrackInfo]:
        for info in self.search_iter(artist, title, album):
            return info
        return None

    def search_iter(self, artist: str, title: str, album: str = "") -> Iterator[TrackInfo]:
        host = self._pick_host()
        if not host or not (artist or title):
            return
        query = " ".join(p for p in (artist, title) if p)
        try:
            import requests
            r = requests.get(
                f"{host}/v1/tracks/search",
                params={"query": query, "limit": 10},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json().get("data") or []
            candidates = []
            for t in data:
                if not t.get("streamable"):
                    continue
                dur = t.get("duration") or 0
                if dur < 30 or dur > 7200:
                    continue
                track_artist = (t.get("user") or {}).get("name") or artist
                track_title = t.get("title") or title
                s = score_match(artist, title, track_artist, track_title)
                if s < 0.3:
                    continue
                cover = ""
                art = t.get("artwork") or {}
                for size in ("1000x1000", "480x480", "150x150"):
                    if art.get(size):
                        cover = art[size]
                        break
                candidates.append((s, t, track_artist, track_title, dur, cover))

            candidates.sort(key=lambda x: x[0], reverse=True)
            for s, t, track_artist, track_title, dur, cover in candidates:
                yield TrackInfo(
                    source=self.name,
                    url=f"{host}/v1/tracks/{t['id']}/stream",
                    artist=track_artist,
                    title=track_title,
                    album=album or "",
                    year="",
                    duration=dur,
                    cover_url=cover,
                    match_score=s,
                )
        except Exception as e:
            log.warning(f"[audius] search failed: {e}")
        return

    def download(self, info: TrackInfo, output_path: Path) -> bool:
        try:
            import requests
            with requests.get(
                info.url, stream=True, timeout=120, allow_redirects=True,
            ) as r:
                r.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
            return output_path.exists() and output_path.stat().st_size > 1000
        except Exception as e:
            log.warning(f"[audius] download failed: {e}")
            return False
