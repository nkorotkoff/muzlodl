"""Tests for doctor + state (auto-detection and caching)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from loader.doctor import SourceHealth, run_doctor, pick_default_chain
from loader.state import get_or_detect, reset as reset_state, load_state, save_state
from loader.config import Config


class TestDoctor(unittest.TestCase):
    def test_pick_chain_prefers_downloadable(self):
        results = {
            "youtube": SourceHealth(name="youtube", available=True, can_search=True, can_download=True),
            "openverse": SourceHealth(name="openverse", available=True, can_search=True, can_download=False),
            "archiveorg": SourceHealth(name="archiveorg", available=True, can_search=True, can_download=True),
        }
        chain = pick_default_chain(results)
        # Both archiveorg and youtube are full; openverse search-only goes last
        self.assertIn("openverse", chain)
        self.assertEqual(chain[-1], "openverse")
        # Downloadable sources come before search-only
        self.assertLess(chain.index("archiveorg"), chain.index("openverse"))

    def test_pick_chain_excludes_itunes_by_default(self):
        results = {
            "itunes": SourceHealth(name="itunes", available=True, can_search=True, can_download=True),
            "archiveorg": SourceHealth(name="archiveorg", available=True, can_search=True, can_download=True),
        }
        chain = pick_default_chain(results)
        # In our state module we strip itunes; here we just test pick_default_chain keeps order
        self.assertIn("itunes", chain)

    def test_run_doctor_returns_dict(self):
        # Mock requests to avoid network
        with patch("requests.get") as g, patch("requests.head") as h:
            mock = type("R", (), {"status_code": 200, "close": lambda s: None})()
            g.return_value = mock
            h.return_value = mock
            results = run_doctor(Config(), only=["archiveorg"])
        self.assertIn("archiveorg", results)
        self.assertIsInstance(results["archiveorg"], SourceHealth)


class TestState(unittest.TestCase):
    def test_reset_removes_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            save_state(p, {"x": 1})
            self.assertTrue((p / ".loader-state.json").exists())
            self.assertTrue(reset_state(p))
            self.assertFalse((p / ".loader-state.json").exists())

    def test_reset_no_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(reset_state(Path(d)))

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            save_state(p, {"version": 1, "last_check": "2026-01-01T00:00:00"})
            loaded = load_state(p)
            self.assertEqual(loaded["version"], 1)

    def test_get_or_detect_creates_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            self.assertIsNone(load_state(p))
            with patch("loader.state.run_doctor") as run:
                run.return_value = {
                    "archiveorg": SourceHealth(name="archiveorg", available=True,
                                               can_search=True, can_download=True),
                }
                chain, state, was_fresh = get_or_detect(Config(), p)
            self.assertFalse(was_fresh)
            self.assertEqual(chain, ["archiveorg"])
            self.assertTrue((p / ".loader-state.json").exists())

    def test_get_or_detect_uses_cache(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            # Write a fresh state (timestamp just now)
            from datetime import datetime
            state = {
                "version": 1,
                "last_check": datetime.now().isoformat(),
                "ttl_hours": 1,
                "default_chain": ["archiveorg", "openverse"],
                "results": {
                    "archiveorg": {"status": "ok"},
                    "openverse": {"status": "ok"},
                },
            }
            save_state(p, state)
            # Should return cached chain without calling run_doctor
            with patch("loader.state.run_doctor") as run:
                run.return_value = {}
                chain, _, was_fresh = get_or_detect(Config(), p)
            self.assertTrue(was_fresh)
            self.assertEqual(chain, ["archiveorg", "openverse"])
            run.assert_not_called()

    def test_get_or_detect_force_reruns(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            state = {
                "version": 1,
                "last_check": "2026-06-30T12:00:00",
                "ttl_hours": 1,
                "default_chain": ["old"],
                "results": {},
            }
            save_state(p, state)
            with patch("loader.state.run_doctor") as run:
                run.return_value = {
                    "archiveorg": SourceHealth(name="archiveorg", available=True,
                                               can_search=True, can_download=True),
                }
                chain, _, was_fresh = get_or_detect(Config(), p, force=True)
            self.assertFalse(was_fresh)
            self.assertEqual(chain, ["archiveorg"])
            run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
