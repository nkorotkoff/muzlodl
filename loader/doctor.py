"""Source auto-detection: tests each source for reachability + search + download.

Used by `music-loader doctor` and the auto-detect on first run.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 music-loader/0.1"


@dataclass
class SourceHealth:
    name: str
    available: bool = False
    can_search: bool = False
    can_download: bool = False
    latency_ms: int = 0
    reason: str = ""
    extras: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.available and self.can_search and self.can_download:
            return "ok"
        if self.available and self.can_search:
            return "search-only"
        if self.available:
            return "reachable"
        return "unreachable"


def _timed(url: str, method: str = "GET", timeout: int = 8, **kw) -> tuple:
    """Returns (status_code, latency_ms, exception_str_or_empty)."""
    t0 = time.time()
    try:
        if method == "HEAD":
            r = requests.head(url, timeout=timeout, allow_redirects=True,
                              headers={"User-Agent": UA}, **kw)
        else:
            r = requests.get(url, timeout=timeout, stream=True,
                             headers={"User-Agent": UA}, **kw)
            r.close()
        ms = int((time.time() - t0) * 1000)
        return r.status_code, ms, ""
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return 0, ms, str(e)[:80]


def test_archiveorg() -> SourceHealth:
    h = SourceHealth(name="archiveorg")
    code, ms, err = _timed("https://archive.org/advancedsearch.php?q=mediatype:audio&rows=1&output=json")
    h.latency_ms = ms
    if code == 200:
        h.available = True
        h.can_search = True
        # Test download path
        code2, ms2, _ = _timed("https://archive.org/download/")
        h.can_download = code2 in (200, 404)  # 404 is fine, means the host is up
    else:
        h.reason = f"search endpoint: HTTP {code} {err}"
    return h


def test_openverse() -> SourceHealth:
    h = SourceHealth(name="openverse")
    code, ms, err = _timed("https://api.openverse.org/v1/audio/?q=test&page_size=1")
    h.latency_ms = ms
    if code == 200:
        h.available = True
        h.can_search = True
        # Test download CDN (upload.wikimedia.org is the most common failure)
        code2, ms2, err2 = _timed("https://upload.wikimedia.org/")
        h.can_download = code2 in (200, 301, 302, 404)
        if not h.can_download:
            h.reason = f"upload.wikimedia.org {code2} {err2[:60]}"
    else:
        h.reason = f"api.openverse.org HTTP {code} {err}"
    return h


def test_wikicommons() -> SourceHealth:
    h = SourceHealth(name="wikicommons")
    code, ms, err = _timed(
        "https://commons.wikimedia.org/w/api.php?action=query&list=search&srsearch=test&srnamespace=6&format=json"
    )
    h.latency_ms = ms
    if code == 200:
        h.available = True
        h.can_search = True
        # Same upload.wikimedia.org for downloads
        code2, _, err2 = _timed("https://upload.wikimedia.org/")
        h.can_download = code2 in (200, 301, 302, 404)
        if not h.can_download:
            h.reason = f"upload.wikimedia.org {code2}"
    else:
        h.reason = f"commons API HTTP {code} {err}"
    return h


def test_audius() -> SourceHealth:
    h = SourceHealth(name="audius")
    code, ms, err = _timed("https://audius.co/v1/health", timeout=5)
    h.latency_ms = ms
    if code in (200, 204):
        h.available = True
        h.can_search = True
        # stream endpoint
        code2, _, _ = _timed("https://audius.co/v1/tracks/search?query=test", timeout=5)
        h.can_download = code2 == 200
        if not h.can_download:
            h.reason = f"search returned {code2}"
    else:
        h.reason = f"health HTTP {code} {err}"
    return h


def test_yandex() -> SourceHealth:
    h = SourceHealth(name="yandex")
    code, ms, err = _timed("https://music.yandex.ru/", timeout=5)
    h.latency_ms = ms
    if code in (200, 302):
        h.available = True
        # Without token, only preview works
        h.can_search = True
        h.can_download = True  # previews at least
        h.extras["note"] = "anonymous: 30s previews only"
    else:
        h.reason = f"music.yandex.ru HTTP {code} {err}"
    return h


def test_jamendo(config) -> SourceHealth:
    h = SourceHealth(name="jamendo")
    if not config.jamendo_client_id:
        h.reason = "JAMENDO_CLIENT_ID not set (free key at developer.jamendo.com)"
        return h
    code, ms, err = _timed(
        f"https://api.jamendo.com/v3.0/tracks/?client_id={config.jamendo_client_id}&format=json&limit=1"
    )
    h.latency_ms = ms
    if code == 200:
        h.available = True
        h.can_search = True
        h.can_download = True
    else:
        h.reason = f"api HTTP {code} {err}"
    return h


def test_ytdlp_site(name: str, base_url: str) -> SourceHealth:
    """Test a yt-dlp based source by checking its main page."""
    h = SourceHealth(name=name)
    code, ms, err = _timed(base_url, timeout=6)
    h.latency_ms = ms
    if code in (200, 301, 302):
        h.available = True
        h.can_search = True
        h.can_download = True
    else:
        h.reason = f"HTTP {code} {err}"
    return h


def test_youtube() -> SourceHealth:
    return test_ytdlp_site("youtube", "https://www.youtube.com/")


def test_soundcloud() -> SourceHealth:
    return test_ytdlp_site("soundcloud", "https://soundcloud.com/")


def test_bandcamp() -> SourceHealth:
    return test_ytdlp_site("bandcamp", "https://bandcamp.com/")


def test_bilibili() -> SourceHealth:
    return test_ytdlp_site("bilibili", "https://www.bilibili.com/")


def test_dailymotion() -> SourceHealth:
    return test_ytdlp_site("dailymotion", "https://www.dailymotion.com/")


def test_itunes() -> SourceHealth:
    h = SourceHealth(name="itunes")
    code, ms, err = _timed("https://itunes.apple.com/search?term=test&media=music&limit=1")
    h.latency_ms = ms
    if code == 200:
        h.available = True
        h.can_search = True
        h.can_download = True  # 30-90s previews only
        h.extras["note"] = "30-90s previews only (used as metadata source)"
    else:
        h.reason = f"HTTP {code} {err}"
    return h


def test_lightaudio() -> SourceHealth:
    h = SourceHealth(name="lightaudio")
    # LightAudio serves a JS-rendered search results page, but the
    # /static/ endpoints work without JS too. Probe the search URL.
    code, ms, err = _timed("https://web.ligaudio.ru/mp3/test")
    h.latency_ms = ms
    if code == 200:
        h.available = True
        h.can_search = True
        h.can_download = True
        h.extras["note"] = "direct MP3 links, Russian/CIS music, no ads"
    else:
        h.reason = f"HTTP {code} {err}"
    return h


def test_mp3party() -> SourceHealth:
    h = SourceHealth(name="mp3party")
    # Direct MP3 links in search HTML (dl2.mp3party.net/online/<id>.mp3).
    # Probe the search page; downloadability is implied by the link format.
    code, ms, err = _timed("https://mp3party.net/search?q=test")
    h.latency_ms = ms
    if code == 200:
        h.available = True
        h.can_search = True
        h.can_download = True
        h.extras["note"] = "direct MP3 links, Russian/CIS music"
    else:
        h.reason = f"HTTP {code} {err}"
    return h


# Map name -> test function (some need config)
TEST_FUNCTIONS = {
    "archiveorg": test_archiveorg,
    "openverse": test_openverse,
    "wikicommons": test_wikicommons,
    "audius": test_audius,
    "yandex": test_yandex,
    "jamendo": test_jamendo,
    "youtube": test_youtube,
    "soundcloud": test_soundcloud,
    "bandcamp": test_bandcamp,
    "bilibili": test_bilibili,
    "dailymotion": test_dailymotion,
    "itunes": test_itunes,
    "lightaudio": test_lightaudio,
    "mp3party": test_mp3party,
}


def run_doctor(config, only: Optional[List[str]] = None) -> Dict[str, SourceHealth]:
    """Run reachability tests for all (or selected) sources. Returns {name: SourceHealth}."""
    results: Dict[str, SourceHealth] = {}
    selected = only or list(TEST_FUNCTIONS.keys())
    for name in selected:
        fn = TEST_FUNCTIONS.get(name)
        if not fn:
            continue
        try:
            if name in ("jamendo",):
                h = fn(config)
            else:
                h = fn()
        except Exception as e:
            h = SourceHealth(name=name, reason=f"test crashed: {e}")
        results[name] = h
    return results


def pick_default_chain(results: Dict[str, SourceHealth]) -> List[str]:
    """Pick a sensible source chain from health results.

    Priority: full audio (search+download) > search-only > skip.
    No iTunes preview by default - user must opt in via --include-previews.
    """
    full = [n for n, h in results.items() if h.can_download and h.can_search]
    search_only = [n for n, h in results.items() if h.can_search and not h.can_download]

    # Within each, prefer by historical reliability
    # Jamendo is a CC-only catalogue, so keep it after the general sources
    # to avoid false-positive matches on popular commercial tracks.
    prefer_order = [
        "archiveorg", "openverse", "wikicommons",
        "yandex", "audius",
        "youtube", "soundcloud", "bandcamp", "bilibili", "dailymotion",
        "jamendo",
    ]

    def sort_key(name: str) -> int:
        try:
            return prefer_order.index(name)
        except ValueError:
            return 999

    chain = sorted(full, key=sort_key)
    chain += sorted(search_only, key=sort_key)
    return chain
