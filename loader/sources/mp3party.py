"""mp3party.net source — direct MP3 links, no API.

Search: ?do=search&subaction=search&story=<query> returns HTML with
data-js-* attributes (id, artist-name, song-title, url). The audio URL
(dl2.mp3party.net/online/<id>.mp3) serves real MP3 when requested with
the site's session cookie and browser-like Accept headers.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from html import unescape
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .base import Source, TrackInfo, score_match

log = logging.getLogger(__name__)

BASE = "https://mp3party.net"
SEARCH_URL = BASE + "/search?q={query}"
MP3_HOST = "https://dl2.mp3party.net"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class MP3PartySource(Source):
    name = "mp3party"

    # dl2.mp3party.net regularly serves "failed to get file info: nil"
    # (29-byte junk) for EVERY track for long stretches. Downloading 5-10
    # junk candidates per track burns ~20s each; when that happens, trip a
    # process-wide breaker and skip the source until it recovers.
    _breaker_until = 0.0
    _breaker_fails = 0
    _BREAKER_THRESHOLD = 3
    _BREAKER_COOLDOWN = 15 * 60.0  # 15 minutes
    _breaker_lock = threading.Lock()

    def __init__(self, timeout: int = 30):
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})

    def is_available(self) -> bool:
        try:
            r = self._session.get(BASE + "/", timeout=8)
            return r.status_code == 200
        except Exception:
            return False

    def _fetch_search(self, artist: str, title: str) -> Optional[str]:
        query = " ".join(p for p in (artist, title) if p).strip()
        if not query:
            return None
        url = SEARCH_URL.format(query=quote(query))
        try:
            r = self._session.get(url, timeout=self._timeout)
            if r.status_code == 200 and "dl2.mp3party.net" in r.text:
                return r.text
        except Exception as e:
            log.debug(f"[mp3party] search {url}: {e}")
        return None

    def _parse_results(self, html: str) -> list[dict]:
        """Parse search results into [{id, artist, title, url}]."""
        results = []
        # data-js-* attributes sit on the same <div>, in any order
        # (artist-name comes BEFORE data-js-id on mp3party). Match the
        # whole tag, not a forward-only window.
        pat = re.compile(r'<[^>]*\bdata-js-id="(\d+)"[^>]*>')
        for m in pat.finditer(html):
            tag = m.group(0)
            sid = m.group(1)
            art_m = re.search(r'data-js-artist-name="([^"]*)"', tag)
            title_m = re.search(r'data-js-song-title="([^"]*)"', tag)
            url_m = re.search(r'data-js-url="(https://dl2\.mp3party\.net/online/\d+\.mp3)"', tag)
            if not title_m or not url_m:
                continue
            title = title_m.group(1)
            if not title:
                continue
            artist = art_m.group(1) if art_m else ""
            # Decode HTML entities (&amp;, &#39;, ...) in titles/artists
            title = unescape(title)
            artist = unescape(artist)
            results.append({
                "id": sid, "artist": artist, "title": title, "url": url_m.group(1),
            })
        # Dedup by id (same track appears in multiple sections)
        seen = set()
        unique = []
        for c in results:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            unique.append(c)
        return unique

    def search(self, artist: str, title: str, album: str = "") -> Optional[TrackInfo]:
        for info in self.search_iter(artist, title, album):
            return info
        return None

    def _breaker_open(self) -> bool:
        with self._breaker_lock:
            return time.monotonic() < MP3PartySource._breaker_until

    @classmethod
    def _breaker_fail(cls) -> None:
        with cls._breaker_lock:
            cls._breaker_fails += 1
            if cls._breaker_fails >= cls._BREAKER_THRESHOLD:
                cls._breaker_until = time.monotonic() + cls._BREAKER_COOLDOWN
                cls._breaker_fails = 0
                log.warning("[mp3party] CDN serving junk, source disabled for %d min",
                            cls._BREAKER_COOLDOWN // 60)

    @classmethod
    def _breaker_ok(cls) -> None:
        with cls._breaker_lock:
            cls._breaker_fails = 0

    def search_iter(self, artist: str, title: str, album: str = "") -> Iterator[TrackInfo]:
        if self._breaker_open():
            return
        html = self._fetch_search(artist, title)
        if not html:
            return

        candidates = self._parse_results(html)
        scored = []
        for c in candidates:
            s = score_match(artist, title, c["artist"], c["title"], album, "")
            if s < 0.3:
                continue
            scored.append((s, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        for score, c in scored:
            yield TrackInfo(
                source=self.name,
                url=c["url"],
                artist=c["artist"] or artist,
                title=c["title"] or title,
                album="",
                match_score=score,
                extra={"raw_title": c["title"], "raw_artist": c["artist"]},
            )

    def download(self, info: TrackInfo, output_path) -> bool:
        url = info.url
        if not url:
            return False
        output_path = Path(output_path)
        headers = {
            "Referer": "https://mp3party.net/",
            "Accept": "audio/mpeg,audio/*;q=0.9,*/*;q=0.8",
        }
        for attempt in (0, 1):
            try:
                with self._session.get(url, stream=True, timeout=self._timeout, headers=headers) as r:
                    r.raise_for_status()
                    with open(output_path, "wb") as f:
                        for chunk in r.iter_content(8192):
                            f.write(chunk)
            except Exception as e:
                log.warning(f"[mp3party] download {url}: {e}")
                return False
            try:
                with open(output_path, "rb") as f:
                    head = f.read(16)
            except OSError:
                return False
            is_mp3 = head[:3] == b"ID3" or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
            if is_mp3:
                self._breaker_ok()
                return output_path.stat().st_size > 0
            # junk (16/29b "failed to get file info") — retry once
            try:
                output_path.unlink()
            except OSError:
                pass
            if attempt == 0:
                log.warning(f"[mp3party] not an MP3 ({len(head)}b), retrying: {url}")
                import time as _t; _t.sleep(1)
                continue
            log.warning(f"[mp3party] not an MP3 ({len(head)}b), rejected: {url}")
            self._breaker_fail()
            return False
        return False
