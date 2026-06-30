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


def sanitize(name: str, max_len: int = 100) -> str:
    if not name:
        return ""
    cleaned = _INVALID.sub("_", name).strip(" ._")
    return cleaned[:max_len] or "_"


def _output_paths(root: Path, artist: str, album: str, title: str):
    safe_artist = sanitize(artist) or "Unknown Artist"
    safe_album = sanitize(album) or "Singles"
    safe_title = sanitize(title, max_len=100)
    # Filename includes artist so files are identifiable even when
    # grouped by album folder. Length-bounded to keep paths portable.
    if safe_artist and safe_artist != "Unknown Artist":
        filename = f"{safe_artist} - {safe_title}.mp3"
    else:
        filename = f"{safe_title}.mp3"
    # Cap the whole filename at ~200 chars for FAT/exFAT compatibility
    if len(filename) > 200:
        filename = filename[:196] + ".mp3"
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

    # ---------- public API ----------
    def process(self, tracks: List[dict]) -> dict:
        self.stats["total"] = len(tracks)
        log.info(
            "processing %d tracks, sources: %s, enrichers: %s",
            len(tracks),
            [s.name for s in self.sources] or "<none>",
            [e.name for e in self.enrichers] or "<none>",
        )

        if self.enrichers and self.config.enrich:
            tracks = self._enrich(tracks)

        if not self.sources:
            log.error("no sources available - check credentials/network")
            for t in tracks:
                self.stats["failed"] += 1
                self._log(t, "failed", None)
            return self.stats

        if self.config.parallel > 1:
            self._run_parallel(tracks)
        else:
            for i, t in enumerate(tracks, 1):
                log.info("[%d/%d] %s - %s",
                         i, len(tracks),
                         t.get("artist", "?"), t.get("title", "?"))
                self._process_one(t)

        log.info("done: %d/%d ok, %d cached, %d failed",
                 self.stats["success"], self.stats["total"],
                 self.stats["cached"], self.stats["failed"])
        log.info("by source: %s", self.stats["by_source"])
        return self.stats

    def _enrich(self, tracks: List[dict]) -> List[dict]:
        """Fill in missing metadata (album, year, duration, cover) from enrichers.

        Runs in parallel with per-track timeout so a slow enricher (e.g.
        MusicBrainz rate-limited or unreachable) doesn't bottleneck the queue.
        """
        wanted = {"album", "year", "duration", "cover_url"}
        timeout = max(1, self.config.enrich_timeout)
        results: List[Optional[dict]] = [None] * len(tracks)

        def _enrich_one(i: int, t: dict) -> None:
            enriched = dict(t)
            for e in self.enrichers:
                try:
                    result = e.enrich(t.get("artist", ""), t.get("title", ""))
                except Exception as ex:
                    log.debug(f"[{e.name}] enrich error: {ex}")
                    continue
                if not result:
                    continue
                for k, v in result.items():
                    if v and not enriched.get(k):
                        enriched[k] = v
                if all(enriched.get(k) for k in wanted):
                    break
            results[i] = enriched

        if self.config.parallel > 1:
            with ThreadPoolExecutor(max_workers=self.config.parallel) as ex:
                futs = [ex.submit(_enrich_one, i, t) for i, t in enumerate(tracks)]
                for f in as_completed(futs):
                    try:
                        f.result(timeout=timeout * 2)
                    except Exception:
                        pass
        else:
            for i, t in enumerate(tracks):
                _enrich_one(i, t)
        return results  # type: ignore[return-value]

    # ---------- internals ----------
    def _run_parallel(self, tracks: List[dict]):
        with ThreadPoolExecutor(max_workers=self.config.parallel) as ex:
            futs = {ex.submit(self._process_one, t): t for t in tracks}
            for f in as_completed(futs):
                try:
                    f.result()
                except Exception as e:
                    log.error(f"task crashed: {e}")

    def _process_one(self, track: dict) -> bool:
        artist = (track.get("artist") or "").strip()
        title = (track.get("title") or "").strip()
        album = (track.get("album") or "").strip()
        year = str(track.get("year") or "").strip()

        if not title:
            log.warning("skipping: no title: %r", track)
            return False

        out_dir, out_path = _output_paths(self.root, artist, album, title)
        out_dir.mkdir(parents=True, exist_ok=True)

        if self.config.skip_existing and out_path.exists() and out_path.stat().st_size > 0:
            log.info("  cached: %s", out_path.name)
            self.stats["cached"] += 1
            self._log(track, "cached", None)
            return True

        for src in self.sources:
            candidates_tried = 0
            for info in src.search_iter(artist, title, album):
                if info.match_score is not None and info.match_score < self.config.min_match_score:
                    continue
                candidates_tried += 1
                if candidates_tried > self.config.max_candidates_per_source:
                    break
                for attempt in range(self.config.retries + 1):
                    try:
                        if src.download(info, out_path):
                            # Some sources (archive.org MP4, iTunes m4a) deliver
                            # audio inside a non-MP3 container. Transcode to MP3.
                            converted = self._transcode_to_mp3(out_path)
                            if info.is_preview:
                                log.info("  %s: got preview (degraded)", src.name)
                            elif not self._check_duration(
                                out_path, expected_seconds=track.get("duration")
                            ):
                                log.info("  %s: duration mismatch, next candidate", src.name)
                                try:
                                    out_path.unlink()
                                except OSError:
                                    pass
                                break  # try next candidate from this source
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
                            # Streaming upload: push to cloud the moment
                            # the track is downloaded, optionally drop the
                            # local copy so disk usage stays flat across
                            # a 1500-track run.
                            if self.config.upload_after_download and self.cloud:
                                album_name = (track.get("album")
                                              or info.album or "Singles")
                                if self.cloud.upload_single(
                                    out_path, artist, album_name, title
                                ):
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
                            self._log(track, "ok", src.name)
                            return True
                        log.info("  %s: download failed, trying next", src.name)
                        break
                    except Exception as e:
                        log.warning("  %s attempt %d: %s", src.name, attempt + 1, e)
                        if attempt < self.config.retries:
                            time.sleep(2 ** attempt)
                    except KeyboardInterrupt:
                        raise
                    except BaseException as e:
                        log.warning("  %s crashed: %s", src.name, e)
                        break
            if candidates_tried == 0:
                log.info("  %s: not found", src.name)

        log.error("  FAILED: %s - %s", artist or "?", title)
        self.stats["failed"] += 1
        self._log(track, "failed", None)
        return False

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

    def _check_duration(self, path: Path, expected_seconds) -> bool:
        """Accept the file unless it is wildly longer or shorter than expected.

        If we have an expected duration (from iTunes/MusicBrainz) and the
        downloaded file is >2x or <0.4x that length, it's almost certainly
        a different recording (full album, short clip, or live medley).
        Reject and try the next source.
        """
        if not expected_seconds or expected_seconds <= 0:
            return True
        actual = self._get_duration(path)
        if not actual:
            return True
        ratio = actual / expected_seconds
        if ratio > 2.0 or ratio < 0.4:
            log.info("    duration: %ds actual vs %ds expected (ratio %.2fx, rejected)",
                     int(actual), int(expected_seconds), ratio)
            return False
        return True

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
        """If the file is not a real MP3, transcode it with ffmpeg.

        Returns True if transcoding happened. False if the file was
        already MP3 (or if ffmpeg is unavailable - the file is kept
        as-is in that case).
        """
        # Quick check: real MP3 starts with "ID3" tag or MPEG frame sync
        try:
            with open(path, "rb") as f:
                head = f.read(16)
            is_mp3 = head[:3] == b"ID3" or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
            if is_mp3:
                return False
        except OSError:
            return False

        # Try ffmpeg
        ffmpeg = self._find_ffmpeg()
        if not ffmpeg:
            log.warning("ffmpeg not found, file kept in original format")
            return False

        tmp_out = path.with_suffix(".transcoded.mp3")
        try:
            import subprocess
            r = subprocess.run(
                [ffmpeg, "-y", "-i", str(path),
                 "-vn",  # no video
                 "-acodec", "libmp3lame",
                 "-ab", f"{self.config.quality}k",
                 "-ac", "2", "-ar", "44100",
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
