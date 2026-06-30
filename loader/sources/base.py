"""Base classes for music sources."""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

log = logging.getLogger(__name__)


_RE_PAREN = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_RE_NONWORD = re.compile(r"[^\w\s]")


def _norm(text: str) -> str:
    """Lowercase, strip parenthetical noise and non-word chars."""
    if not text:
        return ""
    t = _RE_PAREN.sub(" ", text)
    t = _RE_NONWORD.sub(" ", t)
    return " ".join(t.lower().split())


def score_match(
    want_artist: str,
    want_title: str,
    got_artist: str = "",
    got_title: str = "",
    want_album: str = "",
    got_album: str = "",
    album_weight: float = 0.1,
) -> float:
    """Return a 0..1 score for how well `got_*` matches the wanted track.

    The score rewards exact/title matches heavily, artist matches moderately,
    and album matches slightly. Partial substring matches are penalized so
    that e.g. 'Time' does not match 'Time After Time' with a high score.
    """
    a_want = _norm(want_artist)
    t_want = _norm(want_title)
    al_want = _norm(want_album) if want_album else ""
    a_got = _norm(got_artist)
    t_got = _norm(got_title)
    al_got = _norm(got_album)

    def _word_score(want: str, got: str) -> float:
        """Score based on word overlap. Exact word-set match is 1.0."""
        if not want or not got:
            return 0.0
        if want == got:
            return 1.0
        want_words = set(want.split())
        got_words = set(got.split())
        if not want_words or not got_words:
            return 0.0
        # Exact same set of words (order independent)
        if want_words == got_words:
            return 1.0
        # Jaccard-style: punish extra/missing words strongly
        overlap = len(want_words & got_words)
        union = len(want_words | got_words)
        return overlap / union if union else 0.0

    title_score = _word_score(t_want, t_got)
    artist_score = _word_score(a_want, a_got)
    album_score = _word_score(al_want, al_got)

    # Base: 70% title, 30% artist; album adds a small bonus capped at 1.0
    base = 0.7 * title_score + 0.3 * artist_score
    bonus = album_weight * album_score
    final = min(1.0, base + bonus)
    # Same guard as ytdlp_based: if the artist barely matches, cap the
    # score so fan-uploads with the right title but a different artist
    # can't slip past the min_match_score threshold.
    if artist_score < 0.3:
        return min(final, 0.5)
    return final


@dataclass
class TrackInfo:
    """Result of a source search. Carries enough info to download and tag."""
    source: str
    url: str
    artist: str = ""
    title: str = ""
    album: str = ""
    year: str = ""
    duration: float = 0.0
    cover_url: str = ""
    match_score: Optional[float] = None
    is_preview: bool = False  # True for 30-90s previews (skip duration check)
    extra: dict = field(default_factory=dict)


class Source(ABC):
    """Abstract source: search and download tracks."""
    name: str = "base"

    @abstractmethod
    def search(self, artist: str, title: str, album: str = "") -> Optional[TrackInfo]:
        """Search for a track. Return TrackInfo or None if not found."""
        raise NotImplementedError

    def search_iter(self, artist: str, title: str, album: str = "") -> Iterator[TrackInfo]:
        """Yield candidate results, best match first. Default: single result."""
        info = self.search(artist, title, album)
        if info:
            yield info

    @abstractmethod
    def download(self, info: TrackInfo, output_path: Path) -> bool:
        """Download the track to output_path. Return True on success."""
        raise NotImplementedError

    def is_available(self) -> bool:
        """Lightweight health check. Override in subclasses that need credentials."""
        return True

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name}>"
