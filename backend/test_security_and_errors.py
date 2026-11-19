"""Tests for local API security and stable user-facing error responses."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app  # noqa: E402


class TestSecurityAndErrorResponses(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_unknown_endpoint_has_stable_error_code(self):
        response = self.client.get("/does-not-exist")
        self.assertEqual(response.status_code, 404)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "NOT_FOUND")
        self.assertIn("endpoint", payload["message"].lower())

    def test_untrusted_origin_is_rejected(self):
        response = self.client.get("/", headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error_code"], "ORIGIN_NOT_ALLOWED")

    def test_non_local_client_is_rejected(self):
        response = self.client.get("/", environ_base={"REMOTE_ADDR": "192.168.1.25"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error_code"], "LOCAL_ONLY")

    def test_download_validation_is_actionable(self):
        response = self.client.post("/download", json={})
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["error_code"], "URL_REQUIRED")
        self.assertIn("media URL", payload["message"])

    def test_non_local_backend_url_is_rejected(self):
        response = self.client.post(
            "/settings",
            json={"backend_url": "http://192.168.1.10:5000"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error_code"], "BACKEND_URL_NOT_ALLOWED")

    def test_security_headers_are_present(self):
        response = self.client.get("/")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
