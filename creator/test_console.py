import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import state_store as store
import console_store as console

class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old_root,self.old_state=store.ROOT,store.STATE
        store.ROOT=Path(self.tmp.name);store.STATE=store.ROOT/"state"
        (store.ROOT/"config.json").write_text(json.dumps({"timezone":"Asia/Shanghai","platforms":list(console.PLATFORMS),"daily_limits":{"post":2,"message":20},"autonomous_publish":True,"autonomous_chat":True,"max_rounds_per_day":24}))
        console.save_settings({"reply_quiet_start":0,"reply_quiet_end":0})
    def tearDown(self):
        store.ROOT,store.STATE=self.old_root,self.old_state
        self.tmp.cleanup()
    def receive(self,mid="m1",platform="bilibili"):
        return console.ingest(platform,mid,"thread-1","sender","短摘要","observed stable message id on inbox page")
    def verify(self,platform="bilibili"):
        console.report_platform(platform,"verified","actual-account","observed account profile and inbox")
    def test_settings_reject_unknown_and_invalid_types(self):
        for value in ({"arbitrary_path":"x"},{"reply_daily_limit":True},{"reply_platforms":["bad"]},{"reply_daily_limit":-1}):
            with self.assertRaises(ValueError):console.save_settings(value)
    def test_unknown_accounts_are_not_logged_in(self):
        self.assertTrue(all(p["status"]=="unknown" for p in console.snapshot()["platforms"]))
        self.receive();self.assertEqual(console.reserve_reply("bilibili","m1","hello")["reason"],"account_not_recently_verified")
    def test_dedup_and_verified_finish_appear_in_inbox(self):
        self.verify();self.receive();self.receive()
        first=console.reserve_reply("bilibili","m1","hello");self.assertTrue(first["allowed"])
        self.assertFalse(console.reserve_reply("bilibili","m1","hello")["allowed"])
        store.action_finish(first["action_id"],"published","observed sent bubble and exact recipient")
        self.assertEqual(console.snapshot()["inbox"][0]["status"],"replied")
        self.assertEqual(len(console.snapshot()["inbox"]),1)
    def test_uncertain_never_blind_retries(self):
        self.verify();self.receive();first=console.reserve_reply("bilibili","m1","hello")
        store.action_finish(first["action_id"],"uncertain","send result unknown")
        self.assertEqual(console.snapshot()["inbox"][0]["status"],"uncertain")
        self.assertFalse(console.reserve_reply("bilibili","m1","different text")["allowed"])
    def test_reply_limits_zero_unlimited_and_positive_values_apply(self):
        self.verify()
        for i in range(25):
            self.receive(str(i))
            self.assertTrue(console.reserve_reply("bilibili",str(i),"reply")["allowed"])
        console.save_settings({"reply_daily_limit":25,"reply_per_contact_daily_limit":0})
        self.receive("limited-1")
        self.assertEqual(console.reserve_reply("bilibili","limited-1","reply")["reason"],"daily_reply_limit")
        console.save_settings({"reply_daily_limit":0,"reply_per_contact_daily_limit":25})
        self.assertEqual(console.reserve_reply("bilibili","limited-1","reply")["reason"],"per_contact_reply_limit")
        console.save_settings({"reply_daily_limit":1000,"reply_per_contact_daily_limit":1000})
        self.assertTrue(console.reserve_reply("bilibili","limited-1","reply")["allowed"])
    def test_pause_and_disabled_platform(self):
        self.verify();self.receive();(store.ROOT/"PAUSED").write_text("paused")
        self.assertEqual(console.reserve_reply("bilibili","m1","hello")["reason"],"paused_or_chat_disabled")
        console.save_settings({"reply_platforms":[]})
        self.assertEqual(console.reserve_reply("bilibili","m1","hello")["reason"],"reply_disabled")
    def test_concurrent_reservation_exactly_once(self):
        self.verify();self.receive()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            rows=list(pool.map(lambda _:console.reserve_reply("bilibili","m1","hello"),range(5)))
        self.assertEqual(sum(x["allowed"] for x in rows),1)
    def test_owner_route_must_be_verified(self):
        (store.ROOT/"OWNER_CONTACT.json").write_text('{"routes":[],"status":"pending"}')
        with self.assertRaises(ValueError):console.save_settings({"owner_contact_enabled":True})
    def test_switching_account_cannot_reply_to_previous_inbox(self):
        self.verify();self.receive()
        console.report_platform("bilibili","verified","different-account","observed new account on page")
        self.assertEqual(console.reserve_reply("bilibili","m1","hello")["reason"],"inbox_account_mismatch_reobserve_required")
        with self.assertRaises(ValueError):self.receive()
    def test_publish_switch_and_cap(self):
        console.save_settings({"video_auto_publish":False})
        self.assertEqual(store.action_begin("bilibili","post","own","test","one")["reason"],"console_publish_disabled")
    def test_stale_account_and_quiet_hours(self):
        self.receive();self.verify()
        with store.connect() as db:db.execute("UPDATE platform_checks SET checked_at='2020-01-01T12:00:00+08:00'")
        self.assertEqual(console.reserve_reply("bilibili","m1","hello")["reason"],"account_not_recently_verified")
        self.verify();self.receive();console.save_settings({"reply_quiet_start":23,"reply_quiet_end":9})
        with patch.object(store,"now",return_value="2026-10-27T23:30:00+08:00"):
            self.assertEqual(console.reserve_reply("bilibili","m1","hello")["reason"],"quiet_hours")

if __name__=="__main__":unittest.main()
