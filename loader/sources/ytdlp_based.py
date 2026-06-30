"""yt-dlp based sources: YouTube, Bandcamp, SoundCloud.

All three share the same architecture: yt-dlp handles search (via the
`ytsearch5:` / `scsearch5:` / `bcsearch5:` prefixes) and download
(format=bestaudio, postprocessed to MP3 320k).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from .base import Source, TrackInfo

log = logging.getLogger(__name__)


# Patterns that indicate a non-canonical version of a track. We reject
# results whose title matches any of these so the library stays clean
# (no live, remix, cover, instrumental, karaoke, etc.).
VERSION_REJECT_PATTERNS = [
    r"\blive\b",
    r"\bremix\b",
    r"\bcover\b",
    r"\binstrumental\b",
    r"\bacoustic\b",
    r"\bkaraoke\b",
    r"\bdemo\b",
    r"\bslowed\b",
    r"\bspeed[\s\-_]?up\b",
    r"\bbootleg\b",
    r"\bparody\b",
    r"\btribute\b",
    r"\bnightcore\b",
    r"\b8[\s\-_]?bit\b",
    r"\blo[\s\-_]?fi\b",
    r"\bsped up\b",
    r"\bslowed and reverb\b",
    r"\bchipmunks?\b",
    r"\bbass boosted\b",
    r"\bmashup\b",
    r"\bedit version\b",
    r"\bremastered mix\b",
    r"\b8d audio\b",
    r"\bremix edit\b",
    r"\bdj set\b",
    r"\bmegamix\b",
]


def _is_bad_version(title: str) -> bool:
    """Return True if the title indicates a non-canonical version."""
    if not title:
        return False
    t = title.lower()
    for pat in VERSION_REJECT_PATTERNS:
        if re.search(pat, t):
            return True
    return False


def _score_match(entry: dict, want_artist: str, want_title: str, want_album: str = "") -> float:
    """Heuristic 0..1 score: how well does an entry match the requested track."""
    e_title = (entry.get("title") or "").lower()
    e_artist = (entry.get("artist") or entry.get("uploader") or "").lower()
    a = (want_artist or "").lower()
    t = (want_title or "").lower()
    al = (want_album or "").lower()

    title_score = 0.0
    if t:
        # Strip common noise: "official video", "lyrics", "[HD]", "(remastered)" etc.
        t_clean = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", t).strip()
        e_clean = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", e_title).strip()
        if t == e_title or t_clean == e_clean:
            title_score = 1.0
        elif t in e_title or t_clean in e_clean or e_title in t:
            title_score = 0.8
        else:
            # word overlap
            t_words = set(t_clean.split())
            e_words = set(e_clean.split())
            if t_words and e_words:
                overlap = len(t_words & e_words) / max(len(t_words), 1)
                title_score = max(0.0, overlap - 0.2)

    artist_score = 0.0
    if a and e_artist:
        if a in e_artist or e_artist in a:
            artist_score = 1.0
        else:
            a_words = set(a.split())
            e_words = set(e_artist.split())
            if a_words and e_words:
                artist_score = len(a_words & e_words) / max(len(a_words), 1)

    album_score = 0.0
    if al and want_album:
        e_album = (entry.get("album") or "").lower()
        if e_album and (al in e_album or e_album in al):
            album_score = 1.0

    raw = 0.7 * title_score + 0.3 * artist_score
    # Cap the score when the artist barely matches. A YouTube video with
    # the right title but a different uploader (fan upload, cover, mashup
    # titled after the original) scores 0.7 and used to slip through
    # the 0.6 threshold. Capping at 0.5 here forces the caller to want
    # at least a partial artist match for a high score.
    if artist_score < 0.3:
        return min(raw, 0.5)
    return raw


class YTDLPBasedSource(Source):
    """Shared logic for sources that are powered by yt-dlp."""

    search_prefix: str = ""
    cookies_from_browser: Optional[str] = None
    extractor_args: dict = {}

    def __init__(self, quality: str = "320"):
        self.quality = quality

    def search(self, artist: str, title: str, album: str = "") -> Optional[TrackInfo]:
        for info in self.search_iter(artist, title, album):
            return info
        return None

    def search_iter(self, artist: str, title: str, album: str = "") -> Iterator[TrackInfo]:
        """Yield up to 5 candidate matches, best score first.

        Builds a query from artist + title, scores each candidate by
        word overlap with the requested artist/title, and yields them in
        order. The pipeline tries each one in turn until one downloads
        successfully, so partial results are recoverable.
        """
        if not self.search_prefix:
            return
        if not title:
            return

        query_parts = [p for p in (artist, title) if p]
        query = " ".join(query_parts)
        url = f"{self.search_prefix}{query}"

        try:
            import yt_dlp
            opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "skip_download": True,
                "socket_timeout": 10,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if not info or "entries" not in info:
                return
            entries = [e for e in info["entries"] if e]
            if not entries:
                return

            # Score and collect candidates
            candidates = []
            for e in entries[:10]:
                s = _score_match(e, artist, title, album)
                u = e.get("webpage_url") or e.get("url") or ""
                e_title = e.get("title") or ""
                if u and not _is_bad_version(e_title):
                    candidates.append((s, e, u))
            candidates.sort(key=lambda x: x[0], reverse=True)

            for score, entry, u in candidates[:5]:
                if score < 0.2:
                    continue
                yield TrackInfo(
                    source=self.name,
                    url=u,
                    artist=artist,
                    title=title,
                    album=album or entry.get("album", ""),
                    match_score=score,
                    extra={"id": entry.get("id")},
                )
        except Exception as e:
            log.warning(f"[{self.name}] search failed: {e}")
            return

    def download(self, info: TrackInfo, output_path: Path) -> bool:
        if not info.url:
            return False
        try:
            import yt_dlp
            opts = {
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": self.quality,
                    }
                ],
                "outtmpl": str(output_path.with_suffix("")) + ".%(ext)s",
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
            }
            if self.cookies_from_browser:
                opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
            if self.extractor_args:
                opts["extractor_args"] = self.extractor_args
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([info.url])
            return output_path.exists() and output_path.stat().st_size > 0
        except Exception as e:
            log.warning(f"[{self.name}] download failed: {e}")
            return False


class YouTubeSource(YTDLPBasedSource):
    name = "youtube"
    search_prefix = "ytsearch5:"


class BandcampSource(YTDLPBasedSource):
    name = "bandcamp"
    search_prefix = "bcsearch5:"


class SoundCloudSource(YTDLPBasedSource):
    name = "soundcloud"
    search_prefix = "scsearch5:"
