"""
test_updater.py

Unit test suite verifying:
1. Semantic version parsing and comparisons.
2. Cache loading, saving, corruption recovery, and 1-hour expiry checks.
3. HTTP GET retry policy (3 attempts) and shared Session recreation on errors.
4. Download cancellation, temp file removal, and state reset.
5. Integrity check failure handling (file size mismatch) and cleanup.
6. Graceful recovery on missing Internet/offline mode.
"""

import os
import json
import time
import shutil
import unittest
from unittest.mock import MagicMock, patch

import requests

from updater import UpdateManager, COMPANION_VERSION, CACHE_FILE, TEMP_DOWNLOAD_FILE, FINAL_DOWNLOAD_FILE, UPDATES_DIR

class DummyLogger:
    def info(self, msg, *args, **kwargs): pass
    def warning(self, msg, *args, **kwargs): pass
    def error(self, msg, *args, **kwargs): pass
    def log(self, msg, level="INFO", *args, **kwargs): pass


class TestUpdateManager(unittest.TestCase):

    def setUp(self):
        self.logger = DummyLogger()
        self.updater = UpdateManager(self.logger)
        
        # Clean test caches and updates dir
        self._clear_files()

    def tearDown(self):
        self.updater.shutdown()
        self._clear_files()

    def _clear_files(self):
        for f in (CACHE_FILE, TEMP_DOWNLOAD_FILE, FINAL_DOWNLOAD_FILE):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # 1. Semantic Version Parsing & Comparison
    # ------------------------------------------------------------------

    def test_version_comparison(self):
        is_newer = UpdateManager.is_newer_version
        
        # Newer versions
        self.assertTrue(is_newer("1.0.9", "1.1.0"))
        self.assertTrue(is_newer("1.9.9", "2.0.0"))
        self.assertTrue(is_newer("v1.0.0", "v1.2.0"))
        self.assertTrue(is_newer("1.1.0", "1.1.4"))
        self.assertTrue(is_newer("v1.1.0", "1.1.4"))

        # Same or older versions
        self.assertFalse(is_newer("1.1.0", "1.1.0"))
        self.assertFalse(is_newer("2.0.0", "2.0.0"))
        self.assertFalse(is_newer("1.2.0", "1.1.0"))
        self.assertFalse(is_newer("1.1.0", "1.0.9"))
        self.assertFalse(is_newer("v1.1.0", "v1.1.0"))

    # ------------------------------------------------------------------
    # 2. Local Cache Verification
    # ------------------------------------------------------------------

    def test_cache_save_and_load(self):
        self.updater._latest_version = "v2.5.0"
        self.updater._release_notes = "Cool updates."
        self.updater._published = "2026-06-27T00:00:00Z"
        self.updater._asset_url = "http://example.com/installer.exe"
        self.updater._asset_size = 9999
        self.updater._last_checked = time.time()
        
        self.updater._save_cache()
        self.assertTrue(os.path.exists(CACHE_FILE))
        
        # Load in a fresh manager instance
        other_updater = UpdateManager(self.logger)
        self.assertEqual(other_updater.get_latest_version(), "v2.5.0")
        self.assertEqual(other_updater._release_notes, "Cool updates.")
        self.assertEqual(other_updater._asset_size, 9999)

    def test_cache_expiry_and_force(self):
        # Setup cache that is 30 mins old (valid)
        self.updater._latest_version = "v1.5.0"
        self.updater._last_checked = time.time() - 1800.0
        self.updater._save_cache()
        
        with patch.object(self.updater, "_fetch_with_retries") as mock_fetch:
            self.updater.check_for_updates(force=False)
            time.sleep(0.1) # allow worker thread to execute
            mock_fetch.assert_not_called()
            
            # Setup cache that is 1.5 hours old (expired)
            self.updater._last_checked = time.time() - 5400.0
            self.updater._save_cache()
            
            self.updater.check_for_updates(force=False)
            time.sleep(0.1)
            mock_fetch.assert_called_once()

    def test_cache_corruption_recovery(self):
        with open(CACHE_FILE, "w", encoding="utf-8") as fh:
            fh.write("} corrupt json {")
            
        other_updater = UpdateManager(self.logger)
        self.assertEqual(other_updater.get_latest_version(), "v—")
        self.assertEqual(other_updater._last_checked, 0.0)

    # ------------------------------------------------------------------
    # 3. HTTP Session Reuse & Connection Recovery
    # ------------------------------------------------------------------

    @patch("requests.Session.get")
    def test_session_recreation_on_failure(self, mock_get):
        # Simulate two connection failures and a final success
        mock_get.side_effect = [
            requests.exceptions.ConnectionError("Failed connection"),
            requests.exceptions.ConnectionError("Failed connection"),
            MagicMock(status_code=200, json=lambda: {"tag_name": "v1.2.0"})
        ]
        
        initial_session = self.updater._session
        result = self.updater._fetch_with_retries("http://dummy-url")
        
        self.assertIsNotNone(result)
        self.assertEqual(result.get("tag_name"), "v1.2.0")
        
        # Verify initial session is closed and replaced
        self.assertNotEqual(self.updater._session, initial_session)

    # ------------------------------------------------------------------
    # 4. Integrity Checks and Verification
    # ------------------------------------------------------------------

    @patch("requests.Session.get")
    def test_download_integrity_failure_size_mismatch(self, mock_get):
        self.updater._asset_url = "http://dummy-url/setup.exe"
        self.updater._asset_size = 999999 # Large expected size
        
        # Mock stream responses
        mock_response = MagicMock()
        mock_response.headers = {"content-length": "100"} # Tiny server size
        mock_response.iter_content.return_value = [b"a" * 100]
        mock_get.return_value = mock_response
        
        # Spy callback notifications
        notifications = []
        def _cb(status, progress, err):
            notifications.append(status)
        self.updater.register_callback(_cb)
        
        self.updater.download_update()
        time.sleep(0.5) # allow worker thread to download & verify
        
        self.assertIn("Failed", notifications)
        self.assertFalse(os.path.exists(TEMP_DOWNLOAD_FILE))
        self.assertFalse(os.path.exists(FINAL_DOWNLOAD_FILE))

    # ------------------------------------------------------------------
    # 5. Cancellation
    # ------------------------------------------------------------------

    @patch("requests.Session.get")
    def test_download_cancellation(self, mock_get):
        self.updater._asset_url = "http://dummy-url/setup.exe"
        self.updater._asset_size = 500
        
        # Simulate slow response chunks to allow cancellation window
        def chunks(*args, **kwargs):
            time.sleep(0.1)
            yield b"a" * 100
            time.sleep(0.2)
            yield b"b" * 100
            
        mock_response = MagicMock()
        mock_response.headers = {"content-length": "500"}
        mock_response.iter_content = chunks
        mock_get.return_value = mock_response
        
        self.updater.download_update()
        time.sleep(0.15) # let downloader start
        
        self.updater.cancel_download()
        time.sleep(0.3)
        
        self.assertFalse(os.path.exists(TEMP_DOWNLOAD_FILE))
        self.assertFalse(os.path.exists(FINAL_DOWNLOAD_FILE))
        self.assertFalse(self.updater._download_running)

    # ------------------------------------------------------------------
    # 6. Duplicate check prevention
    # ------------------------------------------------------------------

    def test_duplicate_check_prevention(self):
        self.updater._check_running = True
        with patch.object(self.updater, "_notify") as mock_notify:
            self.updater.check_for_updates(force=True)
            mock_notify.assert_not_called()

    # ------------------------------------------------------------------
    # 7. Installer Retention & Lock Handling
    # ------------------------------------------------------------------

    @patch("requests.Session.get")
    def test_installer_retention_and_lock_handling(self, mock_get):
        # Create a mock existing installer
        with open(FINAL_DOWNLOAD_FILE, "wb") as fh:
            fh.write(b"original installer data")
            
        self.updater._asset_url = "http://dummy-url/setup.exe"
        self.updater._asset_size = 100
        
        mock_response = MagicMock()
        mock_response.headers = {"content-length": "100"}
        mock_response.iter_content.return_value = [b"b" * 100]
        mock_get.return_value = mock_response

        # Mock os.rename for FINAL_DOWNLOAD_FILE to bak_file raising PermissionError (locked)
        # Let's patch os.rename to raise PermissionError when the source is FINAL_DOWNLOAD_FILE
        original_rename = os.rename
        def mock_rename(src, dst):
            if src == FINAL_DOWNLOAD_FILE:
                raise PermissionError("Access is denied (mocked file lock)")
            return original_rename(src, dst)

        with patch("os.rename", side_effect=mock_rename):
            self.updater.download_update()
            time.sleep(0.5)
            
        # Assert the existing installer is untouched
        self.assertTrue(os.path.exists(FINAL_DOWNLOAD_FILE))
        with open(FINAL_DOWNLOAD_FILE, "rb") as fh:
            data = fh.read()
        self.assertEqual(data, b"original installer data")
        
        # Assert the newly downloaded installer is kept as .new
        new_file = os.path.join(UPDATES_DIR, "MediaForge-Setup.new")
        self.assertTrue(os.path.exists(new_file))
        with open(new_file, "rb") as fh:
            new_data = fh.read()
        self.assertEqual(new_data, b"b" * 100)

    # ------------------------------------------------------------------
    # 8. GitHub Rate Limit Handling
    # ------------------------------------------------------------------

    @patch("requests.Session.get")
    def test_github_rate_limit_handling(self, mock_get):
        # Mock 403 response with X-RateLimit-Reset header
        reset_timestamp = time.time() + 600.0 # 10 mins in future
        mock_response = MagicMock(status_code=403)
        mock_response.headers = {"X-RateLimit-Reset": str(reset_timestamp)}
        mock_get.return_value = mock_response

        notifications = []
        def _cb(status, progress, err):
            notifications.append(status)
        self.updater.register_callback(_cb)

        # Force check
        self.updater.check_for_updates(force=True)
        time.sleep(0.5)

        self.assertIn("Rate Limited", notifications)
        self.assertAlmostEqual(self.updater._rate_limit_reset_until, reset_timestamp, places=1)

        # Subsequent check (not forced) should be skipped
        with patch.object(self.updater, "_fetch_with_retries") as mock_fetch:
            self.updater.check_for_updates(force=False)
            time.sleep(0.2)
            mock_fetch.assert_not_called()

    # ------------------------------------------------------------------
    # 9. Release Asset Validation & Deterministic Selection (Task 3)
    # ------------------------------------------------------------------

    @patch("requests.Session.get")
    def test_release_only_source_archives(self, mock_get):
        # Mock release containing only source archives
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "tag_name": "v2.0.0",
            "published_at": "2026-06-27T00:00:00Z",
            "body": "Release notes...",
            "html_url": "http://github.com/release",
            "assets": [
                {
                    "name": "source.zip",
                    "state": "uploaded",
                    "size": 1000,
                    "browser_download_url": "http://github.com/source.zip"
                },
                {
                    "name": "source.tar.gz",
                    "state": "uploaded",
                    "size": 500,
                    "browser_download_url": "http://github.com/source.tar.gz"
                }
            ]
        }
        mock_get.return_value = mock_response

        notifications = []
        def _cb(status, progress, err):
            notifications.append(status)
        self.updater.register_callback(_cb)

        self.updater.check_for_updates(force=True)
        time.sleep(0.5)

        self.assertIn("Installer Not Found", notifications)
        self.assertFalse(self.updater.has_update())
        self.assertEqual(self.updater.get_latest_version(), "v2.0.0")

    @patch("requests.Session.get")
    def test_release_valid_installer_present(self, mock_get):
        # Mock release with setup and source archives
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "tag_name": "v2.0.0",
            "published_at": "2026-06-27T00:00:00Z",
            "body": "Notes",
            "html_url": "http://github.com/release",
            "assets": [
                {
                    "name": "source.zip",
                    "state": "uploaded",
                    "size": 1000,
                    "browser_download_url": "http://github.com/source.zip"
                },
                {
                    "name": "MediaForge-Setup.exe",
                    "state": "uploaded",
                    "size": 9999,
                    "browser_download_url": "http://github.com/MediaForge-Setup.exe"
                }
            ]
        }
        mock_get.return_value = mock_response

        self.updater.check_for_updates(force=True)
        time.sleep(0.5)

        self.assertTrue(self.updater.has_update())
        self.assertEqual(self.updater._asset_url, "http://github.com/MediaForge-Setup.exe")

    @patch("requests.Session.get")
    def test_deterministic_sorting_timestamps(self, mock_get):
        # Mock release with multiple installer assets with created_at timestamps
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "tag_name": "v2.0.0",
            "published_at": "2026-06-27T00:00:00Z",
            "body": "Notes",
            "assets": [
                {
                    "name": "MediaForge-Setup-old.exe",
                    "state": "uploaded",
                    "size": 500,
                    "browser_download_url": "http://github.com/old.exe",
                    "created_at": "2026-06-27T01:00:00Z"
                },
                {
                    "name": "MediaForge-Setup-newest.exe",
                    "state": "uploaded",
                    "size": 500,
                    "browser_download_url": "http://github.com/newest.exe",
                    "created_at": "2026-06-27T03:00:00Z"
                },
                {
                    "name": "MediaForge-Setup-mid.exe",
                    "state": "uploaded",
                    "size": 500,
                    "browser_download_url": "http://github.com/mid.exe",
                    "created_at": "2026-06-27T02:00:00Z"
                }
            ]
        }
        mock_get.return_value = mock_response

        self.updater.check_for_updates(force=True)
        time.sleep(0.5)

        self.assertEqual(self.updater._asset_url, "http://github.com/newest.exe")

    @patch("requests.Session.get")
    def test_asset_integrity_checks(self, mock_get):
        # Mock release with invalid installer state, size, or URL
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "tag_name": "v2.0.0",
            "assets": [
                {
                    "name": "MediaForge-Setup-not-uploaded.exe",
                    "state": "uploading",
                    "size": 500,
                    "browser_download_url": "http://github.com/nu.exe"
                },
                {
                    "name": "MediaForge-Setup-zero-size.exe",
                    "state": "uploaded",
                    "size": 0,
                    "browser_download_url": "http://github.com/zero.exe"
                },
                {
                    "name": "MediaForge-Setup-no-url.exe",
                    "state": "uploaded",
                    "size": 500,
                    "browser_download_url": ""
                },
                {
                    "name": "MediaForge-Setup-valid.exe",
                    "state": "uploaded",
                    "size": 500,
                    "browser_download_url": "http://github.com/valid.exe"
                }
            ]
        }
        mock_get.return_value = mock_response

        self.updater.check_for_updates(force=True)
        time.sleep(0.5)

        self.assertEqual(self.updater._asset_url, "http://github.com/valid.exe")

    @patch("requests.Session.get")
    def test_sorting_fallback_updated_at_and_missing_timestamps(self, mock_get):
        # Mock release where created_at is missing but updated_at is present, and some have no timestamps
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "tag_name": "v2.0.0",
            "assets": [
                {
                    "name": "MediaForge-Setup-no-timestamps-first.exe",
                    "state": "uploaded",
                    "size": 500,
                    "browser_download_url": "http://github.com/no-time-first.exe"
                },
                {
                    "name": "MediaForge-Setup-updated-at-new.exe",
                    "state": "uploaded",
                    "size": 500,
                    "browser_download_url": "http://github.com/updated-new.exe",
                    "updated_at": "2026-06-27T04:00:00Z"
                },
                {
                    "name": "MediaForge-Setup-updated-at-old.exe",
                    "state": "uploaded",
                    "size": 500,
                    "browser_download_url": "http://github.com/updated-old.exe",
                    "updated_at": "2026-06-27T02:00:00Z"
                },
                {
                    "name": "MediaForge-Setup-no-timestamps-second.exe",
                    "state": "uploaded",
                    "size": 500,
                    "browser_download_url": "http://github.com/no-time-second.exe"
                }
            ]
        }
        mock_get.return_value = mock_response

        # Since mock_response has updated_at present, the sorting should prefer updated_at over no timestamps.
        # So "http://github.com/updated-new.exe" should be selected first.
        self.updater.check_for_updates(force=True)
        time.sleep(0.5)

        self.assertEqual(self.updater._asset_url, "http://github.com/updated-new.exe")

        # Now test where all assets have NO timestamps. It should preserve original order (select no-time-first).
        mock_response.json.return_value = {
            "tag_name": "v2.0.0",
            "assets": [
                {
                    "name": "MediaForge-Setup-no-timestamps-first.exe",
                    "state": "uploaded",
                    "size": 500,
                    "browser_download_url": "http://github.com/no-time-first.exe"
                },
                {
                    "name": "MediaForge-Setup-no-timestamps-second.exe",
                    "state": "uploaded",
                    "size": 500,
                    "browser_download_url": "http://github.com/no-time-second.exe"
                }
            ]
        }
        self.updater.check_for_updates(force=True)
        time.sleep(0.5)

        self.assertEqual(self.updater._asset_url, "http://github.com/no-time-first.exe")

    # ------------------------------------------------------------------
    # 10. Concurrency Prevention & Graceful Shutdown (Task 2)
    # ------------------------------------------------------------------

    @patch("requests.Session.get")
    def test_concurrent_checks_rejection(self, mock_get):
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"tag_name": "v2.0.0"}
        mock_get.return_value = mock_response

        # Double click check now
        self.updater.check_for_updates(force=True)
        self.updater.check_for_updates(force=True) # should be rejected
        time.sleep(0.5)

        # Assert only one request actually executed (verify call count)
        mock_get.assert_called_once()

    @patch("requests.Session.get")
    def test_concurrent_downloads_rejection(self, mock_get):
        self.updater._asset_url = "http://dummy-url/setup.exe"
        self.updater._asset_size = 500

        # Slow chunk return to keep downloader running
        def slow_chunks(*args, **kwargs):
            time.sleep(0.2)
            yield b"a" * 500
        mock_response = MagicMock()
        mock_response.headers = {"content-length": "500"}
        mock_response.iter_content = slow_chunks
        mock_get.return_value = mock_response

        # Trigger download twice in rapid succession
        self.updater.download_update()
        self.updater.download_update() # should be ignored
        time.sleep(0.5)

        # Since it's a mock, both calls to session.get would have been made if not prevented.
        # Verify Session.get is only called once.
        mock_get.assert_called_once()

    @patch("requests.Session.get")
    def test_shutdown_rejection_inactive(self, mock_get):
        # Call shutdown
        self.updater.shutdown()

        # Try starting check
        self.updater.check_for_updates(force=True)
        time.sleep(0.1)
        mock_get.assert_not_called()

        # Try starting download
        self.updater._asset_url = "http://dummy-url/setup.exe"
        self.updater.download_update()
        time.sleep(0.1)
        # Should not start requests
        mock_get.assert_not_called()

    @patch("requests.Session.get")
    def test_shutdown_rejection_during_active_download(self, mock_get):
        self.updater._asset_url = "http://dummy-url/setup.exe"
        self.updater._asset_size = 500

        # Slow chunk return to keep downloader running
        def slow_chunks(*args, **kwargs):
            time.sleep(0.5)
            yield b"a" * 500
        mock_response = MagicMock()
        mock_response.headers = {"content-length": "500"}
        mock_response.iter_content = slow_chunks
        mock_get.return_value = mock_response

        # Start download
        self.updater.download_update()
        time.sleep(0.1)

        # Call shutdown
        self.updater.shutdown()

        # Try triggering another download
        self.updater.download_update() # rejected
        time.sleep(0.6)

        # session.get should not be called again
        mock_get.assert_called_once()

    @patch("requests.Session.get")
    def test_shutdown_rejection_during_active_check(self, mock_get):
        # Slow API response to keep check worker running
        def slow_json(*args, **kwargs):
            time.sleep(0.5)
            return {"tag_name": "v2.0.0"}
        mock_response = MagicMock(status_code=200)
        mock_response.json = slow_json
        mock_get.return_value = mock_response

        # Start check
        self.updater.check_for_updates(force=True)
        time.sleep(0.1)

        # Call shutdown
        self.updater.shutdown()

        # Try starting new check
        self.updater.check_for_updates(force=True) # rejected
        time.sleep(0.6)

        # API should only have been called once
        mock_get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
