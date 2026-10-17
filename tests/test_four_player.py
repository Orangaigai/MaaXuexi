"""Mode routing, one match, live-question validation and actual first-round credit."""
import json
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import numpy as np
from PIL import Image

from test_optimization import worker
from answer_client import AIResolver
from four_player import detect_fun_mode, race_snapshot, race_finished, four_player_room, race_network_error
from task_support import HumanRequired, TaskIncomplete, TaskCancelled, TaskSkipped, fun_first_round_credited
import task_support


def row(text,x,y,w=500,h=35):
    return dict(text=text,box=[x,y,w,h])


def question(text='下列哪一个是中国的首都？'):
    return [row('四人赛',290,60,140),row('第1题',300,190,110),row(text,50,300),
            row('A. 北京',65,480),row('B. 上海',65,600),row('C. 广州',65,720),row('D. 深圳',65,840)]


ROOM = [row('四人赛',142,205,436,192),row('开始比赛',286,1123,146,40)]
READY_ROOM = ROOM+[row('网络良好',282,992,165,50)]
RESULT = [row('本局排名',270,250,180),row('第二名',280,400,140),row('回顾本局',250,1100,200)]
RULES = '四人赛首局胜利积10分，未获胜积8分，一局定胜负。'


class ImmediateExecutor:
    def __init__(self,**kwargs):
        pass

    def submit(self,fn,*args):
        future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as error:
            future.set_exception(error)
        return future

    def shutdown(self,**kwargs):
        pass


class FourPlayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.report_patch = patch.object(task_support,'REPORT_PATH',Path(self.temp.name)/'last-run.json')
        self.report_patch.start()
        self.addCleanup(self.report_patch.stop)

    def race_worker(self,frames):
        value = worker()
        value._fun_metrics = dict(questions_clicked=0,model_calls=0,model_seconds=[])
        value._race_save = Mock()
        value._exit_competitive = Mock()
        value._sleep = Mock()
        value._fun_click = Mock(return_value=True)
        value._fun_records = Mock(side_effect=frames)
        value.tasker.controller.cached_image = np.zeros((1280,720,3),np.uint8)
        value.ai_resolver = SimpleNamespace(resolve_race_choice=Mock(return_value=0))
        return value

    def test_large_actual_room_title_is_detected_only_after_today_entry(self):
        path = Path(__file__).parent/'fixtures/four-player/ocr.json'
        records = json.loads(path.read_text('utf-8'))
        self.assertIsNone(detect_fun_mode(records))
        self.assertEqual(detect_fun_mode(records,True),'四人赛')

    def test_current_four_player_room_is_recognized_without_matching_or_reopening(self):
        value = worker()
        value._fun_records = Mock(return_value=READY_ROOM)
        value._reset_home = Mock()
        self.assertEqual(value._open_fun_challenge(),READY_ROOM)
        value._reset_home.assert_not_called()
        self.assertEqual(value._fun_mode,'四人赛')
        value.tasker.controller.post_click.assert_not_called()
        value.tasker.post_task.assert_not_called()

    def test_ready_room_requires_actual_mode_and_start_button(self):
        self.assertTrue(four_player_room(READY_ROOM))
        self.assertTrue(four_player_room(ROOM))
        self.assertFalse(four_player_room(ROOM[:1]))
        self.assertFalse(four_player_room(question()))

    def test_owner_verified_return_to_lobby_continues_start_once(self):
        value = self.race_worker([])
        frames = iter([READY_ROOM]+[question()]*4+[RESULT])
        def records():
            frame = next(frames)
            if frame is READY_ROOM:
                value._fun_verification_generation = 1
            return frame
        value._fun_records = Mock(side_effect=records)
        with patch('four_player.ThreadPoolExecutor',ImmediateExecutor):
            value.four_player_answer(READY_ROOM)
        self.assertEqual(value._fun_click.call_count,2)
        self.assertEqual(value._fun_metrics['post_verification_start_clicks'],1)
        self.assertEqual(value._fun_metrics['rounds_started'],1)
        self.assertTrue(value._fun_metrics['round_finished'])

    def test_plain_lobby_wait_does_not_repeat_start_without_owner_verification(self):
        value = self.race_worker([READY_ROOM])
        with patch('four_player.time.monotonic',side_effect=[0,1,81]):
            with self.assertRaisesRegex(TaskIncomplete,'未重复匹配'):
                value.four_player_answer(READY_ROOM)
        value._fun_click.assert_called_once()

    def test_resume_generation_is_not_counted_until_verification_is_absent(self):
        value = worker()
        verification = [row('访问验证',200,300)]
        value._screen_records = Mock(side_effect=[verification,READY_ROOM])
        value.pause = Mock()
        self.assertEqual(value._fun_records(),READY_ROOM)
        value.pause.assert_called_once()
        self.assertEqual(value._fun_verification_generation,1)

    def test_repeated_owner_verified_lobby_returns_do_not_loop_start(self):
        value = self.race_worker([])
        def records():
            value._fun_verification_generation = getattr(value,'_fun_verification_generation',0)+1
            return READY_ROOM
        value._fun_records = Mock(side_effect=records)
        with self.assertRaisesRegex(TaskIncomplete,'停止自动重试'):
            value.four_player_answer(READY_ROOM)
        self.assertEqual(value._fun_click.call_count,2)
        value.ai_resolver.resolve_race_choice.assert_not_called()

    def test_real_matching_network_error_is_not_a_question(self):
        records = json.loads((Path(__file__).parent/'fixtures/four-player-network/ocr.json').read_text('utf-8'))
        self.assertEqual(race_network_error(records),'网络开小差，请稍后再试')
        self.assertIsNone(race_snapshot(records))

    def test_network_error_stops_promptly_without_retry_or_model_call(self):
        records = json.loads((Path(__file__).parent/'fixtures/four-player-network/ocr.json').read_text('utf-8'))
        value = self.race_worker([records])
        with self.assertRaisesRegex(TaskIncomplete,'网络开小差'):
            value.four_player_answer(READY_ROOM)
        value._fun_click.assert_called_once()
        value.ai_resolver.resolve_race_choice.assert_not_called()
        self.assertEqual(value._fun_metrics['network_error'],'网络开小差，请稍后再试')

    def test_question_mentions_network_error_without_popup_is_still_a_question(self):
        records = question('遇到网络开小差，请稍后再试，应该怎样处理？')
        self.assertIsNone(race_network_error(records))
        self.assertIsNotNone(race_snapshot(records))

    def test_multiple_center_cards_do_not_choose_a_mode(self):
        self.assertIsNone(detect_fun_mode([row('四人赛',50,250),row('挑战答题',50,350)],True))

    def test_four_player_dispatch_calls_the_competitive_handler(self):
        value = worker()
        def opening():
            value._fun_mode = '四人赛'
            return ROOM
        value._open_fun_challenge = Mock(side_effect=opening)
        value.four_player_answer = Mock()
        value.funny_answer()
        value.four_player_answer.assert_called_once_with(ROOM)
        self.assertEqual(value._fun_metrics['mode'],'四人赛')
        self.assertEqual(value._fun_metrics['questions_clicked'],0)
        self.assertEqual(value._fun_metrics['model_calls'],0)
        self.assertNotIn('skipped_by_policy',value._fun_metrics)

    def test_real_today_four_player_room_routes_to_handler_after_today_entry(self):
        records = json.loads((Path(__file__).parent/'fixtures/four-player/ocr.json').read_text('utf-8'))
        value = worker()
        value._reset_home = Mock()
        value._sleep = Mock()
        value._fun_records = Mock(side_effect=[[], [row('我的',650,60)],
            [row('我要答题',40,300)], [row('今日趣味答题',300,500)], records])
        value.four_player_answer = Mock()
        value.funny_answer()
        value.four_player_answer.assert_called_once_with(records)
        # The only coordinate click is the known "今日趣味答题" entry, never start.
        value.tasker.controller.post_click.assert_called_once_with(550,518)
        self.assertEqual(value._fun_metrics['model_calls'],0)

    def test_four_player_skip_continues_daily_and_does_not_claim_fun_points(self):
        value = worker()
        value._finish_existing_quiz = Mock()
        value.funny_answer = Mock(side_effect=TaskSkipped('四人赛按设置跳过'))
        value.daily_answer = Mock()
        value.read_learning_score = Mock(side_effect=[{
            '趣味答题':dict(value=0,maximum=10),'每日答题':dict(value=0,maximum=5)},
            {'每日答题':dict(value=5,maximum=5)}])
        with patch('utils.plyer.notification.notify') as notify:
            value.task(['趣味答题','每日答题'],unattended=True)
        result = value.run_report.snapshot()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['tasks']['趣味答题']['state'],'skipped_mode')
        self.assertEqual(result['tasks']['趣味答题']['after']['value'],0)
        self.assertEqual(result['tasks']['每日答题']['state'],'verified')
        self.assertEqual(result['completed_tasks'],['每日答题'])
        self.assertEqual(result['skipped_tasks'],['趣味答题'])
        self.assertEqual(result['unfinished_tasks'],[])
        value.daily_answer.assert_called_once()
        notify.assert_not_called()

    def test_daily_verification_after_four_player_skip_still_requires_owner(self):
        value = worker()
        value._finish_existing_quiz = Mock()
        value.funny_answer = Mock(side_effect=TaskSkipped('四人赛按设置跳过'))
        value.daily_answer = Mock(side_effect=HumanRequired('每日答题需本人验证'))
        value.read_learning_score = Mock(return_value={
            '趣味答题':dict(value=0,maximum=10),'每日答题':dict(value=0,maximum=5)})
        with patch('utils.plyer.notification.notify'):
            value.task(['趣味答题','每日答题'],unattended=True)
        result = value.run_report.snapshot()
        self.assertEqual(result['status'],'needs_human')
        self.assertEqual(result['unfinished_tasks'],['每日答题'])
        self.assertEqual(result['skipped_tasks'],['趣味答题'])
        self.assertEqual(result['tasks']['趣味答题']['state'],'skipped_mode')

    def test_complete_question_and_multiline_options_parse(self):
        records = question()+[row('中华人民共和国首都',100,520)]
        snap = race_snapshot(records)
        self.assertEqual(len(snap['options']),4)
        self.assertIn('中华人民共和国首都',snap['options'][0]['text'])
        self.assertEqual(snap['options'][1]['text'],'上海')
        self.assertNotEqual(snap['key'],race_snapshot(question('下列哪一个是广东省省会？'))['key'])

    def test_duplicates_or_incomplete_option_letters_are_not_clickable(self):
        self.assertIsNone(race_snapshot(question()+[row('A. 重复',60,950)]))
        self.assertIsNone(race_snapshot([r for r in question() if not r['text'].startswith('C.')]))

    def test_lobby_countdown_and_feedback_are_not_questions(self):
        self.assertIsNone(race_snapshot(ROOM))
        self.assertIsNone(race_snapshot([row('3',300,300)]))
        self.assertIsNone(race_snapshot(question()+[row('正确答案：A',50,1000)]))

    def test_unlabeled_choices_require_actual_wide_button_bands(self):
        image = np.full((1280,720,3),(210,90,40),np.uint8)
        for y in (480,620,760,900):
            image[y:y+90,60:660] = 245
        records = [row('四人赛',290,60,140),row('下列哪一个是中国的首都？',50,300)]
        records += [row(text,100,y) for text,y in zip(['北京','上海','广州','深圳'],(500,640,780,920))]
        snap = race_snapshot(records,image)
        self.assertEqual([r['text'] for r in snap['options']],['北京','上海','广州','深圳'])
        self.assertIsNone(race_snapshot(records,np.full((1280,720,3),245,np.uint8)))

    def score_fixture(self):
        folder = Path(__file__).parent/'fixtures/four-player-score-page'
        return json.loads((folder/'ocr.json').read_text('utf-8')), np.array(Image.open(folder/'screen.png'))[:,:,::-1]

    def test_real_score_center_white_cards_never_become_answers(self):
        records,image = self.score_fixture()
        self.assertIsNone(race_snapshot(records,image))

    def test_unidentified_white_cards_are_not_enough_to_establish_a_quiz(self):
        image = np.full((1280,720,3),(210,90,40),np.uint8)
        for y in (480,620,760,900):
            image[y:y+90,60:660] = 245
        records = [row('这是一段普通文章里的说明文字',50,300)]
        records += [row(text,100,y) for text,y in zip(['北京','上海','广州','深圳'],(500,640,780,920))]
        self.assertIsNone(race_snapshot(records,image))

    def test_return_to_score_after_owner_verification_never_calls_ai_or_rematches(self):
        records,image = self.score_fixture()
        value = self.race_worker([records])
        value.tasker.controller.cached_image = image
        value.four_player_answer(ROOM)
        value._fun_click.assert_called_once()
        value.ai_resolver.resolve_race_choice.assert_not_called()
        value.tasker.controller.post_click.assert_not_called()
        self.assertTrue(value._fun_metrics['exited_to_score'])
        self.assertFalse(value._fun_metrics['round_finished'])

    def test_one_match_one_answer_then_settlement(self):
        value = self.race_worker([question()]*4+[RESULT])
        with patch('four_player.ThreadPoolExecutor',ImmediateExecutor):
            value.four_player_answer(ROOM)
        self.assertEqual(value._fun_metrics['rounds_started'],1)
        self.assertEqual(value._fun_metrics['questions_clicked'],1)
        self.assertTrue(value._fun_metrics['round_finished'])
        value._fun_click.assert_called_once()
        self.assertEqual(value.ai_resolver.resolve_race_choice.call_count,1)

    def test_answer_to_previous_question_is_discarded_without_clicking(self):
        value = self.race_worker([question()]*2+[question('下列哪一个是广东省省会？'),RESULT])
        with patch('four_player.ThreadPoolExecutor',ImmediateExecutor):
            value.four_player_answer(ROOM)
        self.assertEqual(value._fun_metrics['discarded_answers'],1)
        value.tasker.controller.post_click.assert_not_called()

    def test_selected_option_text_change_does_not_request_same_question_again(self):
        changed = question()
        changed[3] = row('A. 北京（已选择）',65,480)
        value = self.race_worker([question()]*4+[changed,changed,RESULT])
        with patch('four_player.ThreadPoolExecutor',ImmediateExecutor):
            value.four_player_answer(ROOM)
        self.assertEqual(value._fun_metrics['questions_clicked'],1)
        value.ai_resolver.resolve_race_choice.assert_called_once()

    def test_verification_preempts_ready_answer(self):
        value = self.race_worker([question()]*2+[HumanRequired('本人验证')])
        with patch('four_player.ThreadPoolExecutor',ImmediateExecutor):
            with self.assertRaises(HumanRequired):
                value.four_player_answer(ROOM)
        value.tasker.controller.post_click.assert_not_called()
        self.assertEqual(value._fun_metrics['rounds_started'],1)

    def test_matching_timeout_never_clicks_start_twice(self):
        value = self.race_worker([ROOM])
        with patch('four_player.time.monotonic',side_effect=[0,81]):
            with self.assertRaisesRegex(TaskIncomplete,'未重复匹配'):
                value.four_player_answer(ROOM)
        value._fun_click.assert_called_once()
        value.ai_resolver.resolve_race_choice.assert_not_called()

    def test_long_owner_verification_does_not_expire_same_match(self):
        value = self.race_worker([])
        wall = [0.0]
        frames = iter([ROOM]+[question()]*4+[RESULT])
        def records():
            frame = next(frames)
            if frame is ROOM:
                wall[0] += 120
                value._paused_seconds = 120
            else:
                wall[0] += 0.2
            return frame
        value._fun_records = Mock(side_effect=records)
        with patch('four_player.time.monotonic',side_effect=lambda:wall[0]), \
                patch('four_player.ThreadPoolExecutor',ImmediateExecutor):
            value.four_player_answer(ROOM)
        value._fun_click.assert_called_once()
        self.assertEqual(value._fun_metrics['questions_clicked'],1)
        self.assertTrue(value._fun_metrics['round_finished'])

    def test_owner_pause_records_wait_time_and_preserves_worker_deadline(self):
        value = worker()
        value.unattended = False
        value._deadline = 300
        wall = [0.0]
        def owner_finishes(_):
            wall[0] = 120
            value.pause_flag = False
        with patch('utils.time.monotonic',side_effect=lambda:wall[0]), \
                patch('utils.time.sleep',side_effect=owner_finishes), \
                patch('utils.plyer.notification.notify'):
            value.pause('本人完成验证后继续')
        self.assertEqual(value._paused_seconds,120)
        self.assertEqual(value._deadline,420)

    def test_result_marker_is_required(self):
        self.assertTrue(race_finished(RESULT))
        self.assertFalse(race_finished([row('答对了',200,400)]))
        self.assertFalse(race_finished(ROOM))

    def test_model_uses_fast_no_thinking_contract_and_rejects_boolean(self):
        for answer,expected in [(0,0),(3,3),(True,None),(4,None),('A',None)]:
            resolver = AIResolver('offline-key','deepseek-flash')
            resolver.session.close()
            def transport(request):
                payload = json.loads(request.content)
                self.assertEqual(payload['thinking'],dict(type='disabled'))
                self.assertEqual(payload['max_tokens'],64)
                self.assertEqual(request.extensions['timeout']['read'],4)
                return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(dict(index=answer,uncertain=False))},'finish_reason':'stop'}]})
            resolver.session = httpx.Client(transport=httpx.MockTransport(transport))
            try:
                self.assertEqual(resolver.resolve_race_choice([np.zeros((100,200,3),np.uint8)],['北京','上海','广州','深圳'],'中国的首都是哪里？'),expected)
            finally:
                resolver.session.close()

    def test_eight_points_need_observed_first_round_rules(self):
        score = dict(value=8,maximum=10)
        self.assertTrue(fun_first_round_credited(score,RULES))
        self.assertFalse(fun_first_round_credited(score,''))
        self.assertFalse(fun_first_round_credited(dict(value=7,maximum=10),RULES))

    def test_already_credited_first_round_is_not_replayed(self):
        value = worker()
        value._finish_existing_quiz = Mock()
        value.funny_answer = Mock()
        def read_scores(tasks):
            value.run_report.update(visible_task_rules={'趣味答题':RULES})
            return {'趣味答题':dict(value=8,maximum=10)}
        value.read_learning_score = Mock(side_effect=read_scores)
        value.task(['趣味答题'],unattended=True)
        self.assertEqual(value.run_report.snapshot()['status'],'completed')
        self.assertEqual(value.run_report.snapshot()['tasks']['趣味答题']['state'],'credited')
        value.funny_answer.assert_not_called()

    def test_actual_eight_point_settlement_is_completed_with_honest_score(self):
        value = worker()
        value._finish_existing_quiz = Mock()
        value.funny_answer = Mock()
        responses = iter([0,8])
        def read_scores(tasks):
            value.run_report.update(visible_task_rules={'趣味答题':RULES})
            return {'趣味答题':dict(value=next(responses),maximum=10)}
        value.read_learning_score = Mock(side_effect=read_scores)
        value.task(['趣味答题'],unattended=True)
        result = value.run_report.snapshot()
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['tasks']['趣味答题']['after']['value'],8)
        self.assertEqual(result['tasks']['趣味答题']['state'],'credited')
        self.assertEqual(result['unfinished_tasks'],[])

    def test_four_player_verification_does_not_mark_already_full_daily_unfinished(self):
        value = worker()
        value._finish_existing_quiz = Mock()
        value.funny_answer = Mock(side_effect=HumanRequired('四人赛验证'))
        value.read_learning_score = Mock(return_value={
            '趣味答题':dict(value=0,maximum=10),'每日答题':dict(value=5,maximum=5)})
        with patch('utils.plyer.notification.notify'):
            value.task(['趣味答题','每日答题'],unattended=True)
        result = value.run_report.snapshot()
        self.assertEqual(result['status'],'needs_human')
        self.assertEqual(result['unfinished_tasks'],['趣味答题'])
        self.assertEqual(result['tasks']['每日答题']['state'],'skipped_full')

    def test_stopping_four_player_preserves_already_verified_full_daily(self):
        value = worker()
        value._finish_existing_quiz = Mock()
        value.funny_answer = Mock(side_effect=TaskCancelled())
        value.read_learning_score = Mock(return_value={
            '趣味答题':dict(value=0,maximum=10),'每日答题':dict(value=5,maximum=5)})
        value.task(['趣味答题','每日答题'])
        result = value.run_report.snapshot()
        self.assertEqual(result['status'],'stopped')
        self.assertEqual(result['unfinished_tasks'],['趣味答题'])
        self.assertEqual(result['tasks']['每日答题']['state'],'skipped_full')


if __name__ == '__main__':
    unittest.main()
