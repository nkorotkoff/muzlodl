"""ID3 metadata embedding with cover art."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def _download_cover(url: str, timeout: int = 10) -> Optional[bytes]:
    if not url:
        return None
    try:
        import requests
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.content
    except Exception as e:
        log.debug(f"cover download failed: {e}")
        return None


def embed(path: Path, artist: str, title: str, album: str = "",
          year: str = "", cover_url: str = "", track_no: int = 0) -> bool:
    """Write ID3 tags to an MP3. Returns True on success."""
    try:
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TRCK, APIC, error as ID3Error

        try:
            audio = MP3(path, ID3=ID3)
        except Exception:
            audio = MP3(path)

        if audio.tags is None:
            try:
                audio.add_tags()
            except ID3Error:
                pass

        audio.tags.add(TIT2(encoding=3, text=[title or ""]))
        audio.tags.add(TPE1(encoding=3, text=[artist or ""]))
        if album:
            audio.tags.add(TALB(encoding=3, text=[album]))
        if year:
            audio.tags.add(TDRC(encoding=3, text=[year]))
        if track_no:
            audio.tags.add(TRCK(encoding=3, text=[str(track_no)]))

        if cover_url:
            data = _download_cover(cover_url)
            if data:
                mime = "image/jpeg"
                if data[:8] == b"\x89PNG\r\n\x1a\n":
                    mime = "image/png"
                audio.tags.add(APIC(
                    encoding=3,
                    mime=mime,
                    type=3,
                    desc="Cover",
                    data=data,
                ))

        audio.save()
        return True
    except ImportError:
        log.warning("mutagen not installed, skipping tags")
        return False
    except Exception as e:
        log.warning(f"tag embed failed for {path.name}: {e}")
        return False
