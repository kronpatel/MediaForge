"""Tests for the Windows user-level MediaForge background task controller."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import background_service  # noqa: E402


class TestBackgroundServiceController(unittest.TestCase):
    def test_install_uses_user_level_logon_task(self):
        result = background_service.ServiceResult(True, "ok")
        with patch.object(background_service, "_run", return_value=result) as run, \
             patch.object(background_service, "backend_command", return_value=[r"C:\MediaForge\MediaForgeBackend.exe"]), \
             patch("background_service.os.path.exists", return_value=True):
            actual = background_service.install_service()

        self.assertTrue(actual.success)
        args = run.call_args.args[0]
        self.assertEqual(args[:4], ["/Create", "/TN", background_service.TASK_NAME, "/TR"])
        self.assertIn("/SC", args)
        self.assertIn("ONLOGON", args)
        self.assertIn("/RL", args)
        self.assertIn("LIMITED", args)

    def test_start_stop_remove_and_status_delegate_to_task_scheduler(self):
        result = background_service.ServiceResult(True, "ok")
        with patch.object(background_service, "_run", return_value=result) as run:
            self.assertTrue(background_service.start_service().success)
            self.assertTrue(background_service.stop_service().success)
            self.assertTrue(background_service.remove_service().success)
            self.assertTrue(background_service.query_service().success)

        self.assertEqual(run.call_args_list[0].args[0], ["/Run", "/TN", background_service.TASK_NAME])
        self.assertEqual(run.call_args_list[1].args[0], ["/End", "/TN", background_service.TASK_NAME])
        self.assertEqual(run.call_args_list[2].args[0], ["/Delete", "/TN", background_service.TASK_NAME, "/F"])
        self.assertEqual(
            run.call_args_list[3].args[0],
            ["/Query", "/TN", background_service.TASK_NAME, "/FO", "LIST"],
        )


if __name__ == "__main__":
    unittest.main()
