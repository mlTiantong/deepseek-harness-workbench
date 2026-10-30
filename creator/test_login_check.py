import unittest
from unittest.mock import Mock, patch
import login_check as check
import console_rpc

class LoginCheckTests(unittest.TestCase):
    def test_check_rpc_needs_no_session_or_model(self):
        with patch.object(check,"check_and_record",return_value={"status":"logged_out"}) as direct:
            self.assertEqual(console_rpc.call("login",{"platform":"douyin","action":"check"}),{"status":"logged_out"})
            direct.assert_called_once_with("douyin")
    def test_bili_verified_requires_real_account(self):
        page=Mock();page.evaluate.return_value={"code":0,"data":{"isLogin":True,"mid":123,"uname":"test"}}
        self.assertEqual(check.inspect_page("bilibili",page)["account"],"123")
    def test_bili_expired(self):
        page=Mock();page.evaluate.return_value={"code":-101}
        self.assertEqual(check.inspect_page("bilibili",page)["status"],"logged_out")
    def test_bili_network_error_not_logged_out(self):
        page=Mock();page.evaluate.return_value={"code":-412}
        self.assertEqual(check.inspect_page("bilibili",page)["status"],"unknown")
    def test_douyin_login_button_is_not_logged_in(self):
        page=Mock();page.get_by_text.return_value.count.return_value=1
        page.get_by_text.return_value.nth.return_value.is_visible.return_value=True
        self.assertEqual(check.inspect_page("douyin",page)["status"],"logged_out")
        page.evaluate.assert_not_called()
    def test_no_account_never_fabricates_verified(self):
        page=Mock();page.get_by_text.return_value.count.return_value=0;page.evaluate.return_value=None
        self.assertEqual(check.inspect_page("douyin",page)["status"],"unknown")
    def test_desktop_not_ai_fallback(self):
        self.assertEqual(check.check_platform("qq")["status"],"unavailable")
    def test_closed_browser_is_not_started(self):
        with patch.object(check,"owned_browser",return_value=False):
            self.assertEqual(check.check_platform("douyin")["status"],"unavailable")

if __name__=="__main__":unittest.main()
