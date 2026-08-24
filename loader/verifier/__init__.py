"""Audio fingerprinting + AcoustID verification.

Compares the actual audio content of files in the library against the
AcoustID/MusicBrainz database, so we can find tracks where the metadata
matches but the sound is wrong (fan-uploads, covers, live versions,
30-second previews, etc.).

Requires:
- libchromaprint.so.1 in ~/.local/bin/ (built from chromaprint 1.5.1)
- PYTHONPATH includes our local python_startup dir (loads .so via ctypes)
- An AcoustID API key (free, register at https://acoustid.org/new-application)
"""
from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

_LIB = None


def _load_lib():
    global _LIB
    if _LIB is not None:
        return _LIB
    p = os.path.expanduser("~/.local/bin/libchromaprint.so.1")
    if not os.path.exists(p):
        raise RuntimeError(f"libchromaprint.so.1 not found at {p}")
    _LIB = ctypes.CDLL(p, mode=ctypes.RTLD_GLOBAL)
    _LIB.chromaprint_get_version.restype = ctypes.c_char_p
    return _LIB


@dataclass
class VerificationResult:
    path: Path
    expected: tuple[str, str]  # (artist, title) from filename
    matched: bool
    acoustid_score: float  # 0.0..1.0; 0 if no match
    found_artist: str = ""
    found_title: str = ""
    found_duration: float = 0.0  # in seconds
    recording_id: str = ""  # MusicBrainz recording ID
    decision: str = ""  # "match" / "mismatch" / "unknown" / "preview"
    reason: str = ""
    file_duration: float = 0.0


def _decode_to_pcm(path: Path) -> tuple[bytes, int, int, float]:
    """Read an audio file as mono int16 PCM at 11025 Hz.

    Returns (pcm_bytes, sample_rate, channels, duration_seconds).
    """
    import audioread
    with audioread.audio_open(str(path)) as f:
        sr, ch, dur = f.samplerate, f.channels, f.duration
        raw = bytearray()
        for block in f:
            raw.extend(block)
    pcm = np.frombuffer(bytes(raw), dtype=np.int16)
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1).astype(np.int16)
    if sr != 11025:
        ratio = 11025 / sr
        new_len = int(len(pcm) * ratio)
        x_old = np.linspace(0, 1, len(pcm))
        x_new = np.linspace(0, 1, new_len)
        pcm = np.interp(x_new, x_old, pcm.astype(np.float32)).astype(np.int16)
    return pcm.tobytes(), 11025, 1, dur


def _fingerprint_pcm(pcm: bytes, sr: int, ch: int) -> str:
    """Generate AcoustID fingerprint from raw PCM."""
    import acoustid
    chunk_size = sr * 2 * 2  # ~2 seconds at a time
    def chunks():
        for i in range(0, len(pcm), chunk_size):
            yield pcm[i : i + chunk_size]
    return acoustid.fingerprint(sr, ch, chunks(), maxlength=2 * 60 * 60)


def _parse_filename(path: Path) -> tuple[str, str]:
    """Extract (artist, title) from 'Artist/Album/Artist - Title.opus'."""
    stem = path.stem
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return a.strip(), t.strip()
    return path.parent.parent.name, stem


def verify_file(
    path: Path,
    api_key: str,
    min_score: float = 0.5,
    expected: Optional[tuple[str, str]] = None,
) -> VerificationResult:
    """Verify one file: fingerprint it, look up in AcoustID, score the match."""
    expected = expected or _parse_filename(path)
    result = VerificationResult(path=path, expected=expected, matched=False, acoustid_score=0.0)

    try:
        pcm, sr, ch, file_dur = _decode_to_pcm(path)
    except Exception as e:
        result.reason = f"decode error: {e}"
        result.decision = "unknown"
        return result
    result.file_duration = file_dur

    if file_dur < 35:
        # 30-second previews: not necessarily a mismatch — could be a real
        # short track, but very unlikely. Mark as preview for review.
        result.decision = "preview"
        result.reason = f"{file_dur:.1f}s — likely preview"
        # still try to identify it

    try:
        fp = _fingerprint_pcm(pcm, sr, ch)
    except Exception as e:
        result.reason = f"fingerprint error: {e}"
        result.decision = "unknown"
        return result

    import acoustid
    try:
        lookup = acoustid.lookup(
            apikey=api_key, fingerprint=fp, duration=int(file_dur),
            meta="recordings releasegroups",
        )
    except Exception as e:
        result.reason = f"lookup error: {e}"
        result.decision = "unknown"
        return result

    if "error" in lookup:
        result.reason = f"API error: {lookup['error']}"
        result.decision = "unknown"
        return result

    results = lookup.get("results", [])
    if not results:
        result.decision = "unknown"
        result.reason = "no AcoustID match"
        return result

    best = results[0]
    if isinstance(best, dict):
        # Newer acoustid returns dicts with 'score' and 'recordings' etc.
        best_score = best.get("score", 0.0)
        rec = (best.get("recordings") or [{}])[0] or best
    else:
        # Legacy tuple format: (score, recording_dict)
        best_score = best[0]
        rec = (best[1].get("recordings") or [{}])[0]
    result.acoustid_score = best_score
    result.found_artist = (rec.get("artists") or [{}])[0].get("name", "")
    result.found_title = rec.get("title", "")
    result.found_duration = rec.get("duration", 0)
    result.recording_id = rec.get("id", "")

    # Score-match against expected
    def norm(s):
        return s.lower().replace("'", "").replace("'", "").replace(" ", "")

    exp_a, exp_t = expected
    artist_ok = norm(exp_a) in norm(result.found_artist) or norm(result.found_artist) in norm(exp_a)
    title_ok = norm(exp_t) in norm(result.found_title) or norm(result.found_title) in norm(exp_t)

    if best_score >= min_score and artist_ok and title_ok:
        result.matched = True
        result.decision = "match"
    elif best_score >= min_score:
        result.matched = False
        result.decision = "mismatch"
        result.reason = f"got {result.found_artist} - {result.found_title} (score {best_score:.2f})"
    else:
        result.matched = False
        result.decision = "mismatch"
        result.reason = f"low score {best_score:.2f}: {result.found_artist} - {result.found_title}"

    return result


def _parse_args():
    import argparse
    p = argparse.ArgumentParser(description="Verify library against AcoustID")
    p.add_argument("library", nargs="?", default="./library", type=Path)
    p.add_argument("--api-key", default=os.environ.get("ACOUSTID_API_KEY", ""))
    p.add_argument("--min-score", type=float, default=0.5, help="Match threshold (default 0.5)")
    p.add_argument("--delete", action="store_true", help="Delete mismatched files (default: report only)")
    p.add_argument("--delete-previews", action="store_true", help="Delete 30s previews too")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int, default=0, help="Process only N files (for testing)")
    return p.parse_args()


def main():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    args = _parse_args()
    if not args.api_key:
        print("ERROR: provide --api-key or set ACOUSTID_API_KEY env")
        return 1

    # Pre-load chromaprint library
    _load_lib()

    files = sorted(args.library.rglob("*.opus"))
    if args.limit:
        files = files[: args.limit]
    print(f"verifying {len(files)} files...")

    results: list[VerificationResult] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(verify_file, f, args.api_key, args.min_score): f for f in files}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            tag = {"match": "✓", "mismatch": "✗", "preview": "?", "unknown": " "}.get(r.decision, " ")
            print(f"  {tag} {r.path.name}  score={r.acoustid_score:.2f}  found={r.found_artist} - {r.found_title}" + (f"  [{r.reason}]" if r.reason else ""))
            if i % 20 == 0:
                rate = i / (time.time() - t0)
                eta = (len(files) - i) / rate
                print(f"  --- {i}/{len(files)}  {rate:.1f}/s  ETA {eta/60:.1f}m ---")

    # Summary
    n_match = sum(1 for r in results if r.decision == "match")
    n_mis = sum(1 for r in results if r.decision == "mismatch")
    n_prev = sum(1 for r in results if r.decision == "preview")
    n_unk = sum(1 for r in results if r.decision == "unknown")
    print(f"\n=== {len(results)} files in {(time.time()-t0)/60:.1f} min ===")
    print(f"  match:    {n_match} ({100*n_match/len(results):.0f}%)")
    print(f"  mismatch: {n_mis}  ← these are wrong")
    print(f"  preview:  {n_prev}  (≤35s, may be real short tracks)")
    print(f"  unknown:  {n_unk}  (not in AcoustID)")

    if args.delete or args.delete_previews:
        targets = [r for r in results if r.decision == "mismatch"]
        if args.delete_previews:
            targets += [r for r in results if r.decision == "preview"]
        if not targets:
            print("\nno files to delete")
            return 0
        print(f"\ndeleting {len(targets)} files...")
        for r in targets:
            r.path.unlink()
            print(f"  ✗ {r.path.name}")
        # Clean empty dirs
        for d in sorted(args.library.rglob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()) and not d.name.startswith("."):
                d.rmdir()
    return 0
