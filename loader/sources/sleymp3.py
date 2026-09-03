"""sleymp3.ru source — Russian music portal, AJAX-resolved MP3 links.

Flow per track (mirrors the site's own player JS):
  1. GET /fsong?q=<query>  -> server-rendered HTML; each result is an
     <li class="song-box-inline track" audio-data="0_44075306"> holding
     .artist-name / .song-name / .track-duration children.
  2. GET /vkparser?audio=<id>&title=<t>&q=<q>&artist=<a>  (AJAX, needs
     Referer + X-Requested-With) -> JSON {"link": .., "source": ..,
     "duration": ..}.  For source values "ok"/"WMZ-API"/"localFile" the
     link is a direct MP3 (yandex storage / site CDN).
  3. GET that link -> MP3 bytes.

The page's JS can also decode obfuscated "link" payloads via
window.decodeUrlModule, but that module is never shipped with the page —
for unknown source values the payload is unusable, so we return None and
let the pipeline fall through to the next candidate/source. The POST
endpoints (/engine/ajax/song.php, /ajax) are kept as fallbacks in case
the site moves the resolver (same resilience pattern as zaycev's
filezmeta/play chain).
"""
from __future__ import annotations

import json
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

BASE = "https://sleymp3.ru"
SEARCH_URL = BASE + "/fsong?q={query}"
AJAX_GET = BASE + "/vkparser?audio={audio}&title={title}&q={query}&artist={artist}"
AJAX_POSTS = [
    BASE + "/engine/ajax/song.php",
    BASE + "/ajax",
]

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Source values the player treats as already-decoded direct links
_DIRECT_SOURCES = {"ok", "WMZ-API", "localFile"}


def _clean(s: str) -> str:
    """Strip tags/entities and collapse whitespace."""
    if not s:
        return ""
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).strip()


def _duration_to_secs(text: str) -> float:
    """'03:01' -> 181.0; also accepts '1:02:03'."""
    if not text:
        return 0.0
    m = re.search(r"(?:(\d+):)?(\d{1,2}):(\d{2})", text)
    if not m:
        return 0.0
    h, mn, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mn * 60 + s


class Sleymp3Source(Source):
    name = "sleymp3"

    # Storage hosts can serve HTML junk with HTTP 200. Downloading 5-10
    # junk candidates per track burns ~20s each; when that happens, trip a
    # process-wide breaker and skip the source until it recovers (same as
    # mp3party).
    _breaker_until = 0.0
    _breaker_fails = 0
    _BREAKER_THRESHOLD = 3
    _BREAKER_COOLDOWN = 15 * 60.0  # 15 minutes
    _breaker_lock = threading.Lock()

    def __init__(self, timeout: int = 30, min_interval: float = 1.0):
        self._timeout = timeout
        self._min_interval = min_interval
        self._last_request = 0.0
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": UA,
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
            "Referer": BASE + "/",
        })

    # ---------- helpers ----------

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        dt = time.monotonic() - self._last_request
        if dt < self._min_interval:
            time.sleep(self._min_interval - dt)
        self._last_request = time.monotonic()

    def _breaker_open(self) -> bool:
        with self._breaker_lock:
            return time.monotonic() < Sleymp3Source._breaker_until

    @classmethod
    def _breaker_fail(cls) -> None:
        with cls._breaker_lock:
            cls._breaker_fails += 1
            if cls._breaker_fails >= cls._BREAKER_THRESHOLD:
                cls._breaker_until = time.monotonic() + cls._BREAKER_COOLDOWN
                cls._breaker_fails = 0
                log.warning("[sleymp3] storage serving junk, source disabled for %d min",
                            cls._BREAKER_COOLDOWN // 60)

    @classmethod
    def _breaker_ok(cls) -> None:
        with cls._breaker_lock:
            cls._breaker_fails = 0

    # ---------- availability / search ----------

    def is_available(self) -> bool:
        try:
            r = self._session.get(SEARCH_URL.format(query=quote("тест")),
                                  timeout=8)
            return r.status_code == 200 and "audio-data" in r.text
        except Exception:
            return False

    def _fetch_search(self, artist: str, title: str) -> Optional[str]:
        query = " ".join(p for p in (artist, title) if p).strip()
        if not query:
            return None
        self._throttle()
        try:
            r = self._session.get(SEARCH_URL.format(query=quote(query)),
                                  timeout=self._timeout)
            if r.status_code == 200 and "audio-data" in r.text:
                return r.text
        except Exception as e:
            log.debug(f"[sleymp3] search {query}: {e}")
        return None

    def _parse_results(self, html: str) -> list[dict]:
        """Parse search results into [{audio_data, artist, title, duration}]."""
        results = []
        try:
            for el in BeautifulSoup(html, "lxml").select("[audio-data]"):
                audio_data = (el.get("audio-data") or "").strip()
                if not audio_data:
                    continue
                artist_el = el.select_one(".artist-name")
                title_el = el.select_one(".song-name")
                artist = artist_el.get_text(strip=True) if artist_el else ""
                title = title_el.get_text(strip=True) if title_el else ""
                if not title and artist_el and artist_el.get("title"):
                    # title="ХЛЕБ - Вино" fallback: split once on " - "
                    parts = unescape(artist_el["title"]).split(" - ", 1)
                    if len(parts) == 2:
                        artist = artist or parts[0].strip()
                        title = parts[1].strip()
                if not title:
                    continue
                dur_el = el.select_one(".track-duration")
                results.append({
                    "audio_data": audio_data,
                    "artist": unescape(artist),
                    "title": unescape(title),
                    "duration": _duration_to_secs(dur_el.get_text(strip=True)) if dur_el else 0.0,
                })
        except Exception as e:
            log.debug(f"[sleymp3] bs4 parse failed, using regex fallback: {e}")
            results = []
        if results:
            return self._dedup(results)

        # Regex fallback: from each audio-data tag to the next one.
        pat = re.compile(
            r'<[^>]*\baudio-data="([^"]+)"[^>]*>(.*?)(?=<[^>]*\baudio-data=|\Z)',
            re.S,
        )
        for m in pat.finditer(html):
            audio_data, block = m.group(1), m.group(2)
            artist_m = re.search(r'class="artist-name[^"]*"[^>]*>(.*?)</span>', block, re.S)
            title_m = re.search(r'class="song-name[^"]*"[^>]*>(.*?)</span>', block, re.S)
            dur_m = re.search(r'class="track-duration[^"]*"[^>]*>(.*?)</span>', block, re.S)
            artist = _clean(artist_m.group(1)) if artist_m else ""
            title = _clean(title_m.group(1)) if title_m else ""
            if not title:
                continue
            results.append({
                "audio_data": audio_data,
                "artist": artist,
                "title": title,
                "duration": _duration_to_secs(_clean(dur_m.group(1)) if dur_m else ""),
            })
        return self._dedup(results)

    @staticmethod
    def _dedup(results: list[dict]) -> list[dict]:
        seen = set()
        unique = []
        for c in results:
            if c["audio_data"] in seen:
                continue
            seen.add(c["audio_data"])
            unique.append(c)
        return unique

    def search(self, artist: str, title: str, album: str = "") -> Optional[TrackInfo]:
        for info in self.search_iter(artist, title, album):
            return info
        return None

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
        for score, c in scored[:10]:
            yield TrackInfo(
                source=self.name,
                url="",  # resolved from audio_data at download time
                artist=c["artist"] or artist,
                title=c["title"] or title,
                album="",
                duration=c.get("duration") or None,
                match_score=score,
                extra={
                    "audio_data": c["audio_data"],
                    "raw_title": c["title"],
                    "raw_artist": c["artist"],
                },
            )

    # ---------- download ----------

    @staticmethod
    def _link_from_payload(text: str) -> Optional[str]:
        """Extract a direct MP3 url from a resolver response body."""
        body = (text or "").strip()
        if not body:
            return None
        link, source = "", ""
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                link = str(data.get("link") or "")
                source = str(data.get("source") or "")
        except ValueError:
            if body.startswith("http"):
                link = body  # legacy: bare URL body
        if not link:
            return None
        link = re.sub(r"&proxy.*$", "", link)
        if source and source not in _DIRECT_SOURCES:
            log.debug(f"[sleymp3] obfuscated link (source={source}), cannot decode")
            return None
        if link.startswith("//"):
            link = "https:" + link
        return link if link.startswith("http") else None

    def _resolve_url(self, audio_data: str, info: TrackInfo) -> Optional[str]:
        """audio-data id -> direct MP3 url via the AJAX resolver."""
        extra = info.extra or {}
        raw_title = extra.get("raw_title") or info.title or ""
        raw_artist = extra.get("raw_artist") or info.artist or ""
        query = " ".join(p for p in (raw_artist, raw_title) if p).strip()
        headers = {
            "Referer": SEARCH_URL.format(query=quote(query)) if query else BASE + "/",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        # 1. The endpoint the site's own player calls ($.get /vkparser).
        self._throttle()
        try:
            r = self._session.get(
                AJAX_GET.format(audio=quote(audio_data), title=quote(raw_title),
                                query=quote(query), artist=quote(raw_artist)),
                headers=headers, timeout=self._timeout)
            if r.status_code == 200:
                url = self._link_from_payload(r.text)
                if url:
                    return url
        except Exception as e:
            log.debug(f"[sleymp3] vkparser {audio_data}: {e}")
        # 2. POST fallbacks in case the resolver moves.
        for ajax in AJAX_POSTS:
            self._throttle()
            try:
                r = self._session.post(ajax, data={"audio": audio_data},
                                       headers=headers, timeout=self._timeout)
                if r.status_code == 200:
                    url = self._link_from_payload(r.text)
                    if url:
                        return url
            except Exception as e:
                log.debug(f"[sleymp3] {ajax} {audio_data}: {e}")
        return None

    def download(self, info: TrackInfo, output_path) -> bool:
        if self._breaker_open():
            return False
        output_path = Path(output_path)
        audio_data = (info.extra or {}).get("audio_data")
        if not audio_data and info.url:
            m = re.search(r"audio=(\w+_\d+)", info.url)
            audio_data = m.group(1) if m else None
        if not audio_data:
            return False
        url = self._resolve_url(audio_data, info)
        if not url:
            return False
        try:
            with self._session.get(url, stream=True, timeout=self._timeout) as r:
                r.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
        except Exception as e:
            log.warning(f"[sleymp3] download {url}: {e}")
            output_path.unlink(missing_ok=True)
            return False
        # Storage serves real MP3 (ID3 header or raw frame sync). Anything
        # else is a geo/anti-bot HTML page — reject so the pipeline moves
        # to the next candidate/source.
        try:
            with open(output_path, "rb") as f:
                head = f.read(16)
        except OSError:
            return False
        is_mp3 = head[:3] == b"ID3" or (head and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
        if not is_mp3:
            try:
                output_path.unlink()
            except OSError:
                pass
            log.warning(f"[sleymp3] not an MP3 ({len(head)}b), rejected: {url}")
            self._breaker_fail()
            return False
        self._breaker_ok()
        return output_path.stat().st_size > 0
