from test_console import ConsoleTests
import fast_reply_quota as quota

class FastQuotaTests(ConsoleTests):
    def test_fast_and_regular_share_daily_budget(self):
        self.verify();self.receive()
        import console_store as console
        console.save_settings({"reply_daily_limit":1})
        first=quota.call({"method":"reserve","account":"a","contact":"thread-1"})
        self.assertTrue(first["allowed"])
        self.assertEqual(console.reserve_reply("bilibili","m1","reply")["reason"],"daily_reply_limit")
        quota.call({"method":"finish","account":"a","contact":"thread-1","id":first["id"],"status":"failed"})
        self.assertTrue(console.reserve_reply("bilibili","m1","reply")["allowed"])
