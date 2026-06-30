"""Wikimedia Commons source for audio files.

Searches audio files in the Commons category, then resolves to a direct
download URL via the imageinfo API. Covers classical music (public
domain), CC-licensed performances, sound logos, etc.

No auth required. Good for classical, orchestral, public-domain audio,
and the long tail of CC-licensed recordings.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterator, Optional

from .base import Source, TrackInfo

log = logging.getLogger(__name__)

SEARCH_URL = "https://commons.wikimedia.org/w/api.php"
FILE_URL = "https://commons.wikimedia.org/w/api.php"


def _score(title: str, want_artist: str, want_title: str) -> float:
    t = (want_title or "").lower()
    a = (want_artist or "").lower()
    title_l = title.lower()
    title_s = 0.0
    if t and t in title_l:
        title_s = 1.0
    elif t:
        t_words = set(t.split())
        title_words = set(re.sub(r"[^\w\s]", " ", title_l).split())
        if t_words:
            title_s = len(t_words & title_words) / max(len(t_words), 1)
    artist_s = 0.0
    if a and a in title_l:
        artist_s = 0.5
    return 0.7 * title_s + 0.3 * artist_s


def _is_audio_file(name: str) -> bool:
    n = name.lower()
    return n.endswith((".ogg", ".mp3", ".flac", ".wav", ".opus", ".webm"))


class WikimediaCommonsSource(Source):
    name = "wikicommons"

    AUDIO_MIMES = {
        "audio/ogg", "audio/mpeg", "audio/flac", "audio/wav",
        "audio/opus", "audio/webm",
    }

    def is_available(self) -> bool:
        return True

    def search(self, artist: str, title: str, album: str = "") -> Optional[TrackInfo]:
        for info in self.search_iter(artist, title, album):
            return info
        return None

    def search_iter(self, artist: str, title: str, album: str = "") -> Iterator[TrackInfo]:
        if not (artist or title):
            return
        try:
            import requests
            query = " ".join(p for p in (artist, title) if p)
            r = requests.get(
                SEARCH_URL,
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query + " filetype:audio",
                    "srnamespace": 6,  # File namespace
                    "srlimit": 10,
                    "format": "json",
                },
                timeout=15,
            )
            r.raise_for_status()
            hits = (r.json().get("query") or {}).get("search") or []
        except Exception as e:
            log.warning(f"[wikicommons] search failed: {e}")
            return

        # Score and filter audio files
        candidates = []
        for h in hits:
            title_only = h.get("title", "").replace("File:", "")
            if not _is_audio_file(title_only):
                continue
            score = _score(title_only, artist, title)
            if score < 0.2:
                continue
            candidates.append((title_only, score))

        # Resolve each candidate to a download URL
        for title_only, score in candidates:
            info = self._resolve(title_only, artist, title, score, album)
            if info:
                yield info

    def _resolve(self, filename: str, want_artist: str, want_title: str,
                 score: float, album: str) -> Optional[TrackInfo]:
        try:
            import requests
            r = requests.get(
                FILE_URL,
                params={
                    "action": "query",
                    "titles": f"File:{filename}",
                    "prop": "imageinfo",
                    "iiprop": "url|size|mime|extmetadata",
                    "format": "json",
                },
                timeout=15,
            )
            r.raise_for_status()
            pages = (r.json().get("query") or {}).get("pages") or {}
            for page in pages.values():
                if page.get("missing"):
                    return None
                infos = page.get("imageinfo") or []
                if not infos:
                    return None
                info = infos[0]
                mime = info.get("mime", "")
                if mime not in self.AUDIO_MIMES:
                    return None
                dur_ms = 0
                ext = info.get("extmetadata") or {}
                for k in ("Duration", "duration", "Length"):
                    if k in ext:
                        try:
                            dur_ms = int(float(ext[k]) * 1000)
                            break
                        except (TypeError, ValueError):
                            pass
                return TrackInfo(
                    source=self.name,
                    url=info["url"],
                    artist=want_artist,
                    title=want_title,
                    album=album or "",
                    duration=dur_ms / 1000,
                    match_score=score,
                    extra={"filename": filename, "mime": mime, "size": info.get("size", 0)},
                )
        except Exception as e:
            log.debug(f"[wikicommons] resolve {filename} failed: {e}")
        return None

    def download(self, info: TrackInfo, output_path: Path) -> bool:
        try:
            import requests
            with requests.get(
                info.url, stream=True, timeout=300, allow_redirects=True,
            ) as r:
                r.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(64 * 1024):
                        f.write(chunk)
            return output_path.exists() and output_path.stat().st_size > 1000
        except Exception as e:
            log.warning(f"[wikicommons] download failed: {e}")
            return False
