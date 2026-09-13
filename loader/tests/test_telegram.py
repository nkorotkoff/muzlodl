"""Tests for the Telegram Bot API client + storage backend."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from loader.telegram import (
    TelegramClient, TelegramError, _load_registry,
)
from loader.storage import (
    CloudConfig, TelegramStorage, make_storage, _flood_wait,
)


def _ok(result=None):
    m = MagicMock()
    m.json.return_value = {"ok": True, "result": result or {}}
    m.status_code = 200
    return m


def _fail(code, desc):
    m = MagicMock()
    m.json.return_value = {"ok": False, "error_code": code, "description": desc}
    m.status_code = code
    return m


class TestTelegramClient(unittest.TestCase):
    def test_empty_token_raises(self):
        with self.assertRaises(TelegramError):
            TelegramClient("", "-1001")

    def test_empty_chat_raises(self):
        with self.assertRaises(TelegramError):
            TelegramClient("123:abc", "")

    def test_me_ok(self):
        c = TelegramClient("123:abc", "-1001")
        with patch.object(c.session, "post", return_value=_ok({"username": "mybot"})):
            info = c.me()
        self.assertEqual(info["username"], "mybot")

    def test_api_error_raises(self):
        c = TelegramClient("bad:token", "-1001")
        with patch.object(c.session, "post",
                          return_value=_fail(401, "Unauthorized")):
            with self.assertRaises(TelegramError) as ctx:
                c.me()
        self.assertIn("401", str(ctx.exception))

    def test_check_access_requires_admin(self):
        c = TelegramClient("123:abc", "-1001")
        calls = {"n": 0}

        def fake_post(url, **kw):
            calls["n"] += 1
            if url.endswith("/getMe"):
                return _ok({"username": "mybot"})
            return _fail(400, "Bad Request: chat not found")
        with patch.object(c.session, "post", side_effect=fake_post):
            with self.assertRaises(TelegramError) as ctx:
                c.check_access()
        self.assertIn("admin", str(ctx.exception))

    def test_upload_skips_big_file(self):
        c = TelegramClient("123:abc", "-1001")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "big.opus"
            f.write_bytes(b"x" * 10)
            with patch("loader.telegram.MAX_FILE_SIZE", 5):
                self.assertFalse(c.upload(f, "A/B/big"))

    def test_upload_skips_unchanged(self):
        c = TelegramClient("123:abc", "-1001")
        c._registry = {"A/B/t": {"file_id": "x", "size": 8}}
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.opus"
            f.write_bytes(b"y" * 8)
            with patch.object(c.session, "post") as post:
                self.assertTrue(c.upload(f, "A/B/t"))
                post.assert_not_called()

    def test_upload_sends_audio(self):
        c = TelegramClient("123:abc", "-1001")
        c._registry = {}
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.opus"
            f.write_bytes(b"y" * 8)
            with patch.object(c.session, "post",
                              return_value=_ok({"audio": {"file_id": "FID"}})), \
                 patch("loader.telegram._save_registry"), \
                 patch("loader.telegram.time.sleep"), \
                 patch("mutagen.File", return_value=None):
                self.assertTrue(c.upload(f, "Artist/Album/t"))
                _, kw = c.session.post.call_args
                self.assertEqual(kw["data"]["chat_id"], "-1001")
                self.assertIn("Artist", kw["data"]["performer"])
                self.assertIn("duration", kw["data"])
                self.assertEqual(c.registry["Artist/Album/t"]["file_id"], "FID")


class TestTelegramStorage(unittest.TestCase):
    def test_make_storage(self):
        s = make_storage(CloudConfig(backend="telegram", login="-1001",
                                     password="123:abc"))
        self.assertIsInstance(s, TelegramStorage)

    def test_sequential(self):
        self.assertTrue(TelegramStorage._SEQUENTIAL)

    def test_upload_library_walks_tree(self):
        s = make_storage(CloudConfig(backend="telegram", login="-1001",
                                     password="123:abc"))
        with tempfile.TemporaryDirectory() as lib:
            album = Path(lib) / "Pink Floyd" / "Dark Side"
            album.mkdir(parents=True)
            (album / "Time.opus").write_bytes(b"x" * 10)
            (album / "note.txt").write_text("skip me")
            with patch.object(s.client, "upload", return_value=True) as up:
                count = s.upload_library(Path(lib))
            self.assertEqual(count, 1)
            (ident,) = (c.args[1] for c in up.call_args_list)
            self.assertEqual(ident, "Pink Floyd/Dark Side/Time")

    def test_upload_single_retry_on_flood(self):
        s = make_storage(CloudConfig(backend="telegram", login="-1001",
                                     password="123:abc"))
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.opus"
            f.write_bytes(b"y" * 8)
            side = [TelegramError("[429] Too Many Requests: retry after 1"),
                    True]
            with patch.object(s.client, "upload", side_effect=side), \
                 patch("loader.storage.time.sleep"):
                self.assertTrue(s.upload_single(f, "A", "B", "t"))

    def test_flood_wait_parses(self):
        self.assertEqual(_flood_wait("Too Many Requests: retry after 35"), 35)
        self.assertIsNone(_flood_wait("Unauthorized"))


if __name__ == "__main__":
    unittest.main()
