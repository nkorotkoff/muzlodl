"""Yandex Music source.

Uses the unofficial `yandex-music` library. Search works without a token
(returns 30s previews on download). With a token + active subscription,
full tracks can be downloaded.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator, Optional

from .base import Source, TrackInfo, score_match

log = logging.getLogger(__name__)


class YandexMusicSource(Source):
    name = "yandex"

    def __init__(self, token: str = ""):
        self.token = token
        self.client = None
        self._init_client()

    def _init_client(self):
        try:
            from yandex_music import Client
            self.client = Client(self.token).init() if self.token else Client().init()
        except ImportError:
            log.warning("yandex-music not installed; yandex source disabled")
            self.client = None
        except Exception as e:
            log.warning(f"yandex-music init failed: {e}")
            self.client = None

    def is_available(self) -> bool:
        return self.client is not None

    def search(self, artist: str, title: str, album: str = "") -> Optional[TrackInfo]:
        for info in self.search_iter(artist, title, album):
            return info
        return None

    def search_iter(self, artist: str, title: str, album: str = "") -> Iterator[TrackInfo]:
        if not self.client or not title:
            return
        query = " - ".join(p for p in (artist, title) if p)
        if not query:
            return
        try:
            result = self.client.search(query, type_="track")
            if not result or not result.tracks:
                return
            candidates = []
            for t in result.tracks.results[:5]:
                if not t.available:
                    continue
                track_artists = ", ".join(a.name for a in t.artists) if t.artists else artist
                track_album = t.albums[0].title if t.albums else album
                track_year = str(t.albums[0].year) if t.albums and t.albums[0].year else ""
                cover = ""
                if t.albums and t.albums[0].cover_uri:
                    cover = f"https://{t.albums[0].cover_uri.replace('%%', '1000x1000')}"
                s = score_match(artist, title, track_artists, t.title, album, track_album)
                if s < 0.3:
                    continue
                candidates.append((s, t, track_artists, track_album, track_year, cover))

            candidates.sort(key=lambda x: x[0], reverse=True)
            for s, t, track_artists, track_album, track_year, cover in candidates:
                yield TrackInfo(
                    source=self.name,
                    url=f"yandex:{t.id}",
                    artist=track_artists,
                    title=t.title,
                    album=track_album,
                    year=track_year,
                    duration=(t.duration_ms or 0) / 1000,
                    cover_url=cover,
                    match_score=s,
                    extra={"id": str(t.id), "real_id": t.real_id,
                           "raw_title": t.title, "raw_artist": track_artists},
                )
        except Exception as e:
            log.warning(f"[yandex] search failed: {e}")
        return

    def download(self, info: TrackInfo, output_path: Path) -> bool:
        if not self.client:
            return False
        track_id = info.extra.get("id")
        if not track_id:
            return False
        try:
            tracks = self.client.tracks([track_id])
            if not tracks:
                return False
            track = tracks[0]

            if not self.token:
                # Anonymous: 30s preview
                if not track.preview_url:
                    log.info(f"[yandex] no preview available for {info.title}")
                    return False
                import requests
                with requests.get(track.preview_url, stream=True, timeout=30) as r:
                    r.raise_for_status()
                    with open(output_path, "wb") as f:
                        for chunk in r.iter_content(8192):
                            f.write(chunk)
                return output_path.exists() and output_path.stat().st_size > 1000

            # Authenticated: full track
            track.download(str(output_path), codec="mp3", bitrate_in_kbps=320)
            return output_path.exists() and output_path.stat().st_size > 1000
        except Exception as e:
            log.warning(f"[yandex] download failed: {e}")
            return False
