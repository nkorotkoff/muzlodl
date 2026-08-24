"""Internet Archive source. Public domain / CC music, full-length downloads.

No API key required. Uses the advancedsearch and metadata JSON APIs.

search_iter() yields top candidates in match-score order so the pipeline
can keep trying if a duration check fails (e.g. first result was a full
album, second one is the single track).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterator, List, Optional

from ..match import is_bad_version as _is_bad_version
from .base import Source, TrackInfo

log = logging.getLogger(__name__)

SEARCH_URL = "https://archive.org/advancedsearch.php"
META_URL = "https://archive.org/metadata/{id}/files"
DOWNLOAD_URL = "https://archive.org/download/{id}/{filename}"

# Bitrate floor: anything below this is rejected as a "lo-fi" rip
MIN_BITRATE_KBPS = 96

# Preferred formats in order: best audio quality first
PREFERRED_FORMATS = ["VBR MP3", "128Kbps MP3", "64Kbps MP3", "MPEG4", "M4A", "Ogg Vorbis", "128Kbps M4A"]


def _score_result(doc: dict, want_artist: str, want_title: str) -> float:
    title = (doc.get("title") or "").lower()
    raw_creator = doc.get("creator") or ""
    # creator can be a string or a list of strings on archive.org
    if isinstance(raw_creator, list):
        creator = " ".join(str(c) for c in raw_creator).lower()
    else:
        creator = str(raw_creator).lower()
    a = want_artist.lower()
    t = want_title.lower()
    title_s = 0.0
    if t and t in title:
        title_s = 1.0
    elif t:
        t_words = set(t.split())
        title_words = set(title.split())
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


def _bitrate_kbps(fmt: str) -> int:
    m = re.search(r"(\d+)\s*K(?:bps)?", fmt or "", re.IGNORECASE)
    return int(m.group(1)) if m else 0


class ArchiveOrgSource(Source):
    name = "archiveorg"

    def is_available(self) -> bool:
        return True

    def search(self, artist: str, title: str, album: str = "") -> Optional[TrackInfo]:
        for info in self.search_iter(artist, title, album):
            return info
        return None

    def search_iter(self, artist: str, title: str, album: str = "") -> Iterator[TrackInfo]:
        if not title:
            return
        items = self._search_items(artist, title, top_n=5)
        for doc, score in items:
            info = self._resolve_item(doc, artist, title, album, score)
            if info:
                yield info

    def _search_items(self, artist: str, title: str, top_n: int = 5) -> List:
        """Query the advancedsearch and return [(doc, score), ...] sorted."""
        try:
            import requests
            parts = [f'title:("{title}")']
            if artist:
                parts.append(f'creator:("{artist}")')
            parts.append('mediatype:(audio)')
            q = " AND ".join(parts)
            r = requests.get(
                SEARCH_URL,
                params={"q": q, "fl[]": ["identifier", "title", "creator", "year"],
                        "rows": top_n, "output": "json"},
                timeout=15,
            )
            r.raise_for_status()
            docs = (r.json().get("response") or {}).get("docs") or []
        except Exception as e:
            log.warning(f"[archiveorg] search failed: {e}")
            return []
        scored = []
        for d in docs:
            title_text = d.get("title", "") or ""
            if _is_bad_version(title_text, wanted_title=title):
                continue
            s = _score_result(d, artist, title)
            scored.append((d, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [(d, s) for d, s in scored if s >= 0.2]

    def _resolve_item(self, doc: dict, artist: str, title: str,
                      album: str, score: float) -> Optional[TrackInfo]:
        identifier = doc["identifier"]
        year = str(doc.get("year") or "")[:4] if doc.get("year") else ""
        raw_creator = doc.get("creator") or ""
        if isinstance(raw_creator, list):
            creator = " ".join(str(c) for c in raw_creator)
        else:
            creator = str(raw_creator)
        try:
            import requests
            r = requests.get(META_URL.format(id=identifier), timeout=15)
            r.raise_for_status()
            payload = r.json() or {}
            files = payload.get("result") if isinstance(payload, dict) else payload
            if not isinstance(files, list):
                return None
            audio_files = self._pick_audio_files(files)
            if not audio_files:
                return None
            chosen = audio_files[0]
            bitrate = _bitrate_kbps(chosen.get("format", ""))
            if bitrate and bitrate < MIN_BITRATE_KBPS:
                log.debug(f"[archiveorg] {identifier} too low bitrate ({bitrate}k), skipping")
                return None
            url = DOWNLOAD_URL.format(id=identifier, filename=chosen["name"])
            return TrackInfo(
                source=self.name,
                url=url,
                artist=creator or artist,
                title=doc.get("title") or title,
                album=album or "",
                year=year,
                duration=_parse_length(chosen.get("length")),
                match_score=score,
                extra={"identifier": identifier, "format": chosen.get("format", ""),
                       "bitrate_kbps": bitrate,
                       "raw_title": doc.get("title") or title,
                       "raw_artist": creator or artist},
            )
        except Exception as e:
            log.debug(f"[archiveorg] resolve {identifier} failed: {e}")
            return None

    @staticmethod
    def _pick_audio_files(files: List[dict]) -> List[dict]:
        audio = [f for f in files if not _is_thumb_or_meta(f.get("name", ""))]
        if not audio:
            return []
        mp3s = [f for f in audio if "MP3" in (f.get("format") or "")]
        if mp3s:
            mp3s.sort(key=lambda f: _bitrate_kbps(f.get("format", "")), reverse=True)
            return mp3s
        mp4s = [f for f in audio if "MPEG4" in (f.get("format") or "")]
        if mp4s:
            return mp4s
        return [f for f in audio if any(fmt in (f.get("format") or "")
                                        for fmt in PREFERRED_FORMATS)]

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
            log.warning(f"[archiveorg] download failed: {e}")
            return False


def _parse_length(s) -> float:
    if not s:
        return 0.0
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _is_thumb_or_meta(name: str) -> bool:
    n = name.lower()
    return any(tag in n for tag in (
        ".jpg", ".jpeg", ".png", "thumb", "tile", "gif", "txt", "xml",
        "__ia_", ".log", ".cue", ".m3u", "metadata",
    ))
