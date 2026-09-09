"""Main pipeline: for each track, walk the source chain until one succeeds."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from .config import Config
from .metadata import embed
from .sources.base import Source, TrackInfo

log = logging.getLogger(__name__)


_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
AUDIO_EXTS = {".mp3", ".opus", ".m4a", ".flac", ".ogg", ".webm"}


def sanitize(name: str, max_len: int = 100) -> str:
    if not name:
        return ""
    cleaned = _INVALID.sub("_", name).strip(" ._")
    return cleaned[:max_len] or "_"


def _resolve_case(path: Path, name: str) -> str:
    """Match an existing directory case-insensitively.

    If a directory with the same name (differing only in case) already
    exists under `path`, return that directory's name (preserving its
    casing). Otherwise return `name` as-is.

    This prevents duplicate artist/album dirs when the enricher returns
    different casing for the same artist across tracks.
    """
    if not path.exists():
        return name
    try:
        lower = name.lower()
        for child in path.iterdir():
            if child.is_dir() and child.name.lower() == lower:
                return child.name
    except PermissionError:
        pass
    return name


def _shorten(s: str, max_len: int) -> str:
    """Truncate a string from the end, keeping it under max_len."""
    if len(s) <= max_len:
        return s
    return s[:max_len - 1] + "…"


def _output_paths(root: Path, artist: str, album: str, title: str,
                  max_path_len: int = 0):
    """Compute output directory and file path for a track.

    If max_path_len > 0, the relative path (artist/album/filename) is
    kept under that many characters by truncating from least-essential
    end: artist in filename → title → artist dir → album dir.
    """
    safe_artist = sanitize(artist) or "Unknown Artist"
    safe_album = sanitize(album) or "Singles"
    safe_title = sanitize(title, max_len=100)
    # Normalize casing to match existing directories, preventing
    # duplicates like "Bon Jovi" vs "bon jovi" when the enricher
    # returns different casing across tracks.
    safe_artist = _resolve_case(root, safe_artist)
    # Album is relative to the artist dir
    artist_path = root / safe_artist
    safe_album = _resolve_case(artist_path, safe_album)

    # Build filename: include artist for identification (redundant with
    # the dir, but convenient for search).
    if safe_artist and safe_artist != "Unknown Artist":
        filename = f"{safe_artist} - {safe_title}.opus"
    else:
        filename = f"{safe_title}.opus"

    # Shorten path if requested (Android MTP compat)
    if max_path_len > 0:
        rel = len(safe_artist) + 1 + len(safe_album) + 1 + len(filename)
        if rel > max_path_len:
            # 1. Remove artist from filename (redundant with parent dir)
            filename = f"{safe_title}.opus"
            rel = len(safe_artist) + 1 + len(safe_album) + 1 + len(filename)
        if rel > max_path_len:
            # 2. Truncate title in filename
            excess = rel - max_path_len
            max_t = max(10, len(safe_title) - excess - 3)
            filename = f"{_shorten(safe_title, max_t)}.opus"
            rel = len(safe_artist) + 1 + len(safe_album) + 1 + len(filename)
        if rel > max_path_len:
            # 3. Trim longer directory name
            excess = rel - max_path_len
            if len(safe_artist) >= len(safe_album):
                safe_artist = _shorten(safe_artist, max(20, len(safe_artist) - excess))
            else:
                safe_album = _shorten(safe_album, max(10, len(safe_album) - excess))

    out_dir = root / safe_artist / safe_album
    out_path = out_dir / filename
    return out_dir, out_path


class Pipeline:
    def __init__(self, config: Config, sources: List[Source], enrichers: list = None,
                 cloud=None):
        self.config = config
        self.sources = sources
        self.enrichers = enrichers or []
        self.cloud = cloud  # optional CloudStorage for streaming upload
        self.root = Path(config.output_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_path = self.root / ".loader.log.jsonl"
        self._lock = threading.Lock()
        self.stats = {
            "total": 0, "success": 0, "failed": 0, "cached": 0,
            "by_source": {}, "uploaded": 0, "upload_fail": 0,
        }
        # One worker per source for the parallel candidate search.
        self._search_pool = ThreadPoolExecutor(
            max_workers=max(1, len(sources)),
            thread_name_prefix="search",
        )

    # ---------- public API ----------
    def process(self, tracks: List[dict], is_cancelled=None) -> dict:
        """Process tracks. `is_cancelled` is an optional callable returning
        True when the job should stop starting new downloads."""
        self.stats["total"] = len(tracks)
        log.info(
            "processing %d tracks, sources: %s, enrichers: %s",
            len(tracks),
            [s.name for s in self.sources] or "<none>",
            [e.name for e in self.enrichers] or "<none>",
        )

        if not self.sources:
            log.error("no sources available - check credentials/network")
            for t in tracks:
                self.stats["failed"] += 1
                self._log(t, "failed", None)
            return self.stats

        if self.config.parallel > 1:
            self._run_parallel(tracks, is_cancelled)
        else:
            for i, t in enumerate(tracks, 1):
                if is_cancelled and is_cancelled():
                    log.info("job cancelled at %d/%d, stopping", i, len(tracks))
                    break
                log.info("[%d/%d] %s - %s",
                         i, len(tracks),
                         t.get("artist", "?"), t.get("title", "?"))
                self._process_one(t)

        log.info("done: %d/%d ok, %d cached, %d failed",
                 self.stats["success"], self.stats["total"],
                 self.stats["cached"], self.stats["failed"])
        log.info("by source: %s", self.stats["by_source"])
        return self.stats

    def _enrich_track(self, t: dict) -> dict:
        """Enrich a single track: fill album/year/duration/cover and
        normalize artist/title from the enrichers.

        For artist, title, album: prefer the canonical iTunes form over the
        raw CSV value. The CSV may have "T.A.T.u" while iTunes normalizes
        to "t.A.T.u", and YouTube search is much more reliable with the
        canonical form. Originals are preserved as `raw_artist` / `raw_title`
        for logging.

        C: if iTunes and MusicBrainz both return a duration and they differ
        by >3%, the expected duration is unreliable — drop it and rely on
        consensus/ceilings only (don't pool it as a trusted expected).
        """
        wanted = {"album", "year", "duration", "cover_url"}
        # Fields where we always take the enricher's value if it has one
        override_keys = {"artist", "title", "album"}
        enriched = dict(t)
        # Keep originals around for logs
        enriched.setdefault("raw_artist", t.get("artist", ""))
        enriched.setdefault("raw_title", t.get("title", ""))
        enrichers_durations: dict[str, float] = {}
        for e in self.enrichers:
            try:
                result = e.enrich(t.get("artist", ""), t.get("title", ""))
            except Exception as ex:
                log.debug(f"[{e.name}] enrich error: {ex}")
                continue
            if not result:
                continue
            # Enrich-guard (shared): a versioned enricher title (remix /
            # instrumental / psychoacoustic ...) must never override the
            # CSV request — otherwise the request itself becomes the
            # crooked version. When the title is refused, its duration is
            # the WRONG version's length too — drop both, fall back to
            # consensus (ТАйМСКВЕР: iTunes "Psychoacoustic Version" 226s
            # vs plain 187s — the 229s live rip matched the wrong 226s).
            refused_versioned = False
            _rt = result.get("title")
            if isinstance(_rt, str) and _rt:
                from .match import is_bad_version as _is_bad_v0
                if _is_bad_v0(_rt, t.get("title", "") or ""):
                    refused_versioned = True
                    log.info("  enrich: refusing versioned title %r for %r",
                             _rt[:60], (t.get("title", "") or "")[:60])
            dur = result.get("duration")
            if isinstance(dur, (int, float)) and dur and dur > 0 and not refused_versioned:
                enrichers_durations[e.name] = float(dur)
            for k, v in result.items():
                if not v:
                    continue
                if k in override_keys:
                    if k == "title" and refused_versioned:
                        continue
                    if k == "duration" and refused_versioned:
                        continue
                    enriched[k] = v  # always prefer canonical
                elif not enriched.get(k):
                    if k == "duration" and refused_versioned:
                        continue
                    enriched[k] = v
            if all(enriched.get(k) for k in wanted):
                break
        # C: cross-check durations across enrichers
        if len(enrichers_durations) >= 2:
            vals = sorted(enrichers_durations.values())
            if vals[-1] / max(vals[0], 1) - 1 > 0.03:
                log.debug("enrich duration divergence %s, dropping expected duration",
                          enrichers_durations)
                enriched.pop("duration", None)
        return enriched

    def _enrich(self, tracks: List[dict]) -> List[dict]:
        """Enrich a whole batch up front (legacy/CLI path)."""
        results: List[Optional[dict]] = [None] * len(tracks)

        def _enrich_one(i: int, t: dict) -> None:
            results[i] = self._enrich_track(t)

        if self.config.parallel > 1:
            with ThreadPoolExecutor(max_workers=self.config.parallel) as ex:
                futs = [ex.submit(_enrich_one, i, t) for i, t in enumerate(tracks)]
                for f in as_completed(futs):
                    try:
                        f.result(timeout=max(1, self.config.enrich_timeout) * 2)
                    except Exception:
                        pass
        else:
            for i, t in enumerate(tracks):
                _enrich_one(i, t)
        return results  # type: ignore[return-value]

    # ---------- internals ----------
    def _search_source(self, src, artist, title, album) -> list:
        """One source's candidate search, isolated from its neighbour's
        failures (a dead source must not abort the whole track)."""
        try:
            return list(src.search_iter(artist, title, album))
        except Exception as e:
            log.debug(f"  {src.name}: search error: {e}")
            return []

    def _run_parallel(self, tracks: List[dict], is_cancelled=None):
        n = len(tracks)
        with ThreadPoolExecutor(max_workers=self.config.parallel) as ex:
            i = 0
            while i < n:
                if is_cancelled and is_cancelled():
                    log.info("job cancelled at %d/%d, stopping", i, n)
                    break
                # Submit one batch of max_workers at a time so a cancel
                # takes effect between batches, not after all tracks.
                batch = tracks[i:i + self.config.parallel]
                futs = [ex.submit(self._process_one, t) for t in batch]
                for f in futs:
                    try:
                        f.result()
                    except Exception as e:
                        log.error(f"task crashed: {e}")
                i += len(batch)

    def _process_one(self, track: dict) -> bool:
        # Enrich per track (metadata + expected duration), not for the
        # whole batch up front: downloads start immediately and iTunes
        # throttle/rate-limit work happens in parallel with downloading.
        if self.enrichers and self.config.enrich:
            track = self._enrich_track(track)

        artist = (track.get("artist") or "").strip()
        title = (track.get("title") or "").strip()
        album = (track.get("album") or "").strip()
        year = str(track.get("year") or "").strip()

        if not title:
            log.warning("skipping: no title: %r", track)
            return False

        out_dir, out_path = _output_paths(
            self.root, artist, album, title,
            max_path_len=self.config.max_path_len,
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        if self.config.skip_existing and out_path.exists() and out_path.stat().st_size > 0:
            log.info("  cached: %s", out_path.name)
            self.stats["cached"] += 1
            track["_file_path"] = str(out_path)
            track["_file_size"] = out_path.stat().st_size
            track["_duration"] = self._get_duration(out_path)
            self._log(track, "cached", None)
            return True

        # Dedup: if the same artist+title already exists anywhere in the
        # library (case-insensitive), skip to avoid duplicate downloads.
        if self.config.skip_existing:
            dup_path = self._has_duplicate(artist, title)
            if dup_path:
                log.info("  duplicate: %s - %s already in library, skipping", artist, title)
                self.stats["cached"] += 1
                track["_file_path"] = dup_path
                try:
                    track["_file_size"] = Path(dup_path).stat().st_size
                except OSError:
                    pass
                track["_duration"] = self._get_duration(Path(dup_path))
                self._log(track, "cached", None)
                return True

        from .match import candidate_ok, is_bad_version

        expected = track.get("duration")
        # When iTunes/MusicBrainz gave no expected duration (common for
        # Russian/obscure tracks), derive one from the candidates: the
        # duration shared by several copies is the real track length
        # (user rule: "same length across candidates = pick one of them").
        # Slowed/sped-up/remix copies distort the length, so exclude them.
        # The pipeline sets `expected` BEFORE searching, so compute it in
        # the first pass right after the search below.
        # Search ALL sources in parallel: sequential probing costs ~5-10s
        # per source per failed track (6 sources x 2-4s each = ~30s of pure
        # waiting). Downloads stay serial per track — sources rate-limit.
        cands_by_src: dict = {}
        if self.sources:
            futs = {
                self._search_pool.submit(self._search_source, src, artist, title, album): src
                for src in self.sources
            }
            for fut in as_completed(futs):
                cands_by_src[futs[fut]] = fut.result()

        # The WANTED title is the raw CSV value (pre-enrichment); version
        # markers inside it are part of the requested name.
        want_title = track.get("raw_title") or title
        want_artist = track.get("raw_artist") or artist

        if not expected:
            # Consensus duration: bucket candidate lengths (3s = ±1.5s
            # tolerance), exclude length-distorting versions, and take the
            # most frequent bucket — several copies agreeing on a length is
            # the real recording. A video clip has the same length as the
            # audio, so it votes the same way; slowed/pitch copies land in
            # their own sparse buckets.
            from .match import is_bad_version as _is_bad
            buckets: dict = {}
            for _src in self.sources:
                for info in cands_by_src.get(_src) or []:
                    if not info.duration or info.duration <= 0:
                        continue
                    raw = (info.extra or {}).get("raw_title") or info.title or ""
                    if _is_bad(raw, want_title):
                        continue
                    key = round(info.duration / 3.0)
                    buckets[key] = buckets.get(key, 0) + 1
            if buckets:
                best_key = max(buckets, key=buckets.get)
                if buckets[best_key] >= 2:
                    expected = best_key * 3.0
                    log.info("  consensus expected duration: %.0fs (%d copies)",
                             expected, buckets[best_key])
                else:
                    log.info("  no consensus duration (single copies), duration check disabled")
        # Merge candidates from ALL sources into one pool, then sort by
        # reliability:
        #   1. CONSENSUS — copies sharing the same duration are likely the
        #      same real recording (a slowed/pitch-shifted fake rarely
        #      matches the true length by chance). Several candidates with
        #      equal durations → pick one of them (user rule).
        #   2. proximity to the expected duration, when known;
        #   3. source match score.
        # A singleton with a unique length loses to a pair agreeing on a
        # length, so "Cold Carti - я сохраню" picks the 128s zaycev copies
        # over the lone 146s lightaudio pitch-shift.
        all_cands: list = []
        for src in self.sources:
            for info in cands_by_src.get(src) or []:
                all_cands.append((src, info))
        freq: dict = {}
        for _src, info in all_cands:
            if info.duration:
                key = round(info.duration / 3.0)  # ±1.5s tolerance bucket
                freq[key] = freq.get(key, 0) + 1

        # yt-dlp durations (youtube/soundcloud search metadata) lie often
        # (hour-long sets advertised as 3-min tracks) — trust them less.
        # Direct MP3 hosts (lightaudio/sleymp3/zaycev) report real lengths.
        _UNTRUSTED_DUR = {"youtube", "soundcloud"}
        _DUR_TRUST = 0.35

        def _rank(item) -> tuple:
            _src, info = item
            dur = info.duration or 0
            f = freq.get(round(dur / 3.0), 0) if dur else 0
            if expected and dur:
                near = abs(dur / expected - 1.0)
                if _src.name in _UNTRUSTED_DUR:
                    # Shrink toward "no info": untrusted durations only
                    # count 35% — a lying 3-min set can't outrank an
                    # honest direct-host copy anymore.
                    near = near * _DUR_TRUST + 0.5 * (1 - _DUR_TRUST)
            elif expected:
                near = float("inf")
            else:
                near = 0.0
            return (-f, near, -(info.match_score or 0.0))

        all_cands.sort(key=_rank)
        tried_by_src: dict = {}
        for src, info in all_cands:
                # Reject covers/remixes/live/clips/other non-canonical
                # versions before downloading anything — cheap, source-
                # agnostic gate (unless the requested title itself carries
                # the marker, e.g. a track actually named "... (Cover)").
                # Runs BEFORE the score gate: an exact title match with a
                # stray version marker in the request ("... (ремастер)")
                # scores low in source heuristics but is still the track.
                # The WANTED title is the raw CSV value (pre-enrichment):
                # enrichment normalizes "Another Day in Paradise - 2016
                # Remaster" to a bare title, which would wrongly reject
                # the remastered copies we explicitly asked for.
                extra = info.extra or {}
                cand_title = extra.get("raw_title") or info.title or ""
                cand_artist = extra.get("raw_artist") or info.artist or ""
                if not candidate_ok(want_artist, want_title, cand_title, cand_artist):
                    log.info("  %s: skipped candidate %r (version/cover/clip)",
                             src.name, cand_title[:60])
                    continue
                if info.match_score is not None and info.match_score < self.config.min_match_score:
                    continue
                tried_by_src[src.name] = tried_by_src.get(src.name, 0) + 1
                if tried_by_src[src.name] > self.config.max_candidates_per_source:
                    continue
                if self._try_candidate(track, src, info, out_path, expected):
                    return True

        # Last-resort fallback: tracks that exist on youtube ONLY as clips
        # ("Official Music Video") — the clip carries the same audio, and
        # many popular songs have no non-video upload. Video-form markers
        # are bypassed here; real version markers (live/remix/slowed/cover)
        # are NOT.
        from .match import is_video_only_version
        for src, info in all_cands:
            raw = (info.extra or {}).get("raw_title") or info.title or ""
            ra = (info.extra or {}).get("raw_artist") or ""
            if candidate_ok(want_artist, want_title, raw, ra):
                continue  # already tried above
            if not is_video_only_version(raw, want_title):
                continue
            if self._try_candidate(track, src, info, out_path, expected):
                return True

        for src in self.sources:
            if not tried_by_src.get(src.name):
                log.info("  %s: not found", src.name)

        log.error("  FAILED: %s - %s", artist or "?", title)
        self.stats["failed"] += 1
        self._log(track, "failed", None)
        return False

    @staticmethod
    def _head_size_ok(url: str, expected_seconds, timeout: int = 10) -> tuple[bool, str]:
        """HEAD Content-Length gate: skip hour-long sets before downloading.

        With expected ±8% and 128–320kbps, a real track is ~1–8MB. A HEAD
        reporting 30MB+ for a 3-min request is a set/compilation wearing
        the right title — skip without spending 20s downloading it.
        Unknown length (no header, chunked, HEAD refused) passes.
        """
        if not expected_seconds or expected_seconds <= 0:
            return True, ""
        try:
            import requests as _rq
            r = _rq.head(url, timeout=timeout, allow_redirects=True)
            if r.status_code >= 400:
                return True, ""
            cl = r.headers.get("Content-Length")
            if not cl:
                return True, ""
            size_mb = int(cl) / 1e6
            # 320kbps ceiling for expected*1.08, +2MB slack for covers/tags
            ceiling_mb = expected_seconds * 1.08 * 320 / 8 / 1000 + 2
            if size_mb > max(ceiling_mb, 12):
                return False, f"HEAD {size_mb:.0f}MB > {ceiling_mb:.0f}MB ceiling"
        except Exception:
            pass
        return True, ""

    def _try_candidate(self, track: dict, src, info, out_path: Path, expected) -> bool:
        """Download one candidate and validate it (duration). True = accepted."""
        # HEAD gate only for direct MP3 hosts (lightaudio/sleymp3/zaycev/
        # mp3party/archiveorg): yt-dlp URLs are pages/extractors, not files.
        if getattr(info, "url", "") and src.name not in ("youtube", "soundcloud"):
            ok, reason = self._head_size_ok(info.url, expected)
            if not ok:
                log.info("  %s: size gate (%s), next candidate", src.name, reason)
                return False
        for attempt in range(self.config.retries + 1):
            try:
                if src.download(info, out_path):
                    # Some sources (archive.org MP4, iTunes m4a) deliver
                    # audio inside a non-MP3 container. Transcode to MP3.
                    converted = self._transcode_to_mp3(out_path)
                    if info.is_preview:
                        log.info("  %s: got preview (degraded)", src.name)
                        return self._accept_track(track, info, out_path, src)
                    if not self._check_duration(out_path, expected_seconds=expected):
                        log.info("  %s: duration mismatch, next candidate", src.name)
                        try:
                            out_path.unlink()
                        except OSError:
                            pass
                        return False
                    return self._accept_track(track, info, out_path, src)
                log.info("  %s: download failed, trying next", src.name)
                return False
            except Exception as e:
                log.warning("  %s attempt %d: %s", src.name, attempt + 1, e)
                if attempt < self.config.retries:
                    time.sleep(2 ** attempt)
            except KeyboardInterrupt:
                raise
            except BaseException as e:
                log.warning("  %s crashed: %s", src.name, e)
                return False
        return False

    def _accept_track(self, track: dict, info, out_path: Path, src) -> bool:
        """Finalize a downloaded file: embed metadata, record stats, upload."""
        artist = (track.get("artist") or "").strip()
        title = (track.get("title") or "").strip()
        album = (track.get("album") or "").strip()
        year = str(track.get("year") or "").strip()
        # Tag sanity check BEFORE we overwrite tags: many sources stamp
        # TPE1/TIT2 into the raw file — if those tags are present and
        # strongly disagree with what we asked for, the source matched a
        # wrong track (e.g. a cover under a similar title). Cheap 0s check.
        ok, reason = self._verify_tags(out_path, artist, title)
        if not ok:
            log.info("  %s: tag mismatch (%s), next candidate", src.name, reason)
            try:
                out_path.unlink()
            except OSError:
                pass
            return False
        # AcoustID verification: fingerprint the file and check that the
        # actual sound matches what we asked for. Catches fan-uploads,
        # covers, live versions, and other wrong-content files.
        acoustid_decision = None
        if self.config.acoustid_verify and self.config.acoustid_api_key:
            ok, reason = self._verify_acoustid(out_path, artist, title)
            acoustid_decision = getattr(self, "_last_acoustid_decision", None)
            if not ok:
                log.info("  %s: acoustid mismatch (%s), next candidate", src.name, reason)
                try:
                    out_path.unlink()
                except OSError:
                    pass
                return False
            # B: unknown+tagless is the lone gate hole — harden it:
            # - unknown + tagged but weak → use 0.60 threshold (vs 0.35)
            # - unknown + tagless → PCM dynamics gate (silence/flat)
            if acoustid_decision == "unknown":
                tag_ok, tag_reason = self._verify_tags(out_path, artist, title, threshold=0.60)
                if not tag_ok:
                    log.info("  %s: unknown+tag mismatch (%s), next candidate", src.name, tag_reason)
                    try:
                        out_path.unlink()
                    except OSError:
                        pass
                    return False
                pcm_gate_ok, pcm_reason = self._verify_pcm_dynamics(out_path)
                if not pcm_gate_ok:
                    log.info("  %s: unknown+pcm gate (%s), next candidate", src.name, pcm_reason)
                    try:
                        out_path.unlink()
                    except OSError:
                        pass
                    return False
        # Tag sanity for the non-acoustid or match/unknown-accepted path
        # keeps the original 0.35 threshold (cheap pre-embed gate).
        embed(out_path, artist, title,
              info.album or album,
              info.year or year,
              info.cover_url)
        with self._lock:
            self.stats["success"] += 1
            self.stats["by_source"][src.name] = (
                self.stats["by_source"].get(src.name, 0) + 1
            )
        log.info("  ok from %s", src.name)
        # Streaming upload: push to cloud the moment the track is
        # downloaded, optionally drop the local copy so disk usage stays
        # flat across a 1500-track run.
        if self.config.upload_after_download and self.cloud:
            album_name = track.get("album") or info.album or "Singles"
            if self.cloud.upload_single(out_path, artist, album_name, title):
                with self._lock:
                    self.stats["uploaded"] += 1
                if self.config.delete_after_upload:
                    try:
                        out_path.unlink()
                    except OSError:
                        pass
            else:
                with self._lock:
                    self.stats["upload_fail"] += 1
                log.warning("  keeping local copy: upload failed")
        track["_file_path"] = str(out_path)
        if out_path.exists():
            track["_file_size"] = out_path.stat().st_size
        track["_duration"] = self._get_duration(out_path)
        self._log(track, "ok", src.name)
        return True

    def _log(self, track: dict, status: str, source: Optional[str]):
        try:
            record = {
                "status": status,
                "source": source,
                "artist": track.get("artist"),
                "title": track.get("title"),
                "album": track.get("album"),
            }
            line = json.dumps(record, ensure_ascii=False) + "\n"
            with self._lock:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception as e:
            log.debug(f"log write failed: {e}")

    def _has_duplicate(self, artist: str, title: str) -> Optional[str]:
        """Find an existing file for the same artist+title (case-insensitive)
        anywhere in the library tree. Returns its path, or None.

        Used to skip duplicate downloads when the same song exists under
        a different album folder or different source naming.
        """
        if not title:
            return None
        try:
            title_l = title.strip().lower()
            artist_l = artist.strip().lower()
            if not self.root.exists():
                return None
            for artist_dir in self.root.iterdir():
                if not artist_dir.is_dir() or artist_dir.name.startswith("."):
                    continue
                if artist_l and artist_l != artist_dir.name.lower():
                    continue
                for album_dir in artist_dir.iterdir():
                    if not album_dir.is_dir():
                        continue
                    for f in album_dir.iterdir():
                        if not f.is_file():
                            continue
                        if f.suffix.lower() not in AUDIO_EXTS:
                            continue
                        if title_l in f.stem.lower():
                            return str(f)
        except (PermissionError, OSError):
            pass
        return None

    #: Files shorter than this are almost certainly previews or clipped
    #: fragments (iTunes previews are 30s, YouTube clip fragments can be
    #: 10-20s). Rejected regardless of what the enricher reported, so we
    #: don't depend on enrichment succeeding to catch them.
    MIN_ACCEPTABLE_DURATION = 60.0

    #: Without an expected duration (enricher silent / rate-limited) a
    #: file longer than this is very likely an album or compilation
    #: ("МУККА - Весна" 32min, "TOTO - Africa" 15min) rather than the
    #: track. With an expected duration the 0.92/1.08 ratio check
    #: handles it.
    MAX_ACCEPTABLE_DURATION = 600.0

    def _check_duration(self, path: Path, expected_seconds) -> bool:
        """Accept the file unless its length is wildly off from expected.

        Three independent guards:
        1. The file must actually be decodable audio (an error page saved
           as a "track" has no duration — reject, don't accept blindly).
        2. A hard floor: anything shorter than 60s is a preview/clip.
        3. If we have an expected duration (from iTunes/MusicBrainz) and
           the file is >1.08x or <0.92x that length (±8%), it's almost
           certainly a different recording (full album, short preview,
           live medley, or fan-edit). Without an expected duration, a hard
           ceiling of 10 minutes catches albums/compilations.

        The 0.92/1.08 band (±8%): fake uploads often carry the right
        title AND a plausible length but are a different recording
        (lightaudio "Para-dox - Последнее слово" came in at 255s vs the
        real 214s = ratio 1.19, inside the old 0.7/1.3 band). Studio
        versions of the same track differ by <2%; the extra margin
        absorbs VBR/stream length-estimation drift. Live cuts are
        rejected anyway by the version gate.
        """
        actual = self._get_duration(path)
        if not actual:
            log.info("    duration: file not decodable as audio, rejected")
            return False
        if actual < self.MIN_ACCEPTABLE_DURATION:
            log.info("    duration: %ds actual — too short (preview/clip), rejected",
                     int(actual))
            return False
        if not expected_seconds or expected_seconds <= 0:
            if actual > self.MAX_ACCEPTABLE_DURATION:
                log.info("    duration: %ds actual — too long (album/compilation?), rejected",
                         int(actual))
                return False
            return True
        ratio = actual / expected_seconds
        if ratio > 1.08 or ratio < 0.92:
            log.info("    duration: %ds actual vs %ds expected (ratio %.2fx, rejected)",
                     int(actual), int(expected_seconds), ratio)
            return False
        return True

    def _verify_tags(self, path: Path, expected_artist: str, expected_title: str, threshold: float | None = None) -> tuple[bool, str]:
        """Compare embedded TPE1/TIT2 (or Vorbis artist/title) against expected.

        If the raw download carries tags and they strongly disagree (score
        below threshold), treat as a wrong-track download and let the next
        candidate be tried. Files without tags or with weak disagreement
        pass (many RU mp3party/sleymp3 deliveries are tagless). The unknown
        branch uses a higher threshold (0.60) as per Sonnet's B fix.
        """
        _TAG_MISMATCH_THRESHOLD = threshold if threshold is not None else 0.35
        try:
            import mutagen
            from mutagen.id3 import ID3

            f = mutagen.File(str(path), easy=False)
            if f is None:
                return True, ""
            # Try ID3 (MP3-flavored) first, then generic mutagen easy tags
            # for FLAC/OGG/M4A/Vorbis.
            got_artist = ""
            got_title = ""
            try:
                id3 = ID3(str(path))
                tpe1 = id3.get("TPE1")
                tit2 = id3.get("TIT2")
                if tpe1 and tpe1.text:
                    got_artist = str(tpe1.text[0]).strip()
                if tit2 and tit2.text:
                    got_title = str(tit2.text[0]).strip()
            except Exception:
                pass
            if not got_artist or not got_title:
                # Fallback: mutagen easy/typed tags
                def _first(tag, keys):
                    for k in keys:
                        v = tag.get(k)
                        if not v:
                            continue
                        s = (v[0] if isinstance(v, (list, tuple)) else v)
                        s = str(s).strip()
                        if s:
                            return s
                    return ""
                got_artist = got_artist or _first(f, ["artist", "TPE1", "\xa9ART", "Author"])
                got_title = got_title or _first(f, ["title", "TIT2", "\xa9nam", "Title"])
            if not got_artist and not got_title:
                return True, ""
            # Score the tag pair against what we asked for
            try:
                from .sources.base import score_match as _score

                score = _score(expected_artist, expected_title, got_artist, got_title)
            except Exception:
                return True, ""
            if score < _TAG_MISMATCH_THRESHOLD and max(len(got_artist), len(got_title)) >= 3:
                snippet = f"{got_artist} - {got_title}".strip(" -")[:80]
                return False, f"tags {snippet!r} (score {score:.2f})"
        except Exception:
            pass
        return True, ""

    _last_acoustid_decision: str | None = None

    def _verify_pcm_dynamics(self, path: Path) -> tuple[bool, str]:
        """Cheap silence / flat-dynamics gate for unknown+tagless (Sonnet).

        Re-uses the same PCM that verify_file decodes for AcoustID (audioread
        → mono 11025 Hz int16) — no scipy. Gates: RMS silence, and per-second
        RMS variance so a speech/ambient file of correct length but flat
        dynamics is rejected. Too-short files pass (not enough windows).
        """
        try:
            from .verifier import _decode_to_pcm
        except Exception:
            return True, ""
        try:
            pcm_bytes, sr, _ch, _dur = _decode_to_pcm(path)
        except Exception:
            return True, ""
        try:
            import numpy as np

            pcm = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
            if pcm.size == 0:
                return False, "silence (empty)"
            rms = float(np.sqrt(np.mean(pcm * pcm)))
            if rms < 200:  # int16 scale
                return False, f"silence (rms {rms:.0f})"
            win = sr
            windows = [pcm[i : i + win] for i in range(0, len(pcm) - win, win)]
            if len(windows) < 5:
                return True, "too_short_to_judge"
            rms_windows = [float(np.sqrt(np.mean(w * w))) for w in windows]
            mean = float(np.mean(rms_windows)) + 1e-6
            variance_ratio = float(np.std(rms_windows) / mean)
            if variance_ratio < 0.08:
                return False, f"flat dynamics (var {variance_ratio:.3f})"
        except Exception:
            return True, ""
        return True, ""

    def _verify_acoustid(self, path: Path, expected_artist: str, expected_title: str) -> tuple[bool, str]:
        """Fingerprint an audio file and check it matches what we asked for.

        Returns (matches, reason). `matches=False` means the actual sound
        doesn't match expected artist/title (e.g. wrong song under right
        title). On any error (decode, API, missing in DB), returns True
        so we don't false-positive-fail downloads.
        """
        try:
            from .verifier import verify_file
        except ImportError:
            self._last_acoustid_decision = None
            return True, "verifier not available"
        try:
            result = verify_file(
                path, self.config.acoustid_api_key,
                min_score=self.config.acoustid_min_score,
                expected=(expected_artist, expected_title),
            )
        except Exception as e:
            self._last_acoustid_decision = None
            return True, f"verify error: {e}"

        self._last_acoustid_decision = result.decision
        if result.decision == "mismatch":
            return False, f"got {result.found_artist} - {result.found_title} (score {result.acoustid_score:.2f})"
        # match, unknown, or preview → accept (we don't know better)
        if result.decision == "preview":
            return False, f"looks like a {result.file_duration:.0f}s preview"
        return True, ""

    @staticmethod
    def _get_duration(path: Path) -> float:
        """Read duration from a file. Works for MP3, M4A, MP4, etc."""
        try:
            from mutagen.mp3 import MP3
            return MP3(str(path)).info.length
        except Exception:
            pass
        try:
            import mutagen
            f = mutagen.File(str(path))
            if f and f.info and getattr(f.info, "length", None):
                return f.info.length
        except Exception:
            pass
        return 0.0

    def _transcode_to_mp3(self, path: Path) -> bool:
        """If the file is not a real Opus/MP3, transcode it with ffmpeg.

        Returns True if transcoding happened. False if the file was
        already Opus/MP3 (or if ffmpeg is unavailable - the file is kept
        as-is in that case).
        """
        # Check if already Opus or MP3
        try:
            with open(path, "rb") as f:
                head = f.read(16)
            is_opus = head[:4] == b"OggS"
            is_mp3 = head[:3] == b"ID3" or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
            if is_opus or is_mp3:
                return False
        except OSError:
            return False

        # Try ffmpeg
        ffmpeg = self._find_ffmpeg()
        if not ffmpeg:
            log.warning("ffmpeg not found, file kept in original format")
            return False

        tmp_out = path.with_suffix(".transcoded.opus")
        try:
            import subprocess
            r = subprocess.run(
                [ffmpeg, "-y", "-i", str(path),
                 "-vn",  # no video
                 "-acodec", "libopus",
                 "-b:a", f"{self.config.quality}k",
                 "-ac", "2",
                 str(tmp_out)],
                capture_output=True, timeout=300,
            )
            if r.returncode != 0:
                log.debug("ffmpeg failed: %s", r.stderr.decode()[-200:])
                if tmp_out.exists():
                    tmp_out.unlink()
                return False
            # Replace original with transcoded
            tmp_out.replace(path)
            return True
        except Exception as e:
            log.debug("transcode error: %e", e)
            if tmp_out.exists():
                tmp_out.unlink()
            return False

    @staticmethod
    def _find_ffmpeg() -> str:
        import shutil, os
        path = shutil.which("ffmpeg")
        if path:
            return path
        # Check the venv next to the project
        here = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "ffmpeg"
        if here.exists() and os.access(here, os.X_OK):
            return str(here)
        return ""
