import json
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import httpx
import numpy as np
from PIL import Image
from test_optimization import worker
import test_four_player
from test_four_player import ImmediateExecutor,question,RESULT
from four_player import detect_fun_mode,two_player_room,four_player_room,two_player_start_ready,two_player_random_target,race_snapshot,race_finished,race_match_expired
from answer_client import AIResolver
from task_support import HumanRequired,TaskIncomplete

FIXTURES=Path(__file__).parent/'fixtures/competitive'
ROOM=json.loads((FIXTURES/'double-room.json').read_text('utf-8-sig'))
PROFILE=[dict(text='我要答题',box=[300,460,140,40]),dict(text='学习积分',box=[100,460,140,40])]

class CompetitiveTests(unittest.TestCase):
    def test_real_titleless_double_room_is_identified_from_its_controls(self):
        self.assertTrue(two_player_room(ROOM))
        self.assertEqual(detect_fun_mode(ROOM,True),'双人对战')
        self.assertIsNone(race_snapshot(ROOM,mode='双人对战'))

    def test_real_disabled_double_start_is_not_clicked_as_ready(self):
        image=np.array(Image.open(FIXTURES/'double-room.png'))[:,:,::-1]
        self.assertFalse(two_player_start_ready(ROOM,image))

    def test_network_quality_label_does_not_change_competitive_room_identity(self):
        for quality in ('网络良好','网络较差',''):
            double=[dict(row,text=quality) if row['text']=='网络良好' else row for row in ROOM]
            self.assertEqual(detect_fun_mode(double,True),'双人对战')
            four=[dict(text='四人赛',box=[142,205,436,192]),
                  dict(text=quality,box=[282,992,165,50]),dict(text='开始比赛',box=[286,1123,146,40])]
            self.assertTrue(four_player_room(four))

    def test_double_start_requires_visible_enabled_text(self):
        image=np.array(Image.open(FIXTURES/'double-room.png'))[:,:,::-1].copy()
        x,y,w,h=next(r['box'] for r in ROOM if r['text']=='开始对战')
        image[y:y+h,x:x+w]=255
        self.assertTrue(two_player_start_ready(ROOM,image))

    def test_random_match_targets_the_actual_icon_above_its_label(self):
        image=np.array(Image.open(FIXTURES/'double-room.png'))[:,:,::-1]
        target=two_player_random_target(ROOM,image)
        self.assertEqual(target['text'],'随机匹配')
        x,y,w,h=target['box']
        self.assertTrue(480<x+w/2<560)
        self.assertTrue(550<y+h/2<630)

    def test_double_dispatch_calls_double_handler(self):
        value=worker()
        def opening():value._fun_mode='双人对战';return ROOM
        value._open_fun_challenge=Mock(side_effect=opening)
        value.two_player_answer=Mock()
        value.funny_answer()
        value.two_player_answer.assert_called_once_with(ROOM)
        self.assertEqual(value._fun_metrics['mode'],'双人对战')

    def test_same_mode_entry_waits_for_card_and_clicks_once_during_transition(self):
        value=worker();value._sleep=Mock();value._reset_home=Mock()
        center=[dict(text=text,box=[30,y,140,40]) for text,y in
                [('答题练习',150),('答题竞赛',600),('每日答题',416)]]
        ready=center+[dict(text='双人对战',box=[448,858,157,49])]
        value._fun_records=Mock(side_effect=[center,center,ready,ready,ROOM])
        self.assertEqual(value._open_fun_challenge(target_mode='双人对战'),ROOM)
        value.tasker.controller.post_click.assert_called_once_with(526,882)
        value._reset_home.assert_not_called()

    def test_double_random_match_then_enabled_start_once(self):
        value=test_four_player.FourPlayerTests().race_worker([])
        image=np.array(Image.open(FIXTURES/'double-room.png'))[:,:,::-1].copy()
        value.tasker.controller.cached_image=image.copy()
        x,y,w,h=next(r['box'] for r in ROOM if r['text']=='开始对战')
        image[y:y+h,x:x+w]=255
        q=question();q[0]['text']='双人对战'
        frames=iter([ROOM]+[q]*4+[RESULT])
        def records():value.tasker.controller.cached_image=image;return next(frames)
        value._fun_records=Mock(side_effect=records)
        with patch('four_player.ThreadPoolExecutor',ImmediateExecutor):value.two_player_answer(ROOM)
        self.assertEqual(value._fun_metrics['random_match_clicks'],1)
        self.assertEqual(value._fun_metrics['start_button_clicks'],1)
        self.assertEqual(value._fun_metrics['questions_clicked'],1)
        self.assertEqual([c.args[1] for c in value._fun_click.call_args_list],[[r'随机匹配'],[r'^开始对战$']])

    def test_competitive_settlement_returns_once_without_replay(self):
        value=worker()
        value._sleep=Mock()
        value._fun_metrics=dict(mode='双人对战',round_finished=True)
        value._fun_records=Mock(return_value=PROFILE)
        value._exit_competitive(RESULT)
        self.assertTrue(value._fun_metrics['exited'])
        self.assertEqual(value._fun_metrics['result_back_clicks'],1)
        value.tasker.post_task.assert_called_once_with('返回')

    def test_double_result_returns_through_lobby_and_confirms_exit_once(self):
        confirmation=json.loads((FIXTURES/'double-exit-confirm.json').read_text('utf-8-sig'))
        value=worker();value._sleep=Mock()
        value._fun_metrics=dict(mode='双人对战',round_finished=True)
        value._fun_records=Mock(side_effect=[ROOM,confirmation,confirmation,PROFILE])
        value._exit_competitive(RESULT)
        self.assertTrue(value._fun_metrics['exited'])
        self.assertEqual(value._fun_metrics['double_exit_confirmation_clicks'],1)
        self.assertEqual(value.tasker.post_task.call_count,2)
        value.tasker.controller.post_click.assert_called_once_with(223,724)

    def test_existing_finished_double_round_can_resume_from_exit_confirmation(self):
        confirmation=json.loads((FIXTURES/'double-exit-confirm.json').read_text('utf-8-sig'))
        value=worker();value._sleep=Mock()
        value._fun_metrics=dict(mode='双人对战',round_finished=True)
        value._fun_records=Mock(return_value=PROFILE)
        value._exit_competitive(confirmation)
        self.assertTrue(value._fun_metrics['exited'])
        value.tasker.controller.post_click.assert_called_once_with(223,724)
        value.tasker.post_task.assert_not_called()

    def test_double_exit_confirmation_is_not_accepted_without_settlement(self):
        confirmation=json.loads((FIXTURES/'double-exit-confirm.json').read_text('utf-8-sig'))
        value=worker();value._sleep=Mock()
        value._fun_metrics=dict(mode='双人对战',round_finished=False)
        with self.assertRaisesRegex(TaskIncomplete,'未确认双人结算'):
            value._exit_competitive(confirmation)
        value.tasker.controller.post_click.assert_not_called()

    def test_question_about_match_ending_is_not_a_settlement(self):
        self.assertFalse(race_finished(question('比赛结束后应当如何维护运动场地？')))

    def test_owner_resume_does_not_wait_an_extra_frame_when_live_question_is_valid(self):
        value=worker()
        value._fun_mode='双人对战'
        q=question();q[0]['text']='双人对战'
        value._screen_records=Mock(return_value=q)
        value.tasker.controller.cached_image=np.zeros((1280,720,3),np.uint8)
        self.assertTrue(value._fun_resume_ready())
        value.tasker.controller.post_click.assert_not_called()

    def network_confirm(self):
        return [dict(text='正在匹配中',box=[156,126,409,96]),
            dict(text='网络开小差，请稍后再试',box=[130,602,267,26]),dict(text='确定',box=[532,677,49,29])]

    def test_verification_replaced_by_network_confirm_resumes_to_handle_that_modal(self):
        value=worker()
        value._fun_mode='双人对战'
        value._screen_records=Mock(return_value=self.network_confirm())
        value.tasker.controller.cached_image=np.zeros((1280,720,3),np.uint8)
        self.assertFalse(value._fun_resume_ready())
        self.assertTrue(value._fun_resume_ready())
        value.tasker.controller.post_click.assert_not_called()

    def test_network_confirm_closes_once_and_preserves_failure_context(self):
        value=worker();value._sleep=Mock();value._race_save=Mock()
        value._screen_records=Mock(side_effect=[self.network_confirm(),PROFILE])
        self.assertEqual(value._fun_records(),PROFILE)
        value.tasker.controller.post_click.assert_called_once_with(556,692)
        self.assertTrue(value._fun_metrics['matching_request_failed'])
        self.assertNotIn('round_finished',value._fun_metrics)

    def test_network_confirm_does_not_loop_if_the_same_error_returns(self):
        value=worker();value._sleep=Mock();value._race_save=Mock()
        value._screen_records=Mock(return_value=self.network_confirm())
        with self.assertRaisesRegex(HumanRequired,'再次出现'):value._fun_records()
        self.assertEqual(value.tasker.controller.post_click.call_count,1)

    def test_actual_match_timeout_over_center_is_acknowledged_as_failure(self):
        records=json.loads((FIXTURES/'match-timeout.json').read_text('utf-8'))['records']
        self.assertTrue(race_match_expired(records))
        self.assertIsNone(race_snapshot(records,mode='双人对战'))
        value=worker();value._sleep=Mock();value._race_save=Mock()
        value._screen_records=Mock(side_effect=[records,PROFILE])
        self.assertEqual(value._fun_records(),PROFILE)
        value.tasker.controller.post_click.assert_called_once_with(556,692)
        self.assertTrue(value._fun_metrics['matching_request_failed'])
        self.assertNotIn('round_finished',value._fun_metrics)

    def test_failed_network_reopens_same_mode_once(self):
        value=worker()
        value._fun_metrics=dict(matching_request_failed=True,random_match_clicks=1)
        value._fun_network_retry_count=0
        value._open_fun_challenge=Mock(return_value=ROOM)
        value._fun_click=Mock(return_value=True)
        value.tasker.controller.cached_image=np.array(Image.open(FIXTURES/'double-room.png'))[:,:,::-1]
        self.assertEqual(value._retry_failed_matching(PROFILE,'双人对战'),ROOM)
        value._open_fun_challenge.assert_called_once_with(target_mode='双人对战')
        self.assertEqual(value._fun_network_retry_count,1)
        self.assertEqual(value._fun_metrics['matching_retry_requests'],1)
        value._fun_metrics['matching_request_failed']=True
        with self.assertRaisesRegex(HumanRequired,'一次网络恢复'):value._retry_failed_matching(PROFILE,'双人对战')
        self.assertEqual(value._open_fun_challenge.call_count,1)

    def test_verified_ready_double_start_does_not_click_random_again(self):
        value=test_four_player.FourPlayerTests().race_worker([])
        image=np.array(Image.open(FIXTURES/'double-room.png'))[:,:,::-1].copy()
        value.tasker.controller.cached_image=image.copy()
        x,y,w,h=next(r['box'] for r in ROOM if r['text']=='开始对战')
        image[y:y+h,x:x+w]=255
        q=question();q[0]['text']='双人对战'
        # A slow transition may retain the room for two frames after Start.
        frames=iter([ROOM]*3+[q]*4+[RESULT])
        def records():
            value.tasker.controller.cached_image=image
            value._fun_verification_generation=1
            return next(frames)
        value._fun_records=Mock(side_effect=records)
        with patch('four_player.ThreadPoolExecutor',ImmediateExecutor):value.two_player_answer(ROOM)
        self.assertEqual(value._fun_metrics['random_match_clicks'],1)
        self.assertEqual(value._fun_metrics['start_button_clicks'],1)
        self.assertEqual(value._fun_metrics['post_verification_start_clicks'],0)

    def test_owner_verification_resumes_after_two_double_room_frames_without_input(self):
        value=worker()
        value._screen_records=Mock(side_effect=[[dict(text='访问验证',box=[303,134,113,31])],ROOM,ROOM])
        value.tasker.controller.cached_image=np.zeros((1280,720,3),np.uint8)
        self.assertFalse(value._fun_resume_ready())
        self.assertFalse(value._fun_resume_ready())
        self.assertTrue(value._fun_resume_ready())
        value.tasker.controller.post_click.assert_not_called()

    def test_text_questions_skip_image_upload_but_picture_questions_keep_it(self):
        resolver=AIResolver('offline','deepseek-flash');resolver.session.close()
        captures=[]
        def transport(request):
            captures.append(json.loads(request.content))
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"index":0,"uncertain":false}'},'finish_reason':'stop'}]})
        resolver.session=httpx.Client(transport=httpx.MockTransport(transport))
        try:
            for q in ['中国的首都是哪座城市？','根据下图，哪座城市是首都？']:
                self.assertEqual(resolver.resolve_race_choice([np.zeros((100,200,3),np.uint8)],['北京','上海'],q),0)
            contents=[item['messages'][1]['content'] for item in captures]
            self.assertFalse(any(part['type']=='image_url' for part in contents[0]))
            self.assertTrue(any(part['type']=='image_url' for part in contents[1]))
        finally:resolver.session.close()

if __name__=='__main__':unittest.main()
