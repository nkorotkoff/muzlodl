"""Tests for sources (Jamendo + MusicBrainz + yt-dlp extras)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from loader.sources.base import score_match
from loader.sources.jamendo import JamendoSource
from loader.sources.mp3party import MP3PartySource
from loader.sources.musicbrainz import MusicBrainzEnricher
from loader.sources.ytdlp_extras import DailymotionSource


class TestMP3PartyParser(unittest.TestCase):
    """The data-js-* attributes appear on one <div> in any order;
    artist-name comes BEFORE data-js-id. The parser must capture the
    whole tag or the artist is lost and every result scores 0.5 (below
    the pipeline's 0.75 threshold)."""

    _HTML = (
        '<div class="track__user-panel" data-is-playlist="false" '
        'data-js-artist-name="Coldplay" data-js-id="50240" '
        'data-js-image="/system/x.jpg" '
        'data-js-song-title="Viva la Vida" '
        'data-js-url="https://dl2.mp3party.net/online/50240.mp3">'
        '<div class="track__imageWrapp"></div></div>'
    )

    def test_parses_artist_before_id(self):
        src = MP3PartySource()
        results = src._parse_results(self._HTML)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "50240")
        self.assertEqual(results[0]["artist"], "Coldplay")
        self.assertEqual(results[0]["title"], "Viva la Vida")
        self.assertEqual(results[0]["url"], "https://dl2.mp3party.net/online/50240.mp3")

    def test_search_score_full_match(self):
        src = MP3PartySource()
        with patch.object(src, "_fetch_search", return_value=self._HTML):
            results = list(src.search_iter("Coldplay", "Viva La Vida"))
        self.assertEqual(len(results), 1)
        # Artist must survive parsing so the score is a real match,
        # not the 0.5 cap applied when the artist is unknown.
        self.assertEqual(results[0].artist, "Coldplay")
        self.assertEqual(results[0].match_score, 1.0)

    def test_parses_entities_in_title(self):
        html = self._HTML.replace(
            'data-js-song-title="Viva la Vida"',
            'data-js-song-title="Livin&#39; On A Prayer"',
        )
        src = MP3PartySource()
        results = src._parse_results(html)
        self.assertEqual(results[0]["title"], "Livin' On A Prayer")


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
    def test_dailymotion_prefix(self):
        self.assertEqual(DailymotionSource.search_prefix, "dailymotionsearch5:")
        self.assertEqual(DailymotionSource.name, "dailymotion")


if __name__ == "__main__":
    unittest.main()
