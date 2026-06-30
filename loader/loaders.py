"""Input loaders: CSV, JSON, plain text, Spotify URL."""
from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path
from typing import List

log = logging.getLogger(__name__)


def load_csv(path: Path) -> List[dict]:
    with open(path, encoding="utf-8") as f:
        return [dict(row) for row in csv.DictReader(f)]


def load_json(path: Path) -> List[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        for key in ("tracks", "items", "songs"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return [data]
    return data


def load_text(path: Path) -> List[dict]:
    tracks: List[dict] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^(.+?)\s*[-—–]\s*(.+)$", line)
            if not m:
                tracks.append({"title": line})
                continue
            artist, title = m.group(1).strip(), m.group(2).strip()
            # Handle "Artist - Artist - Title" (artist prefix in title)
            a_lower = artist.lower()
            t_lower = title.lower()
            for sep in (" - ", " — ", " – "):
                if t_lower.startswith(a_lower + sep):
                    title = title[len(artist) + len(sep):].strip()
                    break
            tracks.append({"artist": artist, "title": title})
    return tracks


def load_spotify(url: str) -> List[dict]:
    """Extract a track list from a Spotify playlist/album URL via spotdl."""
    try:
        from spotdl import Spotdl
        from spotdl.utils.config import get_config
        cfg = get_config()
        sp = Spotdl(
            client_id=cfg.get("client_id", ""),
            client_secret=cfg.get("client_secret", ""),
            user_auth=bool(cfg.get("user_auth", False)),
        )
        songs = sp.search([url])
        return [
            {
                "artist": s.artist,
                "title": s.name,
                "album": s.album_name,
                "duration": s.duration,
            }
            for s in songs
        ]
    except ImportError:
        log.error("spotdl not installed; pip install spotdl")
        return []
    except Exception as e:
        log.error(f"spotdl failed for {url}: {e}")
        return []


def load_input(source: str) -> List[dict]:
    """Auto-detect format and load a list of track dicts."""
    if source.startswith("http://") or source.startswith("https://"):
        if "spotify.com" in source:
            return load_spotify(source)
        raise ValueError(f"unsupported URL: {source}")

    p = Path(source)
    if not p.exists():
        raise FileNotFoundError(f"not found: {source}")

    suffix = p.suffix.lower()
    if suffix == ".csv":
        return load_csv(p)
    if suffix == ".json":
        return load_json(p)
    return load_text(p)
