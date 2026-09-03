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
    # Broken sources (audius JSON errors, wikicommons 403, bilibili 412,
    # openverse 0 hits, bandcamp 403, dailymotion bad search scheme,
    # jamendo needs a key, yandex needs the package + token for full tracks)
    # are removed from the default chain to keep doctor output and download
    # logs clean. Pass --sources explicitly to re-enable any of them.
    enabled_sources: List[str] = field(default_factory=lambda: [
        "youtube", "soundcloud", "lightaudio", "mp3party", "sleymp3", "zaycev", "archiveorg",
    ])

    # Metadata enrichers (run before source chain, no audio).
    enabled_enrichers: List[str] = field(default_factory=lambda: [
        "itunes", "musicbrainz",
    ])

    # Per-enricher timeout in seconds. Skip if exceeded.
    enrich_timeout: int = 10

    # Quality
    quality: str = "128"  # kbps — Opus 128k ≈ MP3 192k perceptually, much smaller

    # Behavior
    parallel: int = 1
    retries: int = 1
    skip_existing: bool = True
    min_match_score: float = 0.5  # exact-title candidates score 0.5 when the
    # youtube uploader is a channel, not the artist (VEVO, compilations).
    # Wrong-content files are caught downstream: candidate_ok version filter,
    # iTunes duration check, and title-match guard on enrich.
    enrich: bool = True  # run enrichers before source chain
    max_candidates_per_source: int = 5  # try top-N candidates from each source

    # AcoustID verification: after each download, fingerprint the file
    # and look it up in AcoustID. If the found artist/title don't match
    # what we asked for, treat the download as a mismatch and try the
    # next candidate. Requires an AcoustID API key.
    acoustid_verify: bool = False
    acoustid_api_key: str = ""
    acoustid_min_score: float = 0.5

    # Max relative path length (artist/album/filename). If > 0, paths
    # exceeding this limit are shortened for Android MTP compatibility.
    # Default 0 = no limit (keeps original names).
    max_path_len: int = 0

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
    # Cookies file (Netscape format) for yt-dlp sources. YouTube rate-limits
    # anonymous IPs hard (HTTP 403 on a share of downloads); an authenticated
    # cookies.txt removes most of that. Export from your browser
    # ("Get cookies.txt" extension) and point YT_COOKIES at it.
    yt_cookies: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            yandex_token=os.environ.get("YANDEX_TOKEN", ""),
            acoustid_api_key=os.environ.get("ACOUSTID_API_KEY", ""),
            jamendo_client_id=os.environ.get("JAMENDO_CLIENT_ID", ""),
            spotify_client_id=os.environ.get("SPOTIFY_CLIENT_ID", ""),
            spotify_client_secret=os.environ.get("SPOTIFY_CLIENT_SECRET", ""),
            yt_cookies=os.environ.get("YT_COOKIES", ""),
        )

    def merge(self, **overrides) -> "Config":
        for k, v in overrides.items():
            if v is not None and hasattr(self, k):
                setattr(self, k, v)
        return self
