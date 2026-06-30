"""Source health cache. Persists results from `doctor` to avoid re-testing."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

from .doctor import SourceHealth, run_doctor, pick_default_chain

log = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 1  # re-detect at most once per hour
STATE_FILENAME = ".loader-state.json"


def _state_path(output_dir: Path) -> Path:
    return Path(output_dir) / STATE_FILENAME


def load_state(output_dir: Path) -> Optional[dict]:
    p = _state_path(output_dir)
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.debug(f"state load failed: {e}")
        return None


def save_state(output_dir: Path, state: dict) -> None:
    p = _state_path(output_dir)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.debug(f"state save failed: {e}")


def _state_age_hours(state: dict) -> float:
    last = state.get("last_check", "")
    if not last:
        return 1e9
    try:
        from datetime import datetime
        t = datetime.fromisoformat(last)
        return (datetime.now() - t).total_seconds() / 3600
    except Exception:
        return 1e9


def get_or_detect(
    config,
    output_dir: Path,
    force: bool = False,
    include_previews: bool = False,
) -> tuple:
    """Return (chain: List[str], state: dict, was_fresh: bool).

    Loads cached state if fresh (age < TTL). Otherwise runs doctor,
    saves state, and returns the freshly computed chain.
    """
    output_dir = Path(output_dir)
    state = None if force else load_state(output_dir)
    was_fresh = False

    if state is not None:
        ttl = state.get("ttl_hours", DEFAULT_TTL_HOURS)
        age = _state_age_hours(state)
        if age < ttl:
            was_fresh = True
            log.debug(f"using cached source state ({age:.1f}h old)")

    if state is None or not was_fresh:
        log.info("auto-detecting sources (this runs once, then caches)...")
        results = run_doctor(config)
        chain = pick_default_chain(results)
        if not include_previews:
            chain = [n for n in chain if n != "itunes"]
        state = {
            "version": 1,
            "last_check": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "ttl_hours": DEFAULT_TTL_HOURS,
            "results": {n: asdict(h) for n, h in results.items()},
            "default_chain": chain,
        }
        save_state(output_dir, state)

    chain = list(state.get("default_chain", []))
    if include_previews and "itunes" not in chain:
        chain.append("itunes")
    return chain, state, was_fresh


def reset(output_dir: Path) -> bool:
    """Delete cached state. Returns True if a file was removed."""
    p = _state_path(output_dir)
    if p.exists():
        p.unlink()
        return True
    return False


def print_report(state: dict, stream=None) -> None:
    """Print a nicely formatted doctor report."""
    out = stream or log
    out.info("=" * 70)
    out.info("Source health report (cached %s, TTL %sh)",
             state.get("last_check", "?"), state.get("ttl_hours", "?"))
    out.info("=" * 70)
    out.info(f"{'Source':<14} {'Status':<14} {'Search':<8} {'Download':<10} {'Latency':<10} {'Note'}")
    out.info("-" * 70)
    for name, h in sorted(state.get("results", {}).items()):
        s = h.get("status", "?")
        avail = "yes" if h.get("available") else "no"
        dl = "yes" if h.get("can_download") else "no"
        ms = f"{h.get('latency_ms', 0)}ms"
        note = h.get("reason", "") or h.get("extras", {}).get("note", "")
        out.info(f"{name:<14} {s:<14} {avail:<8} {dl:<10} {ms:<10} {note}")
    out.info("-" * 70)
    chain = state.get("default_chain", [])
    out.info(f"Default chain ({len(chain)}): {', '.join(chain)}")
    out.info("=" * 70)
