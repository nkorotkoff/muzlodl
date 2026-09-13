"""Tests for the cloud sync queue (watcher + per-file jobs + status)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from loader.web import db
from loader.web import sync_queue


class SyncQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = Path(self.tmp.name) / "lib"
        self.lib.mkdir()
        self._db_tmp = tempfile.TemporaryDirectory()
        import loader.web.dbconn as dbconn
        self._orig_dir = dbconn.DB_DIR
        self._orig_path = dbconn.DB_PATH
        dbconn.DB_DIR = Path(self._db_tmp.name)
        dbconn.DB_PATH = Path(self._db_tmp.name) / "library.db"
        # Drop the thread-local conn so tx() reconnects to the temp DB.
        if hasattr(dbconn._local, "conn"):
            try:
                dbconn._local.conn.close()
            except Exception:
                pass
            dbconn._local.conn = None
        db.init_db()
        self._orig_lib = sync_queue.LIBRARY
        sync_queue.LIBRARY = str(self.lib)
        import loader.web.core as core
        self._orig_core_lib = core.LIBRARY
        core.LIBRARY = str(self.lib)

    def tearDown(self):
        import loader.web.dbconn as dbconn
        if hasattr(dbconn._local, "conn"):
            try:
                dbconn._local.conn.close()
            except Exception:
                pass
            dbconn._local.conn = None
        dbconn.DB_DIR = self._orig_dir
        dbconn.DB_PATH = self._orig_path
        sync_queue.LIBRARY = self._orig_lib
        import loader.web.core as core
        core.LIBRARY = self._orig_core_lib
        self.tmp.cleanup()
        self._db_tmp.cleanup()

    def _mk(self, rel, size=100):
        f = self.lib / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * size)
        return f

    def test_scan_enqueues_new_files(self):
        self._mk("Artist/Album/track.opus")
        self._mk("Artist/Album/notes.txt")
        pending = sync_queue.scan_unsynced()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["file_path"], "Artist/Album/track.opus")

    def test_scan_skips_done_same_size(self):
        self._mk("A/B/t.opus", size=50)
        sync_queue.scan_unsynced()
        with db.tx() as conn:
            conn.execute("UPDATE cloud_sync SET status='done' WHERE file_path=?",
                         ("A/B/t.opus",))
        pending = sync_queue.scan_unsynced()
        self.assertEqual(pending, [])

    def test_scan_requeues_changed_size(self):
        self._mk("A/B/t.opus", size=50)
        sync_queue.scan_unsynced()
        with db.tx() as conn:
            conn.execute("UPDATE cloud_sync SET status='done' WHERE file_path=?",
                         ("A/B/t.opus",))
        self._mk("A/B/t.opus", size=60)
        pending = sync_queue.scan_unsynced()
        self.assertEqual(len(pending), 1)

    def test_scan_drops_vanished(self):
        self._mk("A/B/t.opus")
        sync_queue.scan_unsynced()
        (self.lib / "A/B/t.opus").unlink()
        pending = sync_queue.scan_unsynced()
        self.assertEqual(pending, [])
        with db.tx() as conn:
            n = conn.execute("SELECT COUNT(*) FROM cloud_sync").fetchone()[0]
        self.assertEqual(n, 0)

    def test_sync_status_aggregates(self):
        self._mk("A/B/1.opus")
        self._mk("A/B/2.opus")
        sync_queue.scan_unsynced()
        st = sync_queue.sync_status()
        self.assertEqual(st["total"], 2)
        self.assertEqual(st["done"], 0)
        with db.tx() as conn:
            conn.execute("UPDATE cloud_sync SET status='done' WHERE file_path=?",
                         ("A/B/1.opus",))
        st = sync_queue.sync_status()
        self.assertEqual(st["done"], 1)
        self.assertEqual(st["total"], 2)

    def test_enqueue_creates_per_file_jobs(self):
        self._mk("A/B/1.opus")
        self._mk("A/B/2.opus")
        pending = sync_queue.scan_unsynced()
        self.assertEqual(len(pending), 2)
        with patch.object(sync_queue, "_download_jobs", {}), \
             patch.object(sync_queue, "_jobs_lock",
                          __import__("threading").Lock()), \
             patch("loader.credentials.load_cloud") as mock_cloud, \
             patch("loader.storage.TelegramStorage.upload_single",
                   return_value=True) as mock_up:
            from loader.storage import CloudConfig
            mock_cloud.return_value = CloudConfig(
                backend="telegram", login="-1001", password="123:abc")
            gid = sync_queue.enqueue_files(pending)
            self.assertTrue(gid)
            import time
            for _ in range(200):
                st = sync_queue.sync_status()
                if st["done"] + st["failed"] >= 2:
                    break
                time.sleep(0.05)
        st = sync_queue.sync_status()
        self.assertEqual(st["done"], 2)
        self.assertEqual(st["total"], 2)
        self.assertEqual(mock_up.call_count, 2)


if __name__ == "__main__":
    unittest.main()
