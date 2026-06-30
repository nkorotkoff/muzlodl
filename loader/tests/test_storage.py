"""Tests for WebDAV client + cloud storage backends."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from loader.webdav import WebDAVClient, WebDAVError
from loader.storage import (
    BACKENDS, CloudConfig, YandexDiskStorage, MailRuStorage, make_storage, _safe,
)


def mock_response(status_code: int, text: str = "") -> MagicMock:
    r = MagicMock(status_code=status_code, text=text)
    return r


class TestSafe(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(_safe("Artist Name"), "Artist Name")
        self.assertEqual(_safe("a/b\\c"), "a_b_c")
        self.assertEqual(_safe('<foo:bar>'), "_foo_bar_")
        self.assertEqual(_safe(""), "_")
        self.assertEqual(_safe("."), "_")
        self.assertEqual(_safe("a" * 300), "a" * 200)


class TestWebDAVClient(unittest.TestCase):
    def test_upload_calls_put(self):
        client = WebDAVClient("https://example.com/dav", "user", "pass")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "track.mp3"
            f.write_bytes(b"ID3" + b"\x00" * 100)
            with patch.object(client.session, "request") as req:
                req.return_value = mock_response(201)
                ok = client.upload(f, "/music/track.mp3")
                self.assertTrue(ok)
                # PUT call recorded
                args, kw = req.call_args
                self.assertEqual(args[0], "PUT")
                self.assertIn("/music/track.mp3", args[1])
                self.assertEqual(kw["headers"]["Content-Type"], "audio/mpeg")

    def test_mkcol_creates_parents(self):
        client = WebDAVClient("https://example.com/dav", "user", "pass")
        COLLECTION_XML = (
            "<response><propstat><prop><resourcetype><collection/>"
            "</resourcetype></prop></propstat></response>"
        )

        # Stateful fake: track which paths have been created.
        created = set()

        def fake_request(method, url, **kw):
            # Extract path from full URL
            path = url[len("https://example.com/dav"):].rstrip("/")
            if method == "PROPFIND":
                if path in created:
                    return mock_response(207, COLLECTION_XML)
                return mock_response(404, "")
            if method == "MKCOL":
                created.add(path)
                return mock_response(201, "")
            return mock_response(500, "")

        with patch.object(client.session, "request", side_effect=fake_request) as req:
            ok = client.mkdir("/a/b/c")
            self.assertTrue(ok)
        methods = [c.args[0] for c in req.call_args_list]
        self.assertEqual(methods.count("MKCOL"), 3,
                         f"expected 3 MKCOL (one per parent), got {methods.count('MKCOL')}: {methods}")

    def test_exists(self):
        client = WebDAVClient("https://example.com/dav", "user", "pass")
        with patch.object(client.session, "request") as req:
            req.return_value = mock_response(207)
            self.assertTrue(client.exists("/music"))
            req.return_value = mock_response(404)
            self.assertFalse(client.exists("/missing"))

    def test_list_parses_propfind(self):
        client = WebDAVClient("https://example.com/dav", "user", "pass")
        # PROPFIND with Depth: 1 returns the parent + children
        xml = (
            '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:">'
            '<D:response><D:href>/dav/</D:href></D:response>'
            '<D:response><D:href>/dav/Artist/</D:href>'
            '<D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop></D:propstat></D:response>'
            '<D:response><D:href>/dav/song.mp3</D:href>'
            '<D:propstat><D:prop><D:resourcetype/><D:getcontentlength>12345</D:getcontentlength></D:prop></D:propstat></D:response>'
            '</D:multistatus>'
        )
        with patch.object(client.session, "request") as req:
            req.return_value = mock_response(207, xml)
            items = client.list("/dav")
        # /dav/ is the parent (filtered); 2 children remain
        self.assertEqual(len(items), 2)
        names = {n for n, _, _ in items}
        self.assertEqual(names, {"Artist", "song.mp3"})
        sizes = {s for _, _, s in items}
        self.assertIn(12345, sizes)

    def test_upload_error_raises(self):
        client = WebDAVClient("https://example.com/dav", "user", "pass")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.mp3"
            f.write_bytes(b"x")
            with patch.object(client.session, "request") as req:
                req.return_value = mock_response(403, "Forbidden")
                with self.assertRaises(WebDAVError):
                    client.upload(f, "/x.mp3")


class TestStorageBackends(unittest.TestCase):
    def test_yandex_endpoint(self):
        self.assertEqual(BACKENDS["yandex"]["endpoint"], "https://webdav.yandex.ru")
        self.assertTrue(BACKENDS["yandex"]["howto_url"])

    def test_mailru_endpoint(self):
        self.assertEqual(BACKENDS["mailru"]["endpoint"], "https://webdav.cloud.mail.ru")

    def test_make_storage_yandex(self):
        c = CloudConfig(backend="yandex", login="u@x", password="p")
        s = make_storage(c)
        self.assertIsInstance(s, YandexDiskStorage)
        self.assertEqual(s.name, "yandex")

    def test_make_storage_mailru(self):
        c = CloudConfig(backend="mailru", login="u@x", password="p")
        s = make_storage(c)
        self.assertIsInstance(s, MailRuStorage)

    def test_make_storage_unknown(self):
        c = CloudConfig(backend="nonexistent", login="u", password="p")
        with self.assertRaises(ValueError):
            make_storage(c)

    def test_upload_library_walks_artist_album(self):
        c = CloudConfig(backend="yandex", login="u@x", password="p")
        storage = make_storage(c)
        with tempfile.TemporaryDirectory() as lib:
            artist = Path(lib) / "Pink Floyd"
            album = artist / "Dark Side"
            album.mkdir(parents=True)
            (album / "Time.mp3").write_bytes(b"x" * 10)
            (artist / "Singles").mkdir()
            (artist / "Singles" / "Other.mp3").write_bytes(b"x" * 5)
            (Path(lib) / ".loader.log.jsonl").write_text("[]")
            # Mock the WebDAV client so we don't actually hit the network
            with patch.object(storage.client, "upload_streaming") as up:
                count = storage.upload_library(Path(lib))
            self.assertEqual(count, 2)
            self.assertEqual(up.call_count, 2)  # 2 tracks total


if __name__ == "__main__":
    unittest.main()
