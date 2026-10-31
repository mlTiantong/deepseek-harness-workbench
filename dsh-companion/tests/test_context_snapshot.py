"""Reproduce the production Windows reader/rename conflict with a real file handle."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

PLUGIN = (Path(__file__).resolve().parents[1] / 'src/dsh_companion/context_plugin.mjs').as_uri()


@unittest.skipUnless(os.name == 'nt', 'Windows file-sharing regression')
class SnapshotFileTests(unittest.TestCase):
    def test_busy_snapshot_does_not_crash_node_and_publishes_after_reader_closes(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'session.context.json'
            path.write_text('{"revision":1}', encoding='utf-8')
            # Read/write sharing is allowed, replacement (FILE_SHARE_DELETE) is not.
            handle = kernel.CreateFileW(str(path), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value, ctypes.get_last_error())
            script = f"import {{publishJson}} from {json.dumps(PLUGIN)}; console.log(publishJson(process.argv[1], {{revision:2}}));"
            def publish():
                return subprocess.run(['node', '--input-type=module', '-e', script, str(path)],
                                      capture_output=True, text=True, timeout=15)
            try:
                blocked = publish()
                self.assertEqual(blocked.returncode, 0, blocked.stderr)
                self.assertEqual(blocked.stdout.strip(), 'false')
                self.assertEqual(json.loads(path.read_text())['revision'], 1)
            finally:
                kernel.CloseHandle(handle)
            released = publish()
            self.assertEqual(released.returncode, 0, released.stderr)
            self.assertEqual(released.stdout.strip(), 'true')
            self.assertEqual(json.loads(path.read_text())['revision'], 2)
            self.assertFalse(Path(str(path) + '.tmp').exists())


if __name__ == '__main__':
    unittest.main()
