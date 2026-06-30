"""Tests for newly added sources (Deezer dropped, Audius + MusicBrainz + yt-dlp extras)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from loader.sources.audius import AudiusSource
from loader.sources.base import score_match
from loader.sources.jamendo import JamendoSource
from loader.sources.musicbrainz import MusicBrainzEnricher
from loader.sources.ytdlp_extras import BilibiliSource, DailymotionSource


class TestAudiusSource(unittest.TestCase):
    def setUp(self):
        AudiusSource._cached_host = "https://audius.co"

    def test_search_returns_streamable(self):
        src = AudiusSource()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "data": [{
                "id": "abc",
                "title": "Test Track",
                "duration": 180,
                "streamable": True,
                "user": {"name": "Test Artist"},
                "artwork": {"1000x1000": "https://art/1000.jpg"},
            }]
        }
        with patch("requests.get", return_value=mock_resp) as g:
            info = src.search("Test Artist", "Test Track")
            g.assert_called_once()
        self.assertIsNotNone(info)
        self.assertEqual(info.source, "audius")
        self.assertEqual(info.artist, "Test Artist")
        self.assertEqual(info.title, "Test Track")
        self.assertIn("audius.co", info.url)
        self.assertEqual(info.cover_url, "https://art/1000.jpg")

    def test_search_skips_unstreamable(self):
        src = AudiusSource()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"data": [{
            "id": "abc", "title": "X", "duration": 100,
            "streamable": False, "user": {"name": "A"},
        }]}
        with patch("requests.get", return_value=mock_resp):
            self.assertIsNone(src.search("A", "X"))

    def test_download_writes_bytes(self):
        src = AudiusSource()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "track.mp3"
            with patch("requests.get") as g:
                g.return_value.__enter__.return_value = MagicMock(
                    status_code=200,
                    iter_content=lambda n: [b"ID3", b"\x00" * 1000],
                )
                self.assertTrue(src.download(
                    type("I", (), {"url": "https://x/stream"})(), out,
                ))
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 0)


class TestMusicBrainzEnricher(unittest.TestCase):
    def test_enrich_fills_metadata(self):
        enricher = MusicBrainzEnricher()
        enricher._throttle = lambda: None

        mb_response = MagicMock(status_code=200)
        mb_response.json.return_value = {
            "recordings": [{
                "title": "Canonical Title",
                "artist-credit": [{"name": "Canonical Artist"}],
                "releases": [{
                    "id": "rel-123",
                    "title": "Canonical Album",
                    "date": "1995-03-15",
                }],
            }]
        }
        with patch.object(enricher.session, "get", return_value=mb_response):
            result = enricher.enrich("Some Artist", "Some Title")
        self.assertIsNotNone(result)
        self.assertEqual(result["artist"], "Canonical Artist")
        self.assertEqual(result["title"], "Canonical Title")
        self.assertEqual(result["album"], "Canonical Album")
        self.assertEqual(result["year"], "1995")

    def test_enrich_handles_no_results(self):
        enricher = MusicBrainzEnricher()
        enricher._throttle = lambda: None
        mb_response = MagicMock(status_code=200)
        mb_response.json.return_value = {"recordings": []}
        with patch.object(enricher.session, "get", return_value=mb_response):
            self.assertIsNone(enricher.enrich("Nobody", "Nothing"))


class TestScoreMatch(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(score_match("Artist", "Title", "Artist", "Title"), 1.0)

    def test_wrong_title_low_score(self):
        # Same artist, different song -> should not pass a strict threshold
        s = score_match("Artist", "Yesterday", "Artist", "Let It Be")
        self.assertLess(s, 0.5)

    def test_substring_title_penalized(self):
        s = score_match("Artist", "Time", "Artist", "Time After Time")
        self.assertLess(s, 0.7)


class TestJamendoSource(unittest.TestCase):
    def test_search_filters_low_score(self):
        src = JamendoSource("fake-id")
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "results": [
                {
                    "audio": "https://x/1.mp3",
                    "artist_name": "Wrong Artist",
                    "name": "Wrong Title",
                    "album_name": "Wrong Album",
                    "duration": 180,
                    "releasedate": "2020-01-01",
                    "album_image": "https://x/cover.jpg",
                }
            ]
        }
        with patch("requests.get", return_value=mock_resp):
            info = src.search("Artist", "Title")
        # Jamendo yields the candidate; pipeline's min_match_score rejects it.
        self.assertIsNotNone(info)
        self.assertLess(info.match_score, 0.6)

    def test_search_returns_high_score(self):
        src = JamendoSource("fake-id")
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "results": [
                {
                    "audio": "https://x/1.mp3",
                    "artist_name": "Artist",
                    "name": "Title",
                    "album_name": "Album",
                    "duration": 180,
                    "releasedate": "2020-01-01",
                    "album_image": "https://x/cover.jpg",
                }
            ]
        }
        with patch("requests.get", return_value=mock_resp):
            info = src.search("Artist", "Title")
        self.assertIsNotNone(info)
        self.assertEqual(info.artist, "Artist")
        self.assertEqual(info.title, "Title")
        self.assertGreater(info.match_score, 0.9)


class TestYTDLPExtras(unittest.TestCase):
    def test_bilibili_prefix(self):
        self.assertEqual(BilibiliSource.search_prefix, "bilisearch5:")
        self.assertEqual(BilibiliSource.name, "bilibili")

    def test_dailymotion_prefix(self):
        self.assertEqual(DailymotionSource.search_prefix, "dailymotionsearch5:")
        self.assertEqual(DailymotionSource.name, "dailymotion")


if __name__ == "__main__":
    unittest.main()
