"""Unit tests for scripts/verify_portable_exe.py isolated port verification (BUG-20)."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import verify_portable_exe


class TestVerifyPortableExePortIsolation(unittest.TestCase):
    """Tests for isolated port selection and health check target in verify_portable_exe."""

    def test_find_free_port_returns_non_5000_port(self):
        """find_free_port must return an isolated port in the 15990-16050 range, not 5000."""
        port = verify_portable_exe.find_free_port()
        self.assertNotEqual(port, 5000, "Must not use standard port 5000")
        self.assertGreaterEqual(port, 15990)
        self.assertLess(port, 16050)

    def test_extract_and_test_uses_isolated_port_in_health_check(self):
        """extract_and_test must query the isolated port, never hardcoded port 5000."""
        recorded_urls = []

        def fake_urlopen(req, timeout=2):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            recorded_urls.append(url)
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = b'{"status": "ok", "version": "2.0.0"}'
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.poll.return_value = None

        mock_proc2 = MagicMock()
        mock_proc2.returncode = 0
        mock_proc2.communicate.return_value = (
            "Another MediaForge Backend instance is already running",
            "",
        )

        with patch("zipfile.ZipFile"), \
             patch("os.path.exists", return_value=True), \
             patch("subprocess.Popen", side_effect=[mock_proc, mock_proc2]), \
             patch.object(verify_portable_exe.urllib.request, "urlopen", side_effect=fake_urlopen), \
             patch("verify_portable_exe._wait_for_exit", return_value=True), \
             patch("verify_portable_exe._stop_process_tree"), \
             patch("shutil.rmtree"):

            result = verify_portable_exe.extract_and_test(test_port=15999)

        self.assertTrue(result)
        self.assertGreater(len(recorded_urls), 0)
        self.assertTrue(
            any("15999" in u for u in recorded_urls),
            f"Health check must target isolated port 15999, got: {recorded_urls}",
        )
        self.assertFalse(
            any(":5000/" in u for u in recorded_urls),
            f"Health check must NOT target port 5000, got: {recorded_urls}",
        )

    def test_extract_and_test_fails_when_backend_unresponsive(self):
        """extract_and_test returns False when health check fails."""
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.poll.return_value = None

        with patch("zipfile.ZipFile"), \
             patch("os.path.exists", return_value=True), \
             patch("subprocess.Popen", return_value=mock_proc), \
             patch.object(verify_portable_exe.urllib.request, "urlopen", side_effect=OSError("connection refused")), \
             patch("time.sleep"), \
             patch("verify_portable_exe._stop_process_tree"), \
             patch("shutil.rmtree"):

            result = verify_portable_exe.extract_and_test(test_port=15999)

        self.assertFalse(result, "Unresponsive backend must report failure")


if __name__ == "__main__":
    unittest.main()
