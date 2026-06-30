"""Jamendo source - free Creative Commons music.

Get a free client_id at https://developer.jamendo.com/v3.0
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator, Optional

from .base import Source, TrackInfo, score_match

log = logging.getLogger(__name__)


class JamendoSource(Source):
    name = "jamendo"

    def __init__(self, client_id: str):
        self.client_id = client_id

    def is_available(self) -> bool:
        return bool(self.client_id)

    def search(self, artist: str, title: str, album: str = "") -> Optional[TrackInfo]:
        for info in self.search_iter(artist, title, album):
            return info
        return None

    def search_iter(self, artist: str, title: str, album: str = "") -> Iterator[TrackInfo]:
        if not self.client_id or not title:
            return
        query = " ".join(p for p in (artist, title) if p)
        if not query:
            return
        try:
            import requests
            params = {
                "client_id": self.client_id,
                "format": "json",
                "limit": 10,
                "search": query,
                "audioformat": "mp32",
                "order": "popularity_total",
            }
            r = requests.get(
                "https://api.jamendo.com/v3.0/tracks/",
                params=params,
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            results = data.get("results") or []
            if not results:
                return

            candidates = []
            for t in results:
                got_artist = t.get("artist_name") or ""
                got_title = t.get("name") or ""
                got_album = t.get("album_name") or ""
                s = score_match(artist, title, got_artist, got_title, album, got_album)
                if s < 0.3:
                    continue
                candidates.append((s, got_artist, got_title, got_album, t))

            candidates.sort(key=lambda x: x[0], reverse=True)
            for s, got_artist, got_title, got_album, t in candidates[:5]:
                yield TrackInfo(
                    source=self.name,
                    url=t["audio"],
                    artist=got_artist,
                    title=got_title,
                    album=got_album or album,
                    year=(t.get("releasedate") or "")[:4],
                    duration=t.get("duration", 0),
                    cover_url=t.get("album_image", ""),
                    match_score=s,
                )
        except Exception as e:
            log.warning(f"[jamendo] search failed: {e}")
        return

    def download(self, info: TrackInfo, output_path: Path) -> bool:
        if not info.url:
            return False
        try:
            import requests
            with requests.get(info.url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
            return output_path.exists() and output_path.stat().st_size > 1000
        except Exception as e:
            log.warning(f"[jamendo] download failed: {e}")
            return False
