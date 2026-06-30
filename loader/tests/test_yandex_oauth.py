"""Tests for Yandex OAuth flow + REST storage client."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from loader.yandex_oauth import (
    build_implicit_url, build_pkce_url, extract_token_from_redirect,
    generate_pkce_pair, test_token,
)
from loader.credentials import (
    save_yandex_token, load_yandex_token, clear_yandex_token,
)
from loader.storage import YandexRestClient, BACKENDS


class TestImplicitURL(unittest.TestCase):
    def test_build_implicit_url(self):
        url = build_implicit_url("my_client_id")
        self.assertIn("response_type=token", url)
        self.assertIn("client_id=my_client_id", url)
        self.assertIn("redirect_uri=", url)
        self.assertIn("scope=", url)


class TestExtractToken(unittest.TestCase):
    def test_extract_from_fragment(self):
        url = "https://oauth.yandex.com/verification_code#access_token=AQA-abc123&token_type=bearer&expires_in=31536000"
        token = extract_token_from_redirect(url)
        self.assertIsNotNone(token)
        self.assertEqual(token.access_token, "AQA-abc123")
        self.assertEqual(token.token_type, "bearer")
        self.assertEqual(token.expires_in, 31536000)

    def test_extract_from_query(self):
        url = "https://example.com/cb?access_token=AQA-xyz&token_type=bearer"
        token = extract_token_from_redirect(url)
        self.assertIsNotNone(token)
        self.assertEqual(token.access_token, "AQA-xyz")

    def test_extract_with_error(self):
        url = "https://oauth.yandex.com/verification_code#error=access_denied&error_description=User+denied"
        token = extract_token_from_redirect(url)
        self.assertIsNone(token)

    def test_extract_no_token(self):
        url = "https://example.com/some-other-redirect"
        self.assertIsNone(extract_token_from_redirect(url))

    def test_extract_fallback_regex(self):
        # Malformed URL but token is somewhere in it
        url = "garbage#access_token=AQA-regex-works&token_type=bearer"
        token = extract_token_from_redirect(url)
        self.assertIsNotNone(token)
        self.assertEqual(token.access_token, "AQA-regex-works")


class TestPKCE(unittest.TestCase):
    def test_generate_pkce_pair(self):
        v, c = generate_pkce_pair()
        self.assertGreaterEqual(len(v), 43)
        self.assertEqual(len(c), 43)  # SHA256 base64url
        # Verifier and challenge should be different
        self.assertNotEqual(v, c)


class TestYandexTokenStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._patch = patch("loader.credentials._config_dir",
                            return_value=Path(self.tmp.name))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()

    def test_save_and_load(self):
        save_yandex_token("AQA-test-123")
        loaded = load_yandex_token()
        self.assertEqual(loaded, "AQA-test-123")

    def test_clear(self):
        save_yandex_token("AQA-test-123")
        self.assertTrue(clear_yandex_token())
        self.assertIsNone(load_yandex_token())

    def test_load_when_empty(self):
        self.assertIsNone(load_yandex_token())


class TestYandexRestClient(unittest.TestCase):
    def test_constructor(self):
        client = YandexRestClient("AQA-test")
        self.assertEqual(client.token, "AQA-test")
        self.assertIn("Authorization", client.session.headers)
        self.assertEqual(
            client.session.headers["Authorization"], "OAuth AQA-test"
        )

    def test_get_upload_url(self):
        client = YandexRestClient("AQA-test")
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "href": "https://uploader.disk.yandex.net/upload-target/abc123",
            "method": "PUT",
        }
        with patch.object(client.session, "get", return_value=mock_resp) as p:
            url = client.get_upload_url("/music/Artist/Track.mp3")
        self.assertEqual(url, "https://uploader.disk.yandex.net/upload-target/abc123")
        # Check the path was passed correctly
        p.assert_called_once()
        args, kw = p.call_args
        self.assertIn("path", kw["params"])
        self.assertEqual(kw["params"]["path"], "/music/Artist/Track.mp3")
        self.assertEqual(kw["params"]["overwrite"], "true")

    def test_get_upload_url_with_spaces(self):
        """Paths with spaces must be passed raw, not pre-quoted.

        Pre-quoting causes requests to double-encode (%20 -> %2520),
        which Yandex rejects with 409 (path not found).
        """
        client = YandexRestClient("AQA-test")
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "href": "https://uploader.disk.yandex.net/upload-target/abc",
            "method": "PUT",
        }
        raw = "/music/21 Savage/Issa Album/21 Savage - Bank Account.mp3"
        with patch.object(client.session, "get", return_value=mock_resp) as p:
            client.get_upload_url(raw)
        _, kw = p.call_args
        self.assertEqual(kw["params"]["path"], raw)

    def test_upload_success(self):
        client = YandexRestClient("AQA-test")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "track.mp3"
            f.write_bytes(b"x" * 100)
            # Mock the get_upload_url
            with patch.object(client, "get_upload_url",
                            return_value="https://upload.example/up"):
                with patch("requests.put") as put:
                    put.return_value = MagicMock(status_code=201)
                    ok = client.upload(f, "/music/track.mp3")
            self.assertTrue(ok)
            put.assert_called_once()
            args, kw = put.call_args
            self.assertEqual(kw["headers"]["Content-Type"], "audio/mpeg")
            self.assertEqual(kw["headers"]["Content-Length"], "100")

    def test_upload_failure(self):
        client = YandexRestClient("AQA-test")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "track.mp3"
            f.write_bytes(b"x" * 100)
            with patch.object(client, "get_upload_url",
                            return_value="https://upload.example/up"):
                with patch("requests.put") as put:
                    put.return_value = MagicMock(status_code=403, text="Forbidden")
                    with self.assertRaises(RuntimeError):
                        client.upload(f, "/music/track.mp3")

    def test_mkdir_existing(self):
        client = YandexRestClient("AQA-test")
        with patch.object(client.session, "put") as put:
            put.return_value = MagicMock(status_code=409)  # already exists
            self.assertTrue(client.mkdir("/music"))

    def test_mkdir_creates_parent_first(self):
        client = YandexRestClient("AQA-test")
        # First call: 507 (parent missing)
        # Second call (parent): 201 created
        # Third call (original): 201
        with patch.object(client.session, "put") as put:
            put.side_effect = [
                MagicMock(status_code=507),
                MagicMock(status_code=201),
                MagicMock(status_code=201),
            ]
            self.assertTrue(client.mkdir("/music/Artist/Album"))

    def test_backends_dict_has_yandex_rest(self):
        self.assertIn("yandex_rest", BACKENDS)
        self.assertEqual(BACKENDS["yandex_rest"]["endpoint"],
                         "https://cloud-api.yandex.net/v1/disk")


if __name__ == "__main__":
    unittest.main()
