"""Tests for the download state machine (DownloadState, validate_transition)."""

import unittest
import downloader
from downloader import DownloadState, validate_transition, KerzoxDownloadError


class TestDownloadStateEnum(unittest.TestCase):
    def test_all_states_defined(self):
        expected = {
            "queued", "starting", "downloading", "paused",
            "retrying", "verifying", "completed", "failed",
            "cancelled", "recovering",
        }
        actual = {s.value for s in DownloadState}
        self.assertEqual(actual, expected)

    def test_legacy_running_maps_to_starting(self):
        self.assertEqual(
            validate_transition("running", DownloadState.CANCELLED),
            DownloadState.CANCELLED,
        )

    def test_legacy_running_not_in_enum(self):
        with self.assertRaises(ValueError):
            DownloadState("running")


class TestValidTransitions(unittest.TestCase):
    def _assert_valid(self, current, next_state):
        result = validate_transition(current, next_state)
        self.assertEqual(result, DownloadState(next_state) if isinstance(next_state, str) else next_state)

    def _assert_invalid(self, current, next_state):
        with self.assertRaises(KerzoxDownloadError):
            validate_transition(current, next_state)

    # QUEUED
    def test_queued_to_starting(self):
        self._assert_valid("queued", "starting")

    def test_queued_to_paused(self):
        self._assert_valid("queued", "paused")

    def test_queued_to_cancelled(self):
        self._assert_valid("queued", "cancelled")

    def test_queued_to_completed_invalid(self):
        self._assert_invalid("queued", "completed")

    def test_queued_to_failed(self):
        self._assert_valid("queued", "failed")

    # STARTING
    def test_starting_to_downloading(self):
        self._assert_valid("starting", "downloading")

    def test_starting_to_cancelled(self):
        self._assert_valid("starting", "cancelled")

    def test_starting_to_completed_invalid(self):
        self._assert_invalid("starting", "completed")

    # DOWNLOADING
    def test_downloading_to_paused(self):
        self._assert_valid("downloading", "paused")

    def test_downloading_to_cancelled(self):
        self._assert_valid("downloading", "cancelled")

    def test_downloading_to_verifying(self):
        self._assert_valid("downloading", "verifying")

    def test_downloading_to_completed_invalid(self):
        self._assert_invalid("downloading", "completed")

    # PAUSED
    def test_paused_to_queued(self):
        self._assert_valid("paused", "queued")

    def test_paused_to_cancelled(self):
        self._assert_valid("paused", "cancelled")

    def test_paused_to_downloading_invalid(self):
        self._assert_invalid("paused", "downloading")

    # RETRYING
    def test_retrying_to_queued(self):
        self._assert_valid("retrying", "queued")

    def test_retrying_to_cancelled(self):
        self._assert_valid("retrying", "cancelled")

    def test_retrying_to_failed(self):
        self._assert_valid("retrying", "failed")

    def test_retrying_to_completed_invalid(self):
        self._assert_invalid("retrying", "completed")

    # COMPLETED — terminal
    def test_completed_to_anything_invalid(self):
        for s in ("queued", "downloading", "paused", "failed", "cancelled"):
            self._assert_invalid("completed", s)

    # FAILED
    def test_failed_to_queued(self):
        self._assert_valid("failed", "queued")

    def test_failed_to_cancelled_invalid(self):
        self._assert_invalid("failed", "cancelled")

    def test_failed_to_downloading_invalid(self):
        self._assert_invalid("failed", "downloading")

    # CANCELLED — terminal
    def test_cancelled_to_anything_invalid(self):
        for s in ("queued", "downloading", "paused", "failed"):
            self._assert_invalid("cancelled", s)

    # RECOVERING
    def test_recovering_to_queued(self):
        self._assert_valid("recovering", "queued")

    def test_recovering_to_failed(self):
        self._assert_valid("recovering", "failed")

    def test_recovering_to_cancelled(self):
        self._assert_valid("recovering", "cancelled")


class TestSelfTransitionRejected(unittest.TestCase):
    def test_queued_to_queued_invalid(self):
        with self.assertRaises(KerzoxDownloadError):
            validate_transition("queued", "queued")

    def test_completed_to_completed_invalid(self):
        with self.assertRaises(KerzoxDownloadError):
            validate_transition("completed", "completed")

    def test_cancelled_to_cancelled_invalid(self):
        with self.assertRaises(KerzoxDownloadError):
            validate_transition("cancelled", "cancelled")


if __name__ == "__main__":
    unittest.main()
