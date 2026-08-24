"""yt-dlp based sources: YouTube, Bandcamp, SoundCloud.

All three share the same architecture: yt-dlp handles search (via the
`ytsearch5:` / `scsearch5:` / `bcsearch5:` prefixes) and download
(format=bestaudio, postprocessed to MP3 320k).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

from .base import Source, TrackInfo

log = logging.getLogger(__name__)


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

    def __init__(self, quality: str = "320", cookies_file: str = ""):
        self.quality = quality
        self.cookies_file = cookies_file

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
        # Parentheses/feat-clauses are YouTube operators, not search words:
        # "(feat. Ellie Goulding)" degrades results and invites bot-detection
        # garbage. Search on the bare words only.
        query = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", query)
        query = re.sub(r"\s+", " ", query).strip()
        url = f"{self.search_prefix}{query}"

        try:
            import yt_dlp
            opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "skip_download": True,
                "socket_timeout": 10,
                # Never cache search results: a rate-limited degraded
                # response ("one track repeated 5x") would otherwise be
                # served from cache forever.
                "cachedir": False,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if not info or "entries" not in info:
                return
            entries = [e for e in info["entries"] if e]
            if not entries:
                return

            # Score and collect candidates. Version filtering (covers,
            # remixes, clips, ...) happens in the pipeline's candidate_ok,
            # which knows the RAW requested title — a track explicitly
            # requested as "... (2016 Remaster)" must still match the
            # remastered copies. Here we only drop entries we cannot use.
            candidates = []
            for e in entries[:10]:
                s = _score_match(e, artist, title, album)
                u = e.get("webpage_url") or e.get("url") or ""
                e_title = e.get("title") or ""
                if u:
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
                    duration=entry.get("duration") or None,
                    match_score=score,
                    # raw_artist is intentionally EMPTY: on video platforms
                    # the "artist" is the uploader channel (VEVO, compilations,
                    # fan accounts), NOT the performing artist. Feeding it to
                    # candidate_ok would reject the exact track because the
                    # uploader name differs from the requested artist
                    # ("Мёртвые Дельфины" on a random fan channel). The
                    # pipeline's artist check then compares against the
                    # REQUESTED artist, which is always a pass here; wrong
                    # content is caught by title_matches, version markers and
                    # the duration check instead.
                    extra={
                        "id": entry.get("id"),
                        "raw_title": e_title,
                        "raw_artist": "",
                    },
                )
        except Exception as e:
            log.warning(f"[{self.name}] search failed: {e}")
            return

    def download(self, info: TrackInfo, output_path: Path) -> bool:
        if not info.url:
            return False
        # YouTube 403s are a rate-limit on this IP, not a permanent refusal:
        # wait out the block and retry a few times before giving up.
        last_err = None
        for attempt in range(4):
            try:
                import yt_dlp
                opts = {
                    "format": "bestaudio/best",
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "opus",
                            "preferredquality": self.quality,
                        }
                    ],
                    "outtmpl": str(output_path.with_suffix("")) + ".%(ext)s",
                    "quiet": True,
                    "no_warnings": True,
                    "noprogress": True,
                }
                if self.cookies_file:
                    opts["cookiefile"] = self.cookies_file
                if self.cookies_from_browser:
                    opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
                if self.extractor_args:
                    opts["extractor_args"] = self.extractor_args
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([info.url])
                return output_path.exists() and output_path.stat().st_size > 0
            except Exception as e:
                last_err = e
                msg = str(e)
                if "403" in msg and attempt < 3:
                    wait = 15 * (attempt + 1)
                    log.warning(
                        "[%s] 403 rate-limit on attempt %d, waiting %ds: %s",
                        self.name, attempt + 1, wait, msg[:100],
                    )
                    time.sleep(wait)
                    continue
                log.warning(f"[{self.name}] download failed: {e}")
                return False
        log.warning(f"[{self.name}] download failed: {last_err}")
        return False


class YouTubeSource(YTDLPBasedSource):
    name = "youtube"
    # 10 results, not 5: video clips, lyrics videos and slowed reuploads
    # eat the top slots on popular tracks; the plain audio upload is often
    # at position 3-8 and must survive candidate_ok.
    search_prefix = "ytsearch10:"
    # YouTube rate-limits anonymous IPs hard (HTTP 403 on download with the
    # default clients). The android client still serves downloadable streams
    # even when others return 403. IMPORTANT: pass ONLY android — when several
    # clients are listed yt-dlp may pick formats from a blocked client and the
    # whole download dies with 403.
    extractor_args = {
        "youtube": {"player_client": ["android"]},
    }

    # Search also rate-limits: under parallel load the API returns a
    # degraded one-track result repeated N times. Throttle searches
    # process-wide and retry once when the result is degenerate.
    _search_lock = threading.Lock()
    _last_search_ts = 0.0
    _SEARCH_MIN_INTERVAL = 3.0

    def search_iter(self, artist: str, title: str, album: str = "") -> Iterator[TrackInfo]:
        for attempt in range(2):
            with YouTubeSource._search_lock:
                dt = time.monotonic() - YouTubeSource._last_search_ts
                if dt < YouTubeSource._SEARCH_MIN_INTERVAL:
                    time.sleep(YouTubeSource._SEARCH_MIN_INTERVAL - dt)
                YouTubeSource._last_search_ts = time.monotonic()
                gen = super().search_iter(artist, title, album)
                cands = list(gen)
            # Degenerate? Rate-limited search returns the same video (or
            # same title) repeated N times. A real result set has variety.
            titles = {(c.extra or {}).get("raw_title") or c.title for c in cands}
            ids = {(c.extra or {}).get("id") or c.url for c in cands}
            if len(titles) >= 2 or len(ids) >= 2 or attempt == 1:
                yield from cands
                return
            log.info("[youtube] search returned a single repeated result, retrying")
            time.sleep(10 * (attempt + 1))


class BandcampSource(YTDLPBasedSource):
    name = "bandcamp"
    search_prefix = "bcsearch5:"


class SoundCloudSource(YTDLPBasedSource):
    name = "soundcloud"
    search_prefix = "scsearch5:"
