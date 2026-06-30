"""Source registry: build the default source chain and enrichers from config."""
from __future__ import annotations

import logging
from typing import List

from .base import Source
from .ytdlp_based import YouTubeSource, BandcampSource, SoundCloudSource
from .ytdlp_extras import BilibiliSource, DailymotionSource
from .yandex import YandexMusicSource
from .jamendo import JamendoSource
from .audius import AudiusSource
from .archiveorg import ArchiveOrgSource
from .musicbrainz import MusicBrainzEnricher
from .itunes import ITunesEnricher, ITunesPreviewSource
from .openverse import OpenverseSource
from .wikicommons import WikimediaCommonsSource

log = logging.getLogger(__name__)


def default_sources(config, enabled: List[str] = None) -> List[Source]:
    """Build the audio source list, in fallback order, filtered by `enabled`."""
    enabled = enabled or config.enabled_sources
    enabled_set = set(s.lower() for s in enabled)

    builders = {
        "yandex": lambda: YandexMusicSource(config.yandex_token),
        "audius": lambda: AudiusSource(),
        "archiveorg": lambda: ArchiveOrgSource(),
        "openverse": lambda: OpenverseSource(),
        "wikicommons": lambda: WikimediaCommonsSource(),
        "jamendo": lambda: JamendoSource(config.jamendo_client_id)
        if config.jamendo_client_id else None,
        "bandcamp": lambda: BandcampSource(),
        "soundcloud": lambda: SoundCloudSource(),
        "youtube": lambda: YouTubeSource(),
        "bilibili": lambda: BilibiliSource(),
        "dailymotion": lambda: DailymotionSource(),
        "itunes_preview": lambda: ITunesPreviewSource(),
    }

    sources: List[Source] = []
    for name in enabled:
        builder = builders.get(name.lower())
        if not builder:
            continue
        try:
            src = builder()
        except Exception as e:
            log.debug(f"[registry] failed to build {name}: {e}")
            continue
        if src is not None:
            sources.append(src)

    available = [s for s in sources if s.is_available()]
    skipped = [s.name for s in sources if s not in available]
    if skipped:
        log.info(f"unavailable sources: {sorted(skipped)}")
    return available


def default_enrichers(config, enabled: List[str] = None) -> list:
    """Build the list of metadata enrichers (no audio, just metadata)."""
    enrichers = []
    enabled_set = set(s.lower() for s in (enabled or config.enabled_enrichers))
    if "itunes" in enabled_set:
        enrichers.append(ITunesEnricher())
    if "musicbrainz" in enabled_set:
        enrichers.append(MusicBrainzEnricher())
    return [e for e in enrichers if e.is_available()]
