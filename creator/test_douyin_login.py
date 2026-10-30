"""No network/login is performed by these regression checks."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import douyin_login as login


class LoginTests(unittest.TestCase):
    def test_busy_profile_is_not_killed_or_relaunched(self):
        with patch.object(login, "debug_ready", return_value=False), patch.object(login, "profile_processes", return_value=[{"pid": 1, "args": []}]), patch.object(login.subprocess, "Popen") as launch, redirect_stdout(io.StringIO()):
            self.assertEqual(login.open_login(), 1)
            launch.assert_not_called()

    def test_foreign_debug_browser_is_rejected(self):
        with patch.object(login, "debug_ready", return_value=True), patch.object(login, "profile_processes", return_value=[]), patch.object(login.subprocess, "Popen") as launch, redirect_stdout(io.StringIO()):
            self.assertEqual(login.open_login(), 1)
            launch.assert_not_called()

    def test_existing_correct_browser_is_reused_without_refreshing_qr(self):
        processes = [{"pid": 1, "args": ["--remote-debugging-port=9333"]}]
        with patch.object(login, "debug_ready", return_value=True), patch.object(login, "profile_processes", return_value=processes), patch.object(login.subprocess, "Popen") as launch, redirect_stdout(io.StringIO()):
            self.assertEqual(login.open_login(), 0)
            launch.assert_not_called()

    def test_visible_launch_uses_plugin_profile_and_local_debugging(self):
        with patch.object(login, "debug_ready", side_effect=[False, True]), patch.object(login, "profile_processes", return_value=[]), patch.object(login, "chrome_exe", return_value=Path("chrome.exe")), patch.object(login.subprocess, "Popen") as launch, redirect_stdout(io.StringIO()):
            self.assertEqual(login.open_login(), 0)
            args = launch.call_args.args[0]
            self.assertIn(f"--user-data-dir={login.PROFILE}", args)
            self.assertIn("--remote-debugging-address=127.0.0.1", args)
            self.assertFalse(any("headless" in arg or "AutomationControlled" in arg for arg in args))

    def test_check_does_not_start_browser(self):
        with patch.object(login, "debug_ready", return_value=False), patch.object(login.subprocess, "Popen") as launch, redirect_stdout(io.StringIO()):
            self.assertEqual(login.check_login(), 1)
            launch.assert_not_called()


from pathlib import Path
if __name__ == "__main__":
    unittest.main()
