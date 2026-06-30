"""MusicBrainz + Cover Art Archive enricher.

Doesn't download audio. Used to fill in canonical metadata (album, year,
trackNo, cover) before the source chain runs, so source searches can be
more precise and the resulting ID3 tags are correct.

No API key required. MusicBrainz requires a User-Agent and rate-limits
to 1 req/sec; the enricher obeys both.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger(__name__)

UA = "music-loader/0.1 (personal-use, contact: noreply@example.com)"
MB_BASE = "https://musicbrainz.org/ws/2"
CAA_BASE = "https://coverartarchive.org"


class MusicBrainzEnricher:
    name = "musicbrainz"

    def __init__(self):
        import requests
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "application/json",
        })
        self._last_call = 0.0
        self._disabled = False  # set to True if MB is unreachable, to skip future calls

    def is_available(self) -> bool:
        return not self._disabled

    def _throttle(self):
        # MusicBrainz: max 1 req/sec
        now = time.time()
        wait = 1.05 - (now - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def enrich(self, artist: str, title: str) -> Optional[dict]:
        if not (artist and title):
            return None
        if self._disabled:
            return None
        try:
            self._throttle()
            query = f'recording:"{title}" AND artist:"{artist}"'
            r = self.session.get(
                f"{MB_BASE}/recording",
                params={"query": query, "fmt": "json", "limit": 5},
                timeout=8,
            )
            r.raise_for_status()
            recs = r.json().get("recordings") or []
            if not recs:
                return None
            rec = recs[0]

            credits = rec.get("artist-credit") or []
            mb_artist = credits[0].get("name") if credits else artist
            mb_title = rec.get("title") or title

            result = {
                "artist": mb_artist,
                "title": mb_title,
                "album": "",
                "year": "",
                "track_no": 0,
                "cover_url": "",
            }

            releases = rec.get("releases") or []
            release_id = ""
            for rel in releases:
                if rel.get("id"):
                    release_id = rel["id"]
                    result["album"] = rel.get("title") or ""
                    date = rel.get("date") or ""
                    if date:
                        result["year"] = date[:4]
                    break

            if release_id:
                try:
                    self._throttle()
                    r = self.session.get(
                        f"{CAA_BASE}/release/{release_id}/front-500",
                        timeout=6,
                        allow_redirects=False,
                    )
                    if r.status_code in (200, 302):
                        result["cover_url"] = r.headers.get("Location", "")
                except Exception as e:
                    log.debug(f"cover art fetch failed: {e}")

            return result
        except Exception as e:
            err = str(e).lower()
            if "timeout" in err or "connection" in err or "name resolution" in err:
                # Network is bad to MusicBrainz; stop trying for the rest of the run.
                self._disabled = True
                log.warning(f"[musicbrainz] unreachable, disabling for this run")
            else:
                log.warning(f"[musicbrainz] enrich failed: {e}")
            return None
