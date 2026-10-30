"""Checks for durable recall, duplicate sends, quotas and concurrent reservations."""
import concurrent.futures
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import state_store as store


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.old_root, self.old_state = store.ROOT, store.STATE
        store.ROOT = Path(self.directory.name)
        store.STATE = store.ROOT / "state"
        (store.ROOT / "config.json").write_text(json.dumps({"timezone": "Asia/Shanghai", "platforms": ["bilibili", "douyin"],
            "daily_limits": {"post": 2, "comment": 8, "message": 20, "like": 30},
            "autonomous_publish": True, "autonomous_chat": True, "max_rounds_per_day": 24}), encoding="utf-8")

    def tearDown(self):
        store.ROOT, store.STATE = self.old_root, self.old_state
        self.directory.cleanup()

    def reserve(self, key):
        return store.action_begin("bilibili", "post", "own-account", "测试作品", key)

    def test_uncertain_result_survives_reopen_and_blocks_repeat(self):
        first = self.reserve("content-1")
        store.action_finish(first["action_id"], "uncertain", "Upload request timed out")
        second = self.reserve("content-1")
        self.assertFalse(second["allowed"])
        self.assertEqual(second["existing"]["status"], "uncertain")
        result = store.action_finish(first["action_id"], "published", "https://example.com/post/1")
        self.assertEqual(result["status"], "published")
        self.assertEqual(store.action_finish(first["action_id"], "published", "https://example.com/post/1"), result)
        with self.assertRaises(ValueError):
            store.action_finish(first["action_id"], "failed", "incorrect overwrite")

    def test_cross_platform_daily_budget_counts_reserved_actions(self):
        self.assertTrue(self.reserve("content-1")["allowed"])
        self.assertTrue(store.action_begin("douyin", "post", "own-account", "another post", "content-2")["allowed"])
        self.assertEqual(self.reserve("content-3")["reason"], "daily_limit")

    def test_concurrent_workers_cannot_double_reserve(self):
        with store.connect():
            pass
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.reserve, ["same-content"] * 8))
        self.assertEqual(sum(r["allowed"] for r in results), 1)

    def test_chinese_memory_is_deduplicated_and_preserves_evidence(self):
        first = store.remember("interest", "今天研究了生物芯片，想继续学习神经接口", "https://example.com/research", 0.8)
        self.assertEqual(first, store.remember("interest", "今天研究了生物芯片，想继续学习神经接口", "https://example.com/research", 0.8))
        result = store.recall("神经接口下一步")
        self.assertEqual(result[0]["id"], first["id"])
        self.assertEqual(result[0]["confidence"], 0.8)

    def test_pause_blocks_actions_and_failed_actions_release_budget(self):
        first = self.reserve("content-1")
        store.action_finish(first["action_id"], "failed", "No upload was attempted")
        self.assertTrue(self.reserve("content-2")["allowed"])
        self.assertTrue(self.reserve("content-3")["allowed"])
        (store.ROOT / "PAUSED").write_text("test")
        self.assertEqual(self.reserve("content-4")["reason"], "paused")


if __name__ == "__main__":
    unittest.main()
