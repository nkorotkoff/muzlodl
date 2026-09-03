"""Source plugins for music discovery and downloading."""
from .base import Source, TrackInfo
from .ytdlp_based import YouTubeSource, BandcampSource, SoundCloudSource
from .ytdlp_extras import DailymotionSource
from .yandex import YandexMusicSource
from .jamendo import JamendoSource
from .archiveorg import ArchiveOrgSource
from .lightaudio import LightAudioSource
from .mp3party import MP3PartySource
from .sleymp3 import Sleymp3Source
from .itunes import ITunesEnricher, ITunesPreviewSource
from .musicbrainz import MusicBrainzEnricher
from .registry import default_sources, default_enrichers

__all__ = [
    "Source",
    "TrackInfo",
    "YouTubeSource",
    "BandcampSource",
    "SoundCloudSource",
    "DailymotionSource",
    "YandexMusicSource",
    "JamendoSource",
    "ArchiveOrgSource",
    "LightAudioSource",
    "MP3PartySource",
    "Sleymp3Source",
    "ITunesEnricher",
    "ITunesPreviewSource",
    "MusicBrainzEnricher",
    "default_sources",
    "default_enrichers",
]
