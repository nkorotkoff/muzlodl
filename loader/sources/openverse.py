"""Openverse (https://openverse.org) meta-search source.

Openverse is a CC Search engine by the Wikimedia Foundation. It indexes
CC-licensed audio from Jamendo, Wikimedia Commons, Freesound, etc. and
returns direct file URLs. No auth required.

Useful for: any CC-licensed music (indie, electronic, classical,
sound effects). Won't find popular commercial music (still under
copyright) but covers the long tail of free content.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterator, Optional

from .base import Source, TrackInfo

log = logging.getLogger(__name__)

API_URL = "https://api.openverse.org/v1/audio/"


def _score(item: dict, want_artist: str, want_title: str) -> float:
    title = (item.get("title") or "").lower()
    creator = (item.get("creator") or "").lower()
    a = (want_artist or "").lower()
    t = (want_title or "").lower()
    title_s = 0.0
    if t and t in title:
        title_s = 1.0
    elif t:
        t_words = set(t.split())
        title_words = set(re.sub(r"[^\w\s]", " ", title).split())
        if t_words:
            title_s = len(t_words & title_words) / max(len(t_words), 1)
    artist_s = 0.0
    if a:
        if a in creator or creator in a:
            artist_s = 1.0
        else:
            a_words = set(a.split())
            c_words = set(creator.split())
            if a_words:
                artist_s = len(a_words & c_words) / max(len(a_words), 1)
    return 0.7 * title_s + 0.3 * artist_s


def _is_likely_music(item: dict) -> bool:
    """Skip Freesound sound effects; prefer music tracks."""
    cat = (item.get("category") or "").lower()
    genres = item.get("genres") or []
    provider = (item.get("provider") or "").lower()
    if cat == "music":
        return True
    if provider == "jamendo":
        return True
    # Heuristic: Freesound items rarely have genres; Wikimedia often doesn't either.
    # If we have a duration over 30s and a clear title, treat as music.
    if (item.get("duration") or 0) > 30_000:  # ms
        return True
    return False


class OpenverseSource(Source):
    name = "openverse"

    def is_available(self) -> bool:
        return True

    def search(self, artist: str, title: str, album: str = "") -> Optional[TrackInfo]:
        for info in self.search_iter(artist, title, album):
            return info
        return None

    def search_iter(self, artist: str, title: str, album: str = "") -> Iterator[TrackInfo]:
        if not (artist or title):
            return
        query = " ".join(p for p in (artist, title) if p)
        if not query:
            return
        try:
            import requests
            r = requests.get(
                API_URL,
                params={"q": query, "page_size": 10, "format": "json"},
                timeout=15,
            )
            r.raise_for_status()
            # Strip control characters that Openverse includes in waveform data
            raw = r.text
            clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", raw)
            import json
            data = json.loads(clean)
        except Exception as e:
            log.warning(f"[openverse] search failed: {e}")
            return

        results = data.get("results") or []
        # Filter and score
        candidates = []
        for r in results:
            if not _is_likely_music(r):
                continue
            url = r.get("url")
            if not url:
                continue
            score = _score(r, artist, title)
            if score < 0.2:
                continue
            dur = (r.get("duration") or 0) / 1000.0
            cover = r.get("thumbnail") or ""
            candidates.append(TrackInfo(
                source=self.name,
                url=url,
                artist=r.get("creator") or artist,
                title=r.get("title") or title,
                album=album or (r.get("audio_set") or {}).get("title", "") or "",
                duration=dur,
                cover_url=cover,
                match_score=score,
                extra={
                    "license": r.get("license_url", ""),
                    "provider": r.get("provider", ""),
                    "bit_rate": r.get("bit_rate", 0),
                },
            ))

        # Sort by score, yield
        candidates.sort(key=lambda x: x.match_score, reverse=True)
        for c in candidates:
            yield c

    def download(self, info: TrackInfo, output_path: Path) -> bool:
        try:
            import requests
            with requests.get(
                info.url, stream=True, timeout=180, allow_redirects=True,
            ) as r:
                r.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(64 * 1024):
                        f.write(chunk)
            return output_path.exists() and output_path.stat().st_size > 1000
        except Exception as e:
            log.warning(f"[openverse] download failed: {e}")
            return False
