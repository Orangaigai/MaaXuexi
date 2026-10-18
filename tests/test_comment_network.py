"""A failed comment page may retry once; a real challenge remains owner-only."""
import unittest
from unittest.mock import Mock

from test_optimization import worker
import test_owner_continuation as owner
from task_support import HumanRequired, TaskIncomplete

CAPTCHA, PROFILE = owner.CAPTCHA, owner.PROFILE
NETWORK = [dict(text='网络开小差，请稍后再试', box=[209, 606, 298, 30]),
           dict(text='重试', box=[456, 706, 76, 44])]


class CommentNetworkTests(unittest.TestCase):
    setUp = owner.OwnerContinuationTests.setUp
    advance = owner.OwnerContinuationTests.advance

    def guarded(self, frames):
        value = worker()
        del value._guard_verification
        value._screen_records = Mock(side_effect=frames)
        return value

    def test_network_error_inside_verification_retries_only_the_explicit_button(self):
        value = self.guarded([CAPTCHA + NETWORK, CAPTCHA])
        self.assertFalse(value._comment_resume_ready())
        value.tasker.controller.post_click.assert_called_once_with(494, 728)
        self.assertFalse(value._comment_resume_ready())
        self.assertEqual(value.tasker.controller.post_click.call_count, 1)
        value.tasker.controller.post_swipe.assert_not_called()
        value.tasker.post_task.assert_not_called()

    def test_second_network_failure_ends_instead_of_looping(self):
        value = worker()
        self.assertTrue(value._comment_retry_network(NETWORK))
        self.clock = 1
        self.assertTrue(value._comment_retry_network(NETWORK))
        self.clock = 5
        with self.assertRaises(HumanRequired):
            value._comment_retry_network(NETWORK)
        self.assertEqual(value.tasker.controller.post_click.call_count, 1)

    def test_retry_word_without_network_error_is_not_clicked(self):
        value = worker()
        self.assertFalse(value._comment_retry_network(NETWORK[1:]))
        value.tasker.controller.post_click.assert_not_called()

    def test_ambiguous_or_missing_retry_control_preserves_the_page(self):
        for controls in ([dict(text='确定', box=[456, 706, 76, 44])],
                         [NETWORK[1], dict(text='重试', box=[100, 800, 76, 44])]):
            with self.subTest(controls=controls):
                value = worker()
                with self.assertRaises(HumanRequired):
                    value._comment_retry_network([NETWORK[0]] + controls)
                value.tasker.controller.post_click.assert_not_called()

    def test_no_input_when_another_app_is_in_front(self):
        value = worker()
        value._app_in_foreground.return_value = False
        with self.assertRaises(HumanRequired):
            value._comment_retry_network(NETWORK)
        value.tasker.controller.post_click.assert_not_called()

    def test_failed_touch_is_terminal_not_repeated(self):
        value = worker()
        value.tasker.controller.post_click.return_value.wait.return_value.succeeded = False
        with self.assertRaises(HumanRequired):
            value._comment_retry_network(NETWORK)
        self.clock = 5
        with self.assertRaises(HumanRequired):
            value._comment_retry_network(NETWORK)
        self.assertEqual(value.tasker.controller.post_click.call_count, 1)

    def test_same_guard_resumes_after_retry_and_owner_action_without_browser_input(self):
        value = self.guarded([CAPTCHA, CAPTCHA + NETWORK, CAPTCHA, PROFILE, PROFILE, PROFILE])
        value._guard_verification(resume_when=value._comment_resume_ready)
        self.assertFalse(value.pause_flag)
        value.tasker.controller.post_click.assert_called_once_with(494, 728)
        value.tasker.controller.post_swipe.assert_not_called()
        value.tasker.post_task.assert_not_called()

    def test_plain_network_page_is_not_misread_as_closed_editor(self):
        value = worker()
        value._screen_records = Mock(side_effect=[NETWORK, PROFILE])
        value._sleep = Mock(side_effect=self.advance)
        observed = Mock(return_value=True)
        self.assertEqual(value._comment_wait(observed), PROFILE)
        observed.assert_called_once_with(PROFILE)
        value.tasker.controller.post_click.assert_called_once_with(494, 728)

    def test_terminal_owner_callback_failure_is_not_swallowed_by_pause(self):
        value = worker()
        with self.assertRaisesRegex(HumanRequired, '重试已用完'):
            value.pause('本人验证', resume_when=Mock(side_effect=HumanRequired('重试已用完')))
        self.assertFalse(value.pause_flag)
        value.tasker.controller.post_click.assert_not_called()

    def test_transient_screen_read_failure_still_allows_same_pause_to_continue(self):
        value = worker()
        value.pause('本人验证', resume_when=Mock(side_effect=[TaskIncomplete('临时读屏失败'), True]))
        self.assertFalse(value.pause_flag)


if __name__ == '__main__':
    unittest.main()
