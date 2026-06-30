"""Source plugins for music discovery and downloading."""
from .base import Source, TrackInfo
from .ytdlp_based import YouTubeSource, BandcampSource, SoundCloudSource
from .ytdlp_extras import BilibiliSource, DailymotionSource
from .yandex import YandexMusicSource
from .jamendo import JamendoSource
from .audius import AudiusSource
from .archiveorg import ArchiveOrgSource
from .itunes import ITunesEnricher, ITunesPreviewSource
from .musicbrainz import MusicBrainzEnricher
from .openverse import OpenverseSource
from .wikicommons import WikimediaCommonsSource
from .registry import default_sources, default_enrichers

__all__ = [
    "Source",
    "TrackInfo",
    "YouTubeSource",
    "BandcampSource",
    "SoundCloudSource",
    "BilibiliSource",
    "DailymotionSource",
    "YandexMusicSource",
    "JamendoSource",
    "AudiusSource",
    "ArchiveOrgSource",
    "ITunesEnricher",
    "ITunesPreviewSource",
    "MusicBrainzEnricher",
    "OpenverseSource",
    "WikimediaCommonsSource",
    "default_sources",
    "default_enrichers",
]
