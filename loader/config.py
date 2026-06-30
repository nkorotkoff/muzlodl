"""Configuration: env vars and CLI overrides."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    # Output
    output_dir: str = "./library"

    # Source chain, in order. First working source wins per track.
    # itunes_preview is excluded by default — opt in via --include-previews.
    enabled_sources: List[str] = field(default_factory=lambda: [
        "archiveorg", "openverse", "wikicommons",
        "yandex", "audius", "bandcamp", "soundcloud", "youtube",
        "bilibili", "dailymotion", "jamendo",
    ])
    # Note: jamendo is deliberately last. It is a Creative Commons catalogue
    # and rarely contains the commercial/pop tracks most playlists ask for.
    # Putting it earlier caused it to "win" with low-quality/false matches.

    # Metadata enrichers (run before source chain, no audio).
    enabled_enrichers: List[str] = field(default_factory=lambda: [
        "itunes", "musicbrainz",
    ])

    # Per-enricher timeout in seconds. Skip if exceeded.
    enrich_timeout: int = 10

    # Quality
    quality: str = "320"  # kbps MP3

    # Behavior
    parallel: int = 1
    retries: int = 1
    skip_existing: bool = True
    min_match_score: float = 0.75  # 0.3/0.5/0.6 all let through fan-uploads with right title but wrong artist
    enrich: bool = True  # run enrichers before source chain
    max_candidates_per_source: int = 5  # try top-N candidates from each source

    # Streaming upload: push each track to the cloud the moment it
    # is downloaded. Pairs well with `delete_after_upload` so the local
    # library never grows past the current track-in-flight.
    upload_after_download: bool = False
    delete_after_upload: bool = False

    # Credentials (also read from env)
    yandex_token: str = ""
    jamendo_client_id: str = ""
    spotify_client_id: str = ""
    spotify_client_secret: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            yandex_token=os.environ.get("YANDEX_TOKEN", ""),
            jamendo_client_id=os.environ.get("JAMENDO_CLIENT_ID", ""),
            spotify_client_id=os.environ.get("SPOTIFY_CLIENT_ID", ""),
            spotify_client_secret=os.environ.get("SPOTIFY_CLIENT_SECRET", ""),
        )

    def merge(self, **overrides) -> "Config":
        for k, v in overrides.items():
            if v is not None and hasattr(self, k):
                setattr(self, k, v)
        return self
