"""Input loaders tests (no network required)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from loader.loaders import load_csv, load_json, load_text


class TestCSV(unittest.TestCase):
    def test_basic(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tracks.csv"
            p.write_text("artist,title,album,year\nA,T,X,2000\n", encoding="utf-8")
            rows = load_csv(p)
            self.assertEqual(rows, [{"artist": "A", "title": "T", "album": "X", "year": "2000"}])


class TestJSON(unittest.TestCase):
    def test_array(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tracks.json"
            p.write_text(json.dumps([{"artist": "A", "title": "T"}]), encoding="utf-8")
            self.assertEqual(load_json(p), [{"artist": "A", "title": "T"}])

    def test_object_with_tracks_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tracks.json"
            p.write_text(json.dumps({"tracks": [{"artist": "A"}]}), encoding="utf-8")
            self.assertEqual(load_json(p), [{"artist": "A"}])


class TestText(unittest.TestCase):
    def test_artist_title(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tracks.txt"
            p.write_text(
                "# comment\n\nPink Floyd - Time\nLed Zeppelin - Stairway\nJust Title\n",
                encoding="utf-8",
            )
            rows = load_text(p)
            self.assertEqual(
                rows,
                [
                    {"artist": "Pink Floyd", "title": "Time"},
                    {"artist": "Led Zeppelin", "title": "Stairway"},
                    {"title": "Just Title"},
                ],
            )

    def test_strips_artist_prefix(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tracks.txt"
            p.write_text("Pink Floyd - Pink Floyd - Time\n", encoding="utf-8")
            rows = load_text(p)
            self.assertEqual(rows, [{"artist": "Pink Floyd", "title": "Time"}])


if __name__ == "__main__":
    unittest.main()
