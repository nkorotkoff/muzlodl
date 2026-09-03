"""Candidate validation shared by all sources.

A candidate (search result) is rejected when:
- its title carries a version marker (cover, remix, live, slowed,
  instrumental, "official video"/clip, ...) that is NOT part of the
  requested title — unless the user explicitly asked for that version
  (e.g. a track whose real name contains "cover" must still match);
- its title does not fully contain the requested title;
- its artist differs from the requested artist (when both are known).

This is the single gate the pipeline applies before downloading, on top
of each source's own scoring, so no source can slip a cover/clip/remix
into the library.
"""
from __future__ import annotations

import re

_RE_PAREN = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_RE_NONWORD = re.compile(r"[^\w\s]")


def _norm(text: str) -> str:
    """Lowercase, strip parenthetical noise and non-word chars."""
    if not text:
        return ""
    t = _RE_PAREN.sub(" ", text)
    # "Ke$ha" must normalize to "kesha": a $ inside a word is the
    # stylized spelling of the same artist name.
    t = t.replace("$", "s")
    t = _RE_NONWORD.sub(" ", t)
    t = t.lower()
    # "ft." and "feat." are the same word ("Outside ft. Ellie Goulding"
    # vs "Outside (feat. Ellie Goulding)"). Note: "\bft\.?\b" does NOT
    # swallow the dot (the trailing \b matches before it), so handle
    # "ft." and bare "ft" separately.
    t = re.sub(r"\bft\.", "feat", t)
    t = re.sub(r"\bft\b", "feat", t)
    return " ".join(t.split())

#: Markers of non-canonical versions / video-only uploads. A candidate
#: whose title matches any of these is skipped unless the same marker
#: appears in the requested title.
VERSION_MARKERS = [
    # Year + remaster must be stripped TOGETHER ("2016 Remaster" → gone,
    # leaving the bare title; stripping only "remaster" would leave "2016").
    # NOTE: remaster(?:ed)? — "remastered?" would be "remastere" + "d?".
    r"\b\d{4}\s*[-–—]?\s*remaster(?:ed)?\b",
    r"\bremaster(?:ed)?\s*\d{4}\b",
    r"\blive\b", r"\bremix(?:ed)?\b", r"\bcover\b", r"\binstrumental\b",
    r"\bacoustic\b", r"\bkaraoke\b", r"\bdemo\b", r"\bslowed\b",
    r"\bspeed[\s\-_]?up\b", r"\bbootleg\b", r"\bparody\b", r"\btribute\b",
    r"\bnightcore\b", r"\b8[\s\-_]?bit\b", r"\blo[\s\-_]?fi\b",
    r"\bsped up\b", r"\bsped[\s\-_]?up\b", r"\bslowed and reverb\b",
    r"\bchopped\b",
    r"\bscrewed\b", r"\bchipmunks?\b",
    r"\bbass boosted\b", r"\bmashup\b", r"\bedit version\b",
    r"\bremastered mix\b", r"\b8d audio\b", r"\bremix edit\b",
    r"\bdj set\b", r"\bmegamix\b", r"\bradio edit\b",
    r"\bremaster(?:ed)?\b", r"\bремастер\b",
    # Russian equivalents
    r"\bкавер\b", r"\bремикс\b", r"\bминус\b", r"\bкараоке\b",
    r"\bпеределанн\w*\b", r"\bпеределка\b", r"\bперепевк\w*\b",
    r"\bинструментал\b", r"\bвживую\b", r"\bконцерт\b",
    # Remix shorthands that skip the word "remix" entirely: RMX, VIP /
    # club / extended mixes, phonk retags ("Track (Drift Phonk)" is a
    # remix upload). The standalone genre words subsume the compound
    # forms ("club mix", "vip mix", "phonk mix", "drift phonk",
    # "extended mix") — no bare "\bmix\b" here, "Original Mix" (see
    # STRIP_ONLY_PATTERNS) must stay a non-version.
    r"\brmx\b", r"\brework\b", r"\bremake\b", r"\bflip\b", r"\bvip\b",
    r"\bextended(?:\s*mix)?\b", r"\bclub(?:\s*mix)?\b", r"\bversion\b",
    r"\bверсия\b", r"\bphonk\b", r"\bhyper\b", r"\bhyper[\s\-_]?phonk\b",
    r"\bdrift\b", r"\bhouse\b", r"\btrance\b",
    # Trap/bass genre retags ("Track (Trap Remix)", "(Bass Boosted)" —
    # subsumes "\bbass boosted\b" above) and upload edits ("Trap Edit").
    # The bare "\bedit\b" subsumes "radio edit"/"remix edit"/"edit
    # version" above. Still NO bare "\bmix\b": "Original Mix"
    # (STRIP_ONLY_PATTERNS) must stay a non-version — is_bad_version
    # checks only _MARKER_RE, so it never sees "original mix".
    r"\btrap\b", r"\bbass\b", r"\bedit\b",
    # Video-only uploads (clips with extra audio, lyric videos, rips)
    r"\bofficial video\b", r"\bofficial audio\b", r"\bmusic video\b",
    r"\blyric video\b", r"\blyrics?\b", r"\bклип\b", r"\bвидеоклип\b",
    r"\bвидео\b", r"\b4k\b", r"\b1080p\b", r"\b720p\b",
]

_MARKER_RE = [re.compile(p) for p in VERSION_MARKERS]

#: Video-form markers ("official video", "клип", ...) mark the FORM of an
#: upload, not a different recording — the clip carries the same audio as
#: the track. Used as a last-resort fallback for tracks that only exist on
#: youtube as clips (many popular songs). Version markers (remix/live/…)
#: are NEVER bypassed.
VIDEO_MARKERS = [
    r"\bofficial video\b", r"\bofficial music video\b", r"\bmusic video\b",
    r"\blyric video\b", r"\blyrics?\b", r"\bклип\b", r"\bвидеоклип\b",
    r"\bвидео\b", r"\b4k\b", r"\b1080p\b", r"\b720p\b",
]
_VIDEO_RE = [re.compile(p) for p in VIDEO_MARKERS]


def is_video_only_version(title: str, wanted_title: str = "") -> bool:
    """True if `title`'s ONLY version markers are video-form markers.

    "NEVERLOVE — ЛИСИЙ-КИСИЙ (Official Music Video)" is the track itself
    (clip form); "… (Live)" or "… (Remix)" are different recordings.
    """
    if not title:
        return False
    t = _soft_norm(title)
    if not any(rx.search(t) for rx in _VIDEO_RE):
        return False
    t_nv = t
    for rx in _VIDEO_RE:
        t_nv = rx.sub(" ", t_nv)
    w = _soft_norm(wanted_title)
    for rx in _MARKER_RE:
        if rx.search(t_nv) and not rx.search(w):
            return False  # a real version marker remains → not video-only
    return True

#: Technical noise in upload titles — bitrate/container tags ("MP3 320",
#: "320kbps", "flac") and site stamps. Unlike VERSION_MARKERS these are
#: NOT version signals (a track is still the track at 320kbps), so they
#: are only stripped from title-comparison residue, never used by
#: is_bad_version to reject a candidate.
NOISE_PATTERNS = [
    r"\bmp3\b", r"\bmp3uk\b", r"\bflac\b", r"\bwav\b", r"\bogg\b",
    r"\bkbps\b", r"\bkbit\b", r"\b\d{3}\b", r"\bmp4\b",
    # quality tags, not version signals ("[HQ]" audio uploads)
    r"\bhq\b", r"\bhd\b",
]
_NOISE_RE = [re.compile(p) for p in NOISE_PATTERNS]

#: Markers stripped from comparison residue but NOT version signals:
#: "Original Mix" is the canonical version (a "5 Centimeters Per Second
#: (Original Mix)" request must match a plain "[HQ]" upload). is_bad_version
#: must NOT treat it as a version.
STRIP_ONLY_PATTERNS = [
    r"\boriginal mix\b",
]
# High-frequency bare genre words ("trap", "bass", "edit") are version
# markers for is_bad_version ("Track (Trap Remix)" is not the track) but
# must NOT take part in strip-equality checks: strip_markers("Trap
# Queen") would leave "queen" and a plain "Queen" upload would pass
# title_matches as the same track. Keep them only in _MARKER_RE.
_STRIP_VERSION_MARKERS = tuple(
    p for p in VERSION_MARKERS
    if p not in (r"\btrap\b", r"\bbass\b", r"\bedit\b")
)
_STRIP_RE = (
    [re.compile(p) for p in _STRIP_VERSION_MARKERS]
    + _NOISE_RE
    + [re.compile(p) for p in STRIP_ONLY_PATTERNS]
)


def _soft_norm(text: str) -> str:
    """Lowercase + collapse whitespace, KEEPING parentheses.

    Version markers usually live inside parens ("Зло (Live)"), and the
    strict _norm() strips paren contents — which would hide the marker.
    """
    t = re.sub(r"\s+", " ", (text or "").lower()).strip()
    t = t.replace("$", "s")
    t = re.sub(r"\bft\.", "feat", t)
    t = re.sub(r"\bft\b", "feat", t)
    return t


def strip_markers(text: str) -> str:
    """Remove version markers from a normalized string.

    "выше домов ремастер" -> "выше домов", so a requested title that
    carries a marker can still match candidates without it.
    """
    if not text:
        return ""
    out = text
    for rx in _STRIP_RE:
        out = rx.sub(" ", out)
    # Parens may be left empty by marker removal ("выше домов ( )");
    # drop them too so the result is comparable to plain titles.
    out = re.sub(r"[\(\)\[\]]", " ", out)
    # A marker removal can leave a dangling separator ("... - " after
    # "2016 Remaster" is stripped from "... - 2016 Remaster").
    out = re.sub(r"[\s\-–—]+$", "", out)
    return re.sub(r"\s+", " ", out).strip()


def is_bad_version(title: str, wanted_title: str = "") -> bool:
    """True if `title` signals a non-canonical version.

    Markers already present in `wanted_title` are allowed, so a track
    whose real name contains "cover"/"live" still matches.
    """
    if not title:
        return False
    t = _soft_norm(title)
    w = _soft_norm(wanted_title)
    for rx in _MARKER_RE:
        if rx.search(t) and not rx.search(w):
            return True
    return False


def title_matches(want_title: str, cand_title: str) -> bool:
    """True if the candidate is the same track as requested.

    The candidate must equal the requested title, optionally with noise
    stripped by _norm (parens/punctuation). Anything more — a different
    recording like "Time After Time" for "Time" — is rejected here.
    """
    w = _norm(want_title)
    c = _norm(cand_title)
    if not w:
        return True
    # Strip technical noise ("audio", "320", "mp3") from BOTH sides so
    # "Outside [Audio] ft. X" matches "Outside (feat. X)".
    for rx in _NOISE_RE:
        w = rx.sub(" ", w)
        c = rx.sub(" ", c)
    w = re.sub(r"\s+", " ", w).strip()
    c = re.sub(r"\s+", " ", c).strip()
    if not w:
        return True
    if w == c:
        return True
    # Whole-title substring with word boundaries ("Зло" not "Злость").
    m = re.search(rf"(?<!\w){re.escape(w)}(?!\w)", c)
    if m:
        # Whatever follows the matched title must be version markers only
        # ("Time - Official Video" is still Time; the video marker is then
        # rejected by is_bad_version). Any other words mean a different
        # track ("Time" vs "Time After Time").
        rest = c[m.end():].strip()
        if not rest:
            return True
        for rx in _STRIP_RE:
            rest = rx.sub(" ", rest)
        if _norm(rest) == "":
            return True
    # The REQUESTED title may carry a marker the candidate doesn't
    # ("Выше домов (ремастер)" vs "Выше домов"): compare marker-stripped.
    w_clean = strip_markers(w)
    if w_clean and w_clean != w:
        if w_clean == c or re.search(rf"(?<!\w){re.escape(w_clean)}(?!\w)", c) is not None:
            return True
        # fall through: softer branches below may still match
    # The REQUESTED title may carry a parenthetical clause ("Outside
    # (feat. Ellie Goulding)") that _norm dropped entirely. Re-add it:
    # the candidate "Outside [Audio] ft. Ellie Goulding" is the same track.
    w_soft = _soft_norm(want_title)
    if w_soft and ("(" in w_soft or "[" in w_soft):
        wf = re.sub(r"[\(\)\[\]]", " ", w_soft)
        wf = _RE_NONWORD.sub(" ", wf)  # drop "feat." → "feat" etc.
        for rx in _NOISE_RE:
            wf = rx.sub(" ", wf)
        wf = re.sub(r"\s+", " ", wf).strip()
        if wf and wf != w:
            if wf == c or re.search(rf"(?<!\w){re.escape(wf)}(?!\w)", c):
                return True
    # Version markers may sit in parens on EITHER side ("SAVAGE (Skeler
    # Remix)" vs "SAVAGE - Skeler remix"). _norm drops parens entirely,
    # so compare marker+noise-stripped soft forms, with a rest check so
    # "Time" still can't match "Time After Time".
    w_s = strip_markers(_soft_norm(want_title))
    c_s = strip_markers(_soft_norm(cand_title))
    # strip_markers leaves internal "- " separators; collapse them so
    # "savage - skeler" == "savage skeler".
    w_s = re.sub(r"[\s\-–—]+", " ", w_s).strip()
    c_s = re.sub(r"[\s\-–—]+", " ", c_s).strip()
    if w_s and c_s and w_s != w:
        if w_s == c_s:
            return True
        m3 = re.search(rf"(?<!\w){re.escape(w_s)}(?!\w)", c_s)
        if m3:
            rest3 = c_s[m3.end():].strip()
            if not rest3:
                return True
            for rx in _STRIP_RE:
                rest3 = rx.sub(" ", rest3)
            if _norm(rest3) == "":
                return True
    return False


def candidate_ok(
    want_artist: str,
    want_title: str,
    cand_title: str,
    cand_artist: str = "",
) -> bool:
    """Full gate: version markers, title containment, artist match."""
    if not want_title:
        return True
    # Video platforms title uploads as "Artist - Title" (or "Artist: Title").
    # The leading artist is not part of the track name — strip it so
    # "Icona Pop - I Love It (feat. Charli XCX)" still matches the request.
    cand = _soft_norm(cand_title)
    # Track-number prefix on uploads: "2. Ke$ha – Die Young" is the track
    # with a leading index, not a different song.
    cand = re.sub(r"^\d+\s*[\.\-–—]\s*", "", cand)
    want_n = _soft_norm(want_artist)
    if want_n:
        # Multi-artist requests ("A, B - Title") often appear in the
        # candidate as just the first artist ("A - Title"): try each
        # collaborator as a prefix, longest first.
        prefixes = [p for p in re.split(r"[,&+]|\bfeat\b", want_n) if p.strip()]
        prefixes = sorted(set(p.strip() for p in prefixes), key=len, reverse=True)
        stripped = False
        for p in prefixes:
            for sep in (" - ", " – ", " — ", ": "):
                if cand.startswith(p + sep):
                    cand = cand[len(p) + len(sep):]
                    stripped = True
                    break
            if stripped:
                break
        if not stripped:
            # Bare "Artist Title" prefix (archive.org style). Strip it ONLY
            # when the remainder still matches — otherwise "Time" would
            # match "Time After Time" via the artist trick.
            for p in prefixes:
                if cand.startswith(p + " "):
                    rest = cand[len(p) + 1:]
                    if title_matches(want_title, rest):
                        cand = rest
                    break
    if not title_matches(want_title, cand):
        return False
    # A cover marker on a candidate that IS the requested artist is the
    # track itself: "Seether - Careless Whisper (George Michael Cover)" is
    # Seether covering Careless Whisper — exactly what was asked for.
    # Covers by OTHER artists stay rejected. A "cover" word in the ARTIST
    # name means a cover channel ("УВУЛА (Cover шАг)") — never the artist.
    want_n = _norm(want_artist)
    # Check version markers on the artist-stripped title, NOT the raw
    # "Artist - Title" upload string: an artist whose name contains a
    # marker word ("Ivan House", "…Live") would otherwise reject its own
    # canonical tracks. `cand` already had the artist prefix removed.
    cand_v = cand
    # _norm strips parens; a "cover" inside them ("УВУЛА (Cover шАг)")
    # would vanish — use the paren-preserving form.
    cover_in_artist = bool(re.search(r"\bcover\b|\bкавер\b", _soft_norm(cand_artist)))
    if want_n and not cover_in_artist and (
        want_n in _norm(cand_title) or want_n in _norm(cand_artist)
    ):
        cand_v = re.sub(r"\bcover\b|\bкавер\b", " ", cand_v, flags=re.I)
    if is_bad_version(cand_v, want_title):
        return False
    if want_artist and cand_artist:
        wa = _norm(want_artist)
        ca = _norm(cand_artist)
        # Artist matches if exactly equal, or listed among collaborators
        # ("Электрофорез,Ash Code" contains "Электрофорез"). Substring
        # containment alone is too loose ("Римас (Сироткин)" would pass).
        # Split the RAW string — _norm would flatten the commas away.
        listed = {_norm(p.strip()) for p in cand_artist.split(",") if p.strip()}
        if wa != ca and wa not in listed:
            return False
    return True
