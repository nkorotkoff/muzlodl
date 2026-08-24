"""Tests for web security: CSRF double-submit and login rate limiting."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import flask
from flask import jsonify, request

from loader.web import security


class TestCSRF(unittest.TestCase):
    """check_csrf reads flask.request — exercise it inside a request."""

    def _make_app(self):
        app = flask.Flask(__name__)
        app.secret_key = "test"

        @app.post("/check")
        def check():
            return jsonify({"ok": security.check_csrf()})

        @app.get("/cookie")
        def cookie():
            resp = jsonify({"ok": True})
            security.ensure_csrf_cookie(resp)
            return resp

        return app

    def test_check_csrf_matches(self):
        app = self._make_app()
        token = "abc123"
        with app.test_client() as c:
            c.set_cookie("csrf_token", token)
            r = c.post("/check", headers={"X-CSRF-Token": token})
            self.assertEqual(r.json["ok"], True)

    def test_check_csrf_mismatch_rejected(self):
        app = self._make_app()
        with app.test_client() as c:
            c.set_cookie("csrf_token", "cookie-token")
            r = c.post("/check", headers={"X-CSRF-Token": "header-token"})
            self.assertEqual(r.json["ok"], False)

    def test_check_csrf_missing_parts_rejected(self):
        app = self._make_app()
        with app.test_client() as c:
            r = c.post("/check")
            self.assertEqual(r.json["ok"], False)
            r = c.post("/check", headers={"X-CSRF-Token": "h"})
            self.assertEqual(r.json["ok"], False)

    def test_ensure_csrf_cookie_sets(self):
        app = self._make_app()
        with app.test_client() as c:
            r = c.get("/cookie")
            self.assertIn("csrf_token", r.headers.get("Set-Cookie", ""))

    def test_tokens_unique(self):
        self.assertNotEqual(security._new_token(), security._new_token())


class TestRateLimiter(unittest.TestCase):
    def test_allows_up_to_limit(self):
        lim = security._RateLimiter(max_attempts=3, window_seconds=60)
        self.assertTrue(lim.allowed("1.2.3.4"))
        self.assertTrue(lim.allowed("1.2.3.4"))
        self.assertTrue(lim.allowed("1.2.3.4"))
        self.assertFalse(lim.allowed("1.2.3.4"))

    def test_other_ip_unaffected(self):
        lim = security._RateLimiter(max_attempts=2, window_seconds=60)
        lim.allowed("1.1.1.1")
        lim.allowed("1.1.1.1")
        self.assertFalse(lim.allowed("1.1.1.1"))
        self.assertTrue(lim.allowed("2.2.2.2"))

    def test_reset_clears_window(self):
        lim = security._RateLimiter(max_attempts=1, window_seconds=60)
        self.assertTrue(lim.allowed("9.9.9.9"))
        self.assertFalse(lim.allowed("9.9.9.9"))
        lim.reset("9.9.9.9")
        self.assertTrue(lim.allowed("9.9.9.9"))

    def test_window_expiry(self):
        lim = security._RateLimiter(max_attempts=1, window_seconds=1)
        self.assertTrue(lim.allowed("5.5.5.5"))
        self.assertFalse(lim.allowed("5.5.5.5"))
        # Simulate the window passing: prune happens on next call
        import time
        with patch("loader.web.security.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + 10
            self.assertTrue(lim.allowed("5.5.5.5"))


class TestClientIP(unittest.TestCase):
    def _make_app(self):
        app = flask.Flask(__name__)

        @app.get("/ip")
        def ip():
            return jsonify({"ip": security.client_ip()})

        return app

    def test_xff_first_hop(self):
        app = self._make_app()
        with app.test_client() as c:
            r = c.get("/ip", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"})
            self.assertEqual(r.json["ip"], "203.0.113.7")

    def test_fallback_to_remote_addr(self):
        app = self._make_app()
        with app.test_client() as c:
            r = c.get("/ip")
            self.assertNotEqual(r.json["ip"], "")


if __name__ == "__main__":
    unittest.main()
