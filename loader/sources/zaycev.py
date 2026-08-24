"""zaycev.net source — Russian music portal with direct MP3 streaming.

No auth needed. Flow per track:
  1. GET /api/external/pages/search/tracks?q=<query>  -> track ids + info
  2. POST /api/external/track/filezmeta               -> streaming hash per id
  3. GET  /api/external/track/play/<hash>             -> {"url": "https://dl.zaycev.net/..."}
  4. GET  that url                                    -> MP3 bytes

The site is Next.js/React: the SSR /search page ignores the query string
and serves a static block, so the JSON search API above is the only
reliable way to find tracks. All endpoints work unauthenticated (as of
2026-08).
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import quote

import requests

from .base import Source, TrackInfo, score_match

log = logging.getLogger(__name__)

API = "https://zaycev.net/api/external"
SEARCH_URL = API + "/pages/search/tracks?q={query}&page=1&limit=20"
FILEZMETA_URL = API + "/track/filezmeta"
PLAY_URL = API + "/track/play/{hash}"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class ZaycevSource(Source):
    name = "zaycev"

    def __init__(self, timeout: int = 30, min_interval: float = 0.0):
        self._timeout = timeout
        self._min_interval = min_interval
        self._last_request = 0.0
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})

    def is_available(self) -> bool:
        try:
            r = self._session.get(
                SEARCH_URL.format(query=quote("тест")),
                timeout=min(self._timeout, 10),
            )
            return r.status_code == 200 and "trackIds" in r.text
        except Exception:
            return False

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        dt = time.monotonic() - self._last_request
        if dt < self._min_interval:
            time.sleep(self._min_interval - dt)
        self._last_request = time.monotonic()

    def _fetch_search(self, artist: str, title: str) -> Optional[dict]:
        query = " ".join(p for p in (artist, title) if p).strip()
        if not query:
            return None
        self._throttle()
        try:
            r = self._session.get(
                SEARCH_URL.format(query=quote(query)),
                timeout=self._timeout,
            )
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            log.debug(f"[zaycev] search {query}: {e}")
        return None

    def _parse_results(self, data: dict) -> list[dict]:
        """Parse search JSON into [{id, artist, title, url, duration}]."""
        info = data.get("tracksInfo") or {}
        results = []
        for tid in data.get("trackIds") or []:
            t = info.get(str(tid))
            if not t:
                continue
            title = (t.get("track") or "").strip()
            if not title:
                continue
            duration = t.get("duration") or ""
            secs = 0
            m = re.match(r"(\d+):(\d{2})", duration)
            if m:
                secs = int(m.group(1)) * 60 + int(m.group(2))
            results.append({
                "id": str(tid),
                "artist": (t.get("artistName") or "").strip(),
                "title": title,
                "url": f"https://zaycev.net/pages/{str(tid)[:6]}/{tid}.shtml",
                "duration": secs or None,
            })
        return results

    def search(self, artist: str, title: str, album: str = "") -> Optional[TrackInfo]:
        for info in self.search_iter(artist, title, album):
            return info
        return None

    def search_iter(self, artist: str, title: str, album: str = "") -> Iterator[TrackInfo]:
        data = self._fetch_search(artist, title)
        if not data:
            return

        candidates = self._parse_results(data)
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
                url=c["url"],
                artist=c["artist"] or artist,
                title=c["title"] or title,
                album="",
                duration=c.get("duration"),
                match_score=score,
                extra={
                    "track_id": c["id"],
                    "raw_title": c["title"],
                    "raw_artist": c["artist"],
                },
            )

    def _stream_url(self, track_id: str) -> Optional[str]:
        """filezmeta -> play -> direct MP3 url."""
        self._throttle()
        try:
            r = self._session.post(
                FILEZMETA_URL,
                json={"trackIds": [track_id], "subscription": False},
                timeout=self._timeout,
            )
            if r.status_code != 200:
                log.debug(f"[zaycev] filezmeta {track_id}: HTTP {r.status_code}")
                return None
            data = r.json()
            tracks = data.get("tracks") or []
            if not tracks or not tracks[0].get("streaming"):
                return None
            h = tracks[0]["streaming"]
        except Exception as e:
            log.debug(f"[zaycev] filezmeta {track_id}: {e}")
            return None

        self._throttle()
        try:
            r = self._session.get(PLAY_URL.format(hash=h), timeout=self._timeout)
            if r.status_code != 200:
                return None
            d = r.json()
            return d.get("url") or None
        except Exception as e:
            log.debug(f"[zaycev] play {track_id}: {e}")
            return None

    def download(self, info: TrackInfo, output_path) -> bool:
        output_path = Path(output_path)
        track_id = (info.extra or {}).get("track_id")
        if not track_id:
            idm = re.search(r"/pages/\d+/(\d+)\.shtml", info.url or "")
            track_id = idm.group(1) if idm else None
        if not track_id:
            return False
        url = self._stream_url(track_id)
        if not url:
            return False
        try:
            with self._session.get(url, stream=True, timeout=self._timeout) as r:
                r.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
        except Exception as e:
            log.warning(f"[zaycev] download {url}: {e}")
            return False
        # dl.zaycev.net serves real MP3 (ID3 header). Anything else is a
        # geo/anti-bot page — reject so the pipeline moves to the next source.
        try:
            with open(output_path, "rb") as f:
                head = f.read(16)
        except OSError:
            return False
        is_mp3 = head[:3] == b"ID3" or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
        if not is_mp3:
            try:
                output_path.unlink()
            except OSError:
                pass
            log.warning(f"[zaycev] not an MP3 ({len(head)}b), rejected: {url}")
            return False
        return output_path.stat().st_size > 0
