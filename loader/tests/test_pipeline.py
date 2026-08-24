"""Smoke tests for the loader pipeline (no network required)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from loader.config import Config
from loader.pipeline import Pipeline, sanitize, _output_paths
from loader.sources.base import TrackInfo, Source


class TestSanitize(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(sanitize("Hello/World"), "Hello_World")
        # 9 invalid chars (one between each letter) -> 9 underscores
        self.assertEqual(sanitize('a<b>c:d"e/f\\g|h?i*j'), "a_b_c_d_e_f_g_h_i_j")

    def test_empty(self):
        self.assertEqual(sanitize(""), "")

    def test_truncate(self):
        self.assertEqual(sanitize("a" * 200, max_len=10), "a" * 10)


class TestOutputPaths(unittest.TestCase):
    def test_sanitized(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            out_dir, out_path = _output_paths(root, "Artist/Name", "Album:Name", "Title/Name")
            self.assertEqual(out_dir, root / "Artist_Name" / "Album_Name")
            # Filename includes "Artist - Title" format
            self.assertEqual(out_path, out_dir / "Artist_Name - Title_Name.opus")


class _FailingSource(Source):
    name = "fail"
    def search(self, artist, title, album=""):
        return None
    def download(self, info, output_path):
        return False


def _stub_duration(pipe, seconds=120.0):
    """Stub the duration probe so the pipeline sees a plausible track.

    The duration guard (floor 60s / ceiling 10min / ratio check) is
    unit-tested elsewhere; these tests exercise fallback and caching.
    """
    pipe._get_duration = staticmethod(lambda path: seconds)


class _SuccessSource(Source):
    name = "ok"
    def __init__(self):
        self.calls = 0
    def search(self, artist, title, album=""):
        self.calls += 1
        return TrackInfo(source="ok", url="dummy", artist=artist, title=title, match_score=1.0)
    def download(self, info, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"ID3" + b"\x00" * 100)
        return True


class TestPipelineFallback(unittest.TestCase):
    def test_falls_back_to_next_source(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(output_dir=d)
            ok = _SuccessSource()
            fail = _FailingSource()
            pipe = Pipeline(cfg, [fail, ok])
            _stub_duration(pipe)
            pipe.process([{"artist": "A", "title": "T", "album": "X"}])
            self.assertEqual(pipe.stats["success"], 1)
            self.assertEqual(pipe.stats["failed"], 0)
            self.assertEqual(ok.calls, 1)
            self.assertTrue((Path(d) / "A" / "X" / "A - T.opus").exists())

    def test_skips_existing(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(output_dir=d, skip_existing=True)

            class CountingSource(Source):
                name = "ok"
                def __init__(self):
                    self.calls = 0
                def search(self, artist, title, album=""):
                    self.calls += 1
                    return TrackInfo(source="ok", url="dummy", artist=artist, title=title, match_score=1.0)
                def download(self, info, output_path):
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(b"x")
                    return True

            src = CountingSource()
            pipe = Pipeline(cfg, [src])
            _stub_duration(pipe)
            track = {"artist": "A", "title": "T", "album": "X"}
            pipe.process([track])
            pipe.process([track])
            self.assertEqual(src.calls, 1, "second run must skip existing file")

    def test_no_sources(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(output_dir=d)
            pipe = Pipeline(cfg, [])
            pipe.process([{"artist": "A", "title": "T"}])
            self.assertEqual(pipe.stats["failed"], 1)

    def test_enricher_fills_metadata(self):
        """MusicBrainz-style enricher should fill in album/year before search."""
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(output_dir=d)

            captured = {}

            class CapturingSource(Source):
                name = "cap"
                def search(self, artist, title, album=""):
                    captured["artist"] = artist
                    captured["title"] = title
                    captured["album"] = album
                    return None
                def download(self, info, output_path):
                    return False

            class FakeEnricher:
                name = "fake"
                def is_available(self): return True
                def enrich(self, artist, title):
                    return {"artist": artist, "title": title, "album": "Found Album", "year": "2020"}

            pipe = Pipeline(cfg, [CapturingSource()], enrichers=[FakeEnricher()])
            _stub_duration(pipe)
            pipe.process([{"artist": "A", "title": "T"}])
            self.assertEqual(captured["album"], "Found Album")


if __name__ == "__main__":
    unittest.main()
