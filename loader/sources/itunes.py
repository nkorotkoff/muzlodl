"""iTunes Search API. No auth required.

Used in two roles:
- As an *enricher*: returns canonical metadata (artist, album, year,
  duration, cover) for the pipeline to use before the source chain.
- As a *degraded source*: returns a 30-90s preview URL. Marked with
  `is_preview=True` so the duration check is skipped. Place it last
  in the source chain so it only fires when nothing full-length worked.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Iterator, Optional

from ..match import strip_markers
from .base import Source, TrackInfo


def _norm_title(s: str) -> str:
    """Lowercase, strip parens/punctuation and version markers."""
    t = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", s or "")
    t = re.sub(r"[^\w\s]", " ", t)
    t = " ".join(t.lower().split())
    return strip_markers(t)

log = logging.getLogger(__name__)

SEARCH_URL = "https://itunes.apple.com/search"

# iTunes Search API allows ~20 calls/minute. We use:
# - a 3s global throttle (1.5s was ~40/min — over the limit, which is
#   why parallel pipelines kept hitting 429s and stalling for minutes)
# - a disk-backed cache so repeated tracks skip the network entirely
# - 429 backoff: if we get rate-limited, sleep 30s and back off further
_ITUNES_MIN_INTERVAL = 3.0
_itunes_lock = threading.Lock()
_itunes_last_call = 0.0
_itunes_429_count = 0
_itunes_429_until = 0.0  # epoch: skip until this time

_CACHE_PATH = Path.home() / ".cache" / "music-loader" / "itunes_cache.json"
_cache_lock = threading.Lock()


def _cache_load() -> dict:
    if not _CACHE_PATH.exists():
        return {}
    try:
        with _cache_lock:
            with open(_CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return {}


def _cache_save(data: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _cache_lock:
            # Atomic replace: concurrent writers (parallel jobs, multiple
            # pipelines) must not interleave partial JSON or clobber each
            # other's entries — a lost positive entry means the next run
            # hits iTunes again (and 429s).
            tmp = _CACHE_PATH.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            tmp.replace(_CACHE_PATH)
    except Exception as e:
        log.debug(f"itunes cache save failed: {e}")


def _cache_key(artist: str, title: str) -> str:
    a = (artist or "").strip().lower()
    t = (title or "").strip().lower()
    return f"{a}::{t}"


def _itunes_throttle():
    """Sleep enough so we don't exceed iTunes rate limits.

    If we recently got 429s, we back off significantly. Otherwise we
    pace to one call per `_ITUNES_MIN_INTERVAL` seconds.
    """
    global _itunes_last_call, _itunes_429_count
    with _itunes_lock:
        now = time.time()
        # If we are in a 429 backoff window, sleep through it
        if now < _itunes_429_until:
            wait = _itunes_429_until - now
            log.debug(f"[itunes] in 429 backoff, sleeping {wait:.0f}s")
            time.sleep(wait)
            now = time.time()
        wait = _ITUNES_MIN_INTERVAL - (now - _itunes_last_call)
        if wait > 0:
            time.sleep(wait)
        _itunes_last_call = time.time()


def _itunes_mark_429() -> None:
    """Called when iTunes returns 429. Escalate backoff."""
    global _itunes_429_count, _itunes_429_until
    with _itunes_lock:
        _itunes_429_count += 1
        # 1st 429: wait 30s, 2nd: 60s, 3rd: 120s, etc.
        cooldown = 30 * (2 ** min(_itunes_429_count - 1, 4))
        _itunes_429_until = time.time() + cooldown
        log.warning(
            f"[itunes] rate-limited ({_itunes_429_count} times), backing off {cooldown}s"
        )


def _itunes_mark_success() -> None:
    """Reset the 429 counter after a clean call."""
    global _itunes_429_count, _itunes_429_until
    with _itunes_lock:
        if _itunes_429_count > 0:
            _itunes_429_count = 0
            _itunes_429_until = 0.0


def _bigger_cover(url: str) -> str:
    if url and "100x100bb" in url:
        return url.replace("100x100bb", "600x600bb")
    return url


def _score(item: dict, want_artist: str, want_title: str) -> float:
    t = (item.get("trackName") or "").lower()
    a = (item.get("artistName") or "").lower()
    wa = want_artist.lower()
    wt = want_title.lower()
    title_s = 1.0 if wt and wt in t else 0.0
    artist_s = 1.0 if wa and wa in a else 0.0
    return 0.7 * title_s + 0.3 * artist_s


def _search(artist: str, title: str):
    """Shared search helper. Returns list of result items, best match first.

    Uses an on-disk cache so repeated tracks (same artist+title) skip the
    network call entirely. Failed lookups are also cached as empty lists
    for a few minutes so we don't hammer iTunes with bad queries.
    """
    if not (artist or title):
        return []
    key = _cache_key(artist, title)
    cache = _cache_load()
    cached = cache.get(key)
    if cached is not None:
        if isinstance(cached, dict) and "expires" in cached and cached["expires"] < time.time():
            pass  # expired negative cache
        else:
            return cached if isinstance(cached, list) else cached.get("results", [])

    _itunes_throttle()
    try:
        import requests
        r = requests.get(
            SEARCH_URL,
            params={
                "term": " ".join(p for p in (artist, title) if p),
                "media": "music",
                "entity": "song",
                "limit": 5,
            },
            timeout=15,
        )
        if r.status_code == 429:
            _itunes_mark_429()
            return []
        r.raise_for_status()
        _itunes_mark_success()
    except Exception as e:
        log.warning(f"[itunes] search failed: {e}")
        return []

    results = r.json().get("results") or []
    results.sort(key=lambda x: _score(x, artist, title), reverse=True)
    results = [it for it in results if _score(it, artist, title) >= 0.3]

    # Cache positive results for 30 days, negative for 10 minutes
    if results:
        cache[key] = {"results": results, "expires": time.time() + 30 * 86400}
    else:
        cache[key] = {"results": [], "expires": time.time() + 600}
    _cache_save(cache)
    return results


class ITunesEnricher:
    name = "itunes"

    def is_available(self) -> bool:
        # If we are in a hard 429 backoff, skip
        return not (time.time() < _itunes_429_until)

    def enrich(self, artist: str, title: str) -> Optional[dict]:
        results = _search(artist, title)
        if not results:
            return None
        best = results[0]
        # NEVER rewrite the track to a different song: when the exact
        # title is missing (e.g. requested "Another Day in Paradise -
        # 2016 Remaster" but the API only returns other Phil Collins
        # tracks) the best result may score just 0.3 (artist only).
        # Using it would make the pipeline download the WRONG track.
        # Compare the marker-stripped title; mismatch → no enrichment.
        want = _norm_title(title)
        got = _norm_title(best.get("trackName") or "")
        if want and got and want != got and want not in got and got not in want:
            return None
        return {
            "artist": best.get("artistName") or artist,
            "title": best.get("trackName") or title,
            "album": best.get("collectionName") or "",
            "year": (best.get("releaseDate") or "")[:4],
            "track_no": best.get("trackNumber", 0) or 0,
            "duration": (best.get("trackTimeMillis") or 0) / 1000,
            "cover_url": _bigger_cover(best.get("artworkUrl100", "")),
        }


class ITunesPreviewSource(Source):
    """iTunes 30-90s preview. Use as last-resort fallback in the source chain."""
    name = "itunes_preview"

    def is_available(self) -> bool:
        return not (time.time() < _itunes_429_until)

    def search(self, artist: str, title: str, album: str = "") -> Optional[TrackInfo]:
        for info in self.search_iter(artist, title, album):
            return info
        return None

    def search_iter(self, artist: str, title: str, album: str = "") -> Iterator[TrackInfo]:
        for item in _search(artist, title):
            url = item.get("previewUrl")
            if not url:
                continue
            yield TrackInfo(
                source=self.name,
                url=url,
                artist=item.get("artistName") or artist,
                title=item.get("trackName") or title,
                album=item.get("collectionName") or album,
                year=(item.get("releaseDate") or "")[:4],
                duration=(item.get("trackTimeMillis") or 0) / 1000,
                cover_url=_bigger_cover(item.get("artworkUrl100", "")),
                is_preview=True,
                match_score=_score(item, artist, title),
                extra={"raw_title": item.get("trackName") or title,
                       "raw_artist": item.get("artistName") or artist},
            )

    def download(self, info: TrackInfo, output_path: Path) -> bool:
        if not info.url:
            return False
        try:
            import requests
            with requests.get(info.url, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
            return output_path.exists() and output_path.stat().st_size > 1000
        except Exception as e:
            log.warning(f"[itunes_preview] download failed: {e}")
            return False
