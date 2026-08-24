"""LightAudio.ru source — Russian music portal with direct MP3 downloads.

LightAudio (https://web.ligaudio.ru) is a Russian music search engine that
exposes direct MP3 links in its search results page. No JavaScript required,
no auth, no ad-wall — a perfect Tier 2 source for Russian/CIS music.

The search URL is just a slug-encoded query under /mp3/. Results are
rendered server-side; each result is an <a class="down"> with a direct
storage*.lightaudio.ru link and a title attribute in the form
"Скачать «Artist – Title»".

Resilient extraction:
- Multiple strategies to find the download link (a.down, a[href*='.mp3'], etc.)
- Multiple strategies to extract the title (title attr, aria-label, text)
- Storage host fallback: try storage4 → storage6 → https
- Artist/title matching uses the same Jaccard scoring as YouTube source
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import quote, unquote, urljoin

import requests
from bs4 import BeautifulSoup

from .base import Source, TrackInfo

log = logging.getLogger(__name__)


# Multiple storage hosts — try each if one fails
STORAGE_HOSTS = [
    "https://storage6.lightaudio.ru",
    "https://storage4.lightaudio.ru",
    "https://storage2.lightaudio.ru",
    "https://storage1.lightaudio.ru",
    "https://storage3.lightaudio.ru",
    "https://storage5.lightaudio.ru",
]


# Search URL templates — try in order until one works
SEARCH_URLS = [
    "https://web.ligaudio.ru/mp3/{query}",
    "https://www.lightaudio.ru/mp3/{query}",
]


# Tags/classes used to find the download link. Multiple to be resilient
# against layout changes.
LINK_SELECTORS = [
    "a.down",                  # canonical
    "a[href*='.mp3']",         # fallback
    "a[href*='lightaudio.ru']",  # by domain
]


# Tags/attrs used to extract the title
TITLE_ATTRS = ["title", "aria-label"]


def _norm(s: str) -> str:
    """Lowercase + collapse whitespace + remove quotes for matching."""
    if not s:
        return ""
    s = s.replace("—", "-").replace("–", "-").replace("«", "").replace("»", "")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _score_match(artist: str, title: str, candidate: str) -> float:
    """Score how well a candidate title matches the wanted artist+title.

    candidate looks like "Artist – Title" or "Artist - Title" or "Скачать «Artist – Title»".
    """
    from ..match import strip_markers

    cand = _norm(candidate)
    # Strip common prefixes like "Скачать «" and suffixes "»"
    cand = re.sub(r"^(скачать|download|слушать)\s*", "", cand)
    cand = re.sub(r"[\(\[][^\)\]]*[\)\]]\s*$", "", cand)  # strip trailing (Live), [Remix], etc.

    a = _norm(artist)
    t = _norm(title)
    # A requested title may carry a version marker ("Выше домов (ремастер)")
    # that candidates omit — strip markers so both still score 1.0.
    t_clean = strip_markers(t) or t

    if not cand or not a or not t:
        return 0.0

    # Direct substring match
    a_in = a in cand
    t_in = t_clean in cand or t in cand

    if a_in and t_in:
        return 1.0
    if t_in and not a_in:
        # title matches, artist may be abbreviated or alternate
        return 0.7
    if a_in and not t_in:
        return 0.5
    return 0.0


def _slugify(artist: str, title: str) -> str:
    """Build the direct-URL slug LightAudio uses for /mp3/{slug}.

    Format observed: lowercased, spaces between words preserved, " - "
    between artist and title (also URL-encoded as %20). Examples:
      - "Skeler - Eyes on Fire" → "skeler%20-%20eyes%20on%20fire"
      - "Кипелов - Реки времён" → "кипелов%20-%20реки%20времён"
    """
    raw = f"{artist} - {title}".strip()
    return raw.lower()


def _build_direct_urls(artist: str, title: str) -> list[str]:
    """Build direct-URL candidates from artist+title.

    LightAudio's search ranking is unreliable (returns wrong-artist
    matches even when the right track exists on the same site). But the
    direct URL /mp3/{slug} is more reliable — it surfaces the actual
    search-results page for that specific (artist, title) pair.
    """
    slug = _slugify(artist, title)
    encoded = quote(slug, safe="")
    return [
        f"https://web.ligaudio.ru/mp3/{encoded}",
        f"https://www.lightaudio.ru/mp3/{encoded}",
    ]


def _search_url(query: str) -> str:
    """Build the search URL (legacy fallback)."""
    return quote(query, safe="")


class LightAudioSource(Source):
    """Search and download from LightAudio.ru (Russian music, no ads, no auth)."""

    name = "lightaudio"

    def __init__(self, timeout: int = 30):
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
        })

    def is_available(self) -> bool:
        try:
            r = self._session.get(SEARCH_URLS[0].format(query="test"),
                                  timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def _fetch_search(self, artist: str, title: str) -> Optional[str]:
        """Try direct URL first, fall back to search.

        Direct URL (/mp3/{slug}) is more reliable than search — search
        ranking on LightAudio sometimes returns wrong-artist matches
        (e.g. Skeler → Darci) even when the right track exists on the
        site. The direct URL bypasses ranking and surfaces the actual
        page for that specific (artist, title) pair.
        """
        # 1. Try direct URL: /mp3/{artist-slug} - {title-slug}
        for url in _build_direct_urls(artist, title):
            try:
                r = self._session.get(url, timeout=self._timeout)
                if r.status_code == 200 and "mp3" in r.text:
                    return r.text
            except Exception as e:
                log.debug(f"lightaudio direct {url}: {e}")

        # 2. Fall back to search with the full query
        query = _slugify(artist, title)
        for tmpl in SEARCH_URLS:
            url = tmpl.format(query=quote(query))
            try:
                r = self._session.get(url, timeout=self._timeout)
                if r.status_code == 200 and "mp3" in r.text:
                    return r.text
            except Exception as e:
                log.debug(f"lightaudio search {url}: {e}")
        return None

    def _parse_results(self, html: str) -> list[tuple[str, str, str, float]]:
        """Parse search results.

        Returns [(mp3_url, title, slug_title, duration_seconds), ...].
        Duration comes from each item's <meta itemprop="duration">
        (ISO-8601, e.g. "PT1M17S") — needed because LightAudio lists
        several copies of the same track and the first one may be a
        clipped fragment (1:17 vs the real 3:44).
        """
        soup = BeautifulSoup(html, "lxml")
        results = []
        # Primary: per-item blocks with an explicit download link.
        for item in soup.select("div.item"):
            down = item.select_one("a.down[href]")
            if not down:
                continue
            href = down.get("href", "")
            if not href or ".mp3" not in href:
                continue
            title_el = item.select_one("span.title[itemprop='name']")
            title = title_el.get_text(strip=True) if title_el else ""
            # Combine with the item's own artist so _score_match can tell
            # originals apart from covers by other performers.
            autor_el = item.select_one("span.autor")
            autor = autor_el.get_text(strip=True) if autor_el else ""
            cand_title = f"{autor} – {title}" if autor and title else (title or autor)
            dur = 0.0
            meta = item.select_one("meta[itemprop='duration']")
            if meta and meta.get("content"):
                m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", meta["content"])
                if m:
                    h, mn, s = (int(g) if g else 0 for g in m.groups())
                    dur = h * 3600 + mn * 60 + s
            # Normalize URL: href is "//storage6.lightaudio.ru/dm/..."
            # i.e. scheme-relative, host is already in the path
            if href.startswith("//"):
                mp3_url = "https:" + href
            elif href.startswith("http"):
                mp3_url = href
            else:
                continue
            if cand_title:
                results.append((mp3_url, cand_title, cand_title, dur))
        if results:
            return results
        # Fallback: generic download links, no duration info.
        for sel in LINK_SELECTORS:
            for a in soup.select(sel):
                href = a.get("href", "")
                if not href or ".mp3" not in href:
                    continue
                # Extract title
                title = ""
                for attr in TITLE_ATTRS:
                    if a.get(attr):
                        title = a[attr]
                        break
                if not title:
                    title = a.get_text(strip=True)
                if not title:
                    continue
                if href.startswith("//"):
                    mp3_url = "https:" + href
                    results.append((mp3_url, title, title, 0.0))
                elif href.startswith("http"):
                    results.append((href, title, title, 0.0))
            if results:
                return results  # found via this selector
        return results

    def search(self, artist: str, title: str, album: str = "") -> Optional[TrackInfo]:
        for info in self.search_iter(artist, title, album):
            return info
        return None

    def search_iter(self, artist: str, title: str, album: str = "") -> Iterator[TrackInfo]:
        html = self._fetch_search(artist, title)
        if not html:
            return

        candidates = self._parse_results(html)
        # Score and sort. Among equal scores, prefer (1) an EXACT title
        # match — slowed/reverb/remix copies are longer than the original,
        # so raw duration sorting alone would pick those — and (2) the
        # LONGEST copy (LightAudio lists clipped fragments first, e.g.
        # 1:17 of a 3:44 track, and the pipeline takes the first candidate).
        scored = []
        for url, cand_title, _, duration in candidates:
            s = _score_match(artist, title, cand_title)
            if s >= 0.5:
                # Extract clean title from "Скачать «Artist – Title»" → "Title"
                clean = re.sub(r"^.*?[«\"](.*?)[»\"].*", r"\1", cand_title)
                if not clean or clean == cand_title:
                    clean = cand_title
                # Strip "Artist –" prefix
                clean = re.sub(r"^[^–\-]+[–\-]\s*", "", clean).strip()
                exact = 1 if _norm(clean) == _norm(title) else 0
                scored.append((s, exact, duration, url, clean, cand_title))
        scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))

        for s, _exact, dur, url, clean, raw_title in scored[:5]:
            # raw_artist: first segment of "Autor – Title" (or the raw
            # candidate string itself when no separator is present).
            raw_artist = ""
            if " – " in raw_title:
                raw_artist = raw_title.split(" – ", 1)[0].strip()
            yield TrackInfo(
                source=self.name,
                url=url,
                artist=artist,
                title=clean or title,
                album=album,
                duration=dur,  # from <meta itemprop="duration"> in HTML
                match_score=s,
                extra={"raw_title": raw_title, "raw_artist": raw_artist},
            )

    def download(self, info: TrackInfo, output_path) -> bool:
        url = info.url
        if not url:
            return False
        output_path = Path(output_path)  # accept str or Path
        try:
            with self._session.get(url, stream=True, timeout=self._timeout) as r:
                r.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
            return output_path.exists() and output_path.stat().st_size > 0
        except Exception as e:
            log.warning(f"[lightaudio] download {url}: {e}")
            return False
