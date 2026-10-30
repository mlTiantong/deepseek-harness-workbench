"""Pause stops only the process tree owned by this runner."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import psutil
import run


class RunnerTests(unittest.TestCase):
    def test_pause_terminates_owned_process_but_keeps_other_process(self):
        with tempfile.TemporaryDirectory() as directory:
            owned = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
            other = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
            path = Path(directory) / "owned.json"
            path.write_text(json.dumps({"pid": owned.pid, "created": psutil.Process(owned.pid).create_time()}))
            try:
                run.stop_record(path)
                self.assertIsNotNone(owned.poll())
                self.assertIsNone(other.poll())
                self.assertFalse(path.exists())
            finally:
                for process in (owned, other):
                    if process.poll() is None:
                        process.terminate()
                    process.wait()

    def test_reused_pid_is_not_terminated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stale.json"
            path.write_text(json.dumps({"pid": psutil.Process().pid, "created": 0}))
            with self.assertRaises(RuntimeError):
                run.stop_record(path)


if __name__ == "__main__":
    unittest.main()
