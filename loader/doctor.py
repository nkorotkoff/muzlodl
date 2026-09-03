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


def test_yandex(config) -> SourceHealth:
    h = SourceHealth(name="yandex")
    if not config.yandex_token:
        # Anonymous yandex only serves 30s previews — useless for full
        # tracks (the pipeline's 60s floor would reject every download).
        # Keep it OUT of the recommended chain until a token is set,
        # same policy as jamendo without a client_id.
        h.reason = "YANDEX_TOKEN not set (anonymous: 30s previews only)"
        return h
    code, ms, err = _timed("https://music.yandex.ru/", timeout=5)
    h.latency_ms = ms
    if code in (200, 302):
        h.available = True
        h.can_search = True
        h.can_download = True  # full tracks with token + subscription
        h.extras["note"] = "token set: full tracks (requires subscription)"
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


def test_sleymp3() -> SourceHealth:
    h = SourceHealth(name="sleymp3")
    # /fsong is the server-rendered search page; audio-data="<id>" attrs
    # are what the AJAX resolver (/vkparser) needs to hand out MP3 urls.
    # Their presence means both search and download paths are live.
    code, ms, err = _timed("https://sleymp3.ru/fsong?q=test")
    h.latency_ms = ms
    if code == 200:
        h.available = True
        h.can_search = True
        h.can_download = True
        h.extras["note"] = "AJAX-resolved MP3 links, Russian/CIS music"
    else:
        h.reason = f"HTTP {code} {err}"
    return h


# Map name -> test function (some need config).
# Removed along with their registry builders (always fail, code deleted):
#   audius    — JSON parse errors, hosts down
#   openverse — 0 results, dead project
#   wikicommons — HTTP 403 since 2024
#   bilibili  — HTTP 412 Precondition Failed
# Doctor and the recommended chain must never list sources that
# `sources/registry.py` cannot build (the old state file recommended a
# 10-source chain of which 4 were dead — a doctor/registry desync).
TEST_FUNCTIONS = {
    "archiveorg": test_archiveorg,
    "yandex": test_yandex,
    "jamendo": test_jamendo,
    "youtube": test_youtube,
    "soundcloud": test_soundcloud,
    "bandcamp": test_bandcamp,
    "dailymotion": test_dailymotion,
    "itunes": test_itunes,
    "lightaudio": test_lightaudio,
    "mp3party": test_mp3party,
    "sleymp3": test_sleymp3,
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
            if name in ("jamendo", "yandex"):
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
    # Only sources that still exist in sources/registry.py are listed —
    # a dead source here would get recommended despite having no builder.
    prefer_order = [
        "archiveorg",
        "yandex",
        "youtube", "soundcloud", "bandcamp", "dailymotion",
        "jamendo",
        "sleymp3",
    ]

    def sort_key(name: str) -> int:
        try:
            return prefer_order.index(name)
        except ValueError:
            return 999

    chain = sorted(full, key=sort_key)
    chain += sorted(search_only, key=sort_key)
    return chain
