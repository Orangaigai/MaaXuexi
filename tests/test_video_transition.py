"""Real video progression can disappear near a clip transition."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from content_tasks import ContentTasks
from task_support import HumanRequired, TaskIncomplete
from test_optimization import worker,job


class VideoTransitionTests(unittest.TestCase):
    def test_supplementary_video_feed_is_selected_before_opening_a_clip(self):
        value=worker();value._sleep=Mock()
        value.detect=Mock(return_value=([[15,1017,700,169]],['video']))
        value._has_unread_text=Mock(return_value=True)
        value.tasker.controller.post_screencap.return_value=job()
        value.tasker.controller.post_screencap.return_value.get.return_value=np.zeros((1280,720,3),np.uint8)
        value.watch_video(duration=65,alternate_feed=True)
        self.assertEqual([call.args[0] for call in value.tasker.post_task.call_args_list],
                         ['电视台','本机视频视野频道','返回'])
        value.tasker.controller.post_click.assert_called_once_with(165,1027)

    def test_missing_alternate_channel_does_not_click_or_scroll_an_unconfirmed_page(self):
        value=worker();value._sleep=Mock()
        value.tasker.post_task.side_effect=[job(),job(False)]
        with self.assertRaises(TaskIncomplete):value.watch_video(alternate_feed=True)
        value.tasker.controller.post_click.assert_not_called()
        value.tasker.controller.post_swipe.assert_not_called()

    def test_native_large_video_card_is_supported_without_reading_thumbnail_as_title(self):
        value=worker();value._sleep=Mock()
        value.detect=Mock(return_value=([[21,263,685,474]],['video_big']))
        value._has_unread_text=Mock(return_value=False)
        value.tasker.controller.post_screencap.return_value=job()
        value.tasker.controller.post_screencap.return_value.get.return_value=np.zeros((1280,720,3),np.uint8)
        value.watch_video(duration=65)
        value.tasker.controller.post_click.assert_called_once_with(171,273)
        value._has_unread_text.assert_not_called()
        self.assertEqual(value.detect.call_count,1)

    def test_opened_video_marker_does_not_block_an_unfinished_score(self):
        value=worker();value._sleep=Mock()
        value.detect=Mock(return_value=([[10,300,400,300]],['video']))
        value._has_unread_text=Mock(return_value=False)
        value.tasker.controller.post_screencap.return_value=job()
        value.tasker.controller.post_screencap.return_value.get.return_value=np.zeros((1280,720,3),np.uint8)
        value.watch_video(duration=65)
        value.tasker.controller.post_click.assert_called_once_with(160,310)
        self.assertEqual(value.detect.call_count,3)

    def value(self, error):
        value=worker()
        value._sleep=Mock()
        value._watch_video_segment=ContentTasks._watch_video_segment.__get__(value)
        value._video_progress=Mock(side_effect=[
            dict(position=66,total=131),dict(position=77,total=131),error])
        return value

    def test_hidden_progress_keeps_confirmed_time_without_counting_unknown_interval(self):
        value=self.value(TaskIncomplete('无法读取视频实际播放进度，已停止计时'))
        self.assertEqual(value._watch_video_segment(65),10)
        self.assertEqual(value._sleep.call_count,2)

    def test_lost_video_page_stops_instead_of_treating_navigation_as_playback(self):
        value=self.value(TaskIncomplete('progress missing'))
        value._content_page_ready=Mock(side_effect=[True,True,False])
        with self.assertRaises(TaskIncomplete):
            value._watch_video_segment(65)

    def test_access_verification_is_never_swallowed_as_video_transition(self):
        value=self.value(HumanRequired('本人访问验证'))
        with self.assertRaises(HumanRequired):
            value._watch_video_segment(65)

    def test_new_clip_without_readable_progress_switches_without_counting_time(self):
        value=self.value(TaskIncomplete('unused'))
        value._video_progress=Mock(side_effect=TaskIncomplete('initial progress missing'))
        self.assertEqual(value._watch_video_segment(65),0)
        value._sleep.assert_not_called()

    def test_missing_initial_progress_on_lost_page_still_stops(self):
        value=self.value(TaskIncomplete('unused'))
        value._video_progress=Mock(side_effect=TaskIncomplete('initial progress missing'))
        value._content_page_ready=Mock(return_value=False)
        with self.assertRaises(TaskIncomplete):value._watch_video_segment(65)

    def test_initial_verification_never_becomes_a_skipped_clip(self):
        value=self.value(TaskIncomplete('unused'))
        value._video_progress=Mock(side_effect=HumanRequired('本人验证'))
        with self.assertRaises(HumanRequired):value._watch_video_segment(65)


if __name__=='__main__':unittest.main()
