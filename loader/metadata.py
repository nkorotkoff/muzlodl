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
    """Write metadata tags to an audio file. Returns True on success.

    Handles both MP3 (ID3) and Opus (Vorbis comment) — the pipeline
    produces either depending on the source (lightaudio/mp3party deliver
    MP3, youtube/soundcloud extract Opus via yt-dlp).
    """
    try:
        with open(path, "rb") as f:
            head = f.read(12)
        is_opus = head[:4] == b"OggS"
    except OSError:
        return False
    try:
        if is_opus:
            return _embed_opus(path, artist, title, album, year, cover_url, track_no)
        return _embed_mp3(path, artist, title, album, year, cover_url, track_no)
    except ImportError:
        log.warning("mutagen not installed, skipping tags")
        return False
    except Exception as e:
        log.warning(f"tag embed failed for {path.name}: {e}")
        return False


def _embed_mp3(path: Path, artist: str, title: str, album: str,
               year: str, cover_url: str, track_no: int) -> bool:
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


def _embed_opus(path: Path, artist: str, title: str, album: str,
                year: str, cover_url: str, track_no: int) -> bool:
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import VCommentDict

    audio = OggOpus(str(path))
    if audio.tags is None:
        audio.tags = VCommentDict()

    audio.tags["title"] = [title or ""]
    audio.tags["artist"] = [artist or ""]
    if album:
        audio.tags["album"] = [album]
    if year:
        audio.tags["date"] = [year]
    if track_no:
        audio.tags["tracknumber"] = [str(track_no)]
    if cover_url:
        data = _download_cover(cover_url)
        if data:
            mime = "image/jpeg"
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                mime = "image/png"
            audio.tags["metadata_block_picture"] = _picture_tag(mime, data)

    audio.save()
    return True


def _picture_tag(mime: str, data: bytes) -> str:
    """Build an Ogg Vorbis METADATA_BLOCK_PICTURE value."""
    import base64
    picture = {
        "type": 3,
        "mime": mime,
        "desc": "Cover",
        "data": data,
    }
    # Xiph FLAC picture structure
    payload = (
        (picture["type"]).to_bytes(4, "big")
        + len(picture["mime"]).to_bytes(4, "big") + picture["mime"].encode()
        + len(picture["desc"]).to_bytes(4, "big") + picture["desc"].encode()
        + (0).to_bytes(4, "big")      # width
        + (0).to_bytes(4, "big")      # height
        + (0).to_bytes(4, "big")      # depth
        + (0).to_bytes(4, "big")      # colors
        + len(picture["data"]).to_bytes(4, "big") + picture["data"]
    )
    return base64.b64encode(payload).decode()
