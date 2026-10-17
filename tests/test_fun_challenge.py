"""Counter, mode, stale-answer and bounded-stop checks, without devices/API calls."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import httpx
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from fun_challenge import correct_count, options_from_records, fingerprint, question_text, challenge_target
from task_support import TaskIncomplete, TaskUnavailable, TaskSkipped
from test_optimization import worker, job
from answer_client import AIResolver
import webui


def page(count, question='这道题应该选哪一个'):
    return [dict(text='挑战答题', box=[290,58,140,40]),
            dict(text=f'已答对{count}题', box=[400,140,180,30]),
            dict(text=question, box=[60,240,500,40]),
            dict(text='A. 第一项', box=[60,430,500,40]),
            dict(text='B. 第二项', box=[60,550,500,40])]


class FunChallengeTests(unittest.TestCase):
    def make_worker(self):
        value = worker()
        value._open_fun_challenge = Mock()
        value._exit_fun_challenge = Mock()
        value._wait_fun_settlement = Mock()
        value._sleep = Mock()
        value.ai_resolver = SimpleNamespace(resolve_fun_choice=Mock(return_value=0), last_error=None)
        value._fun_options = Mock(side_effect=options_from_records)
        value.tasker.controller.cached_image = np.zeros((1280,720,3),np.uint8)
        return value

    def test_ten_correct_gets_one_normal_ending_answer_before_submission(self):
        value = self.make_worker()
        frames = []
        for count in range(10):
            frames += [page(count, f'题目{count}'), page(count, f'题目{count}')]
        frames += [page(10, '题目10'),page(10, '题目10'),
                   [dict(text='挑战结束',box=[200,600,300,60]),dict(text='本次答对10题',box=[200,730,260,40])]]
        value._fun_records = Mock(side_effect=frames)
        value.funny_answer()
        self.assertEqual(value._fun_metrics['questions_clicked'],11)
        self.assertEqual(value._fun_metrics['confirmed_correct'],10)
        self.assertEqual(value.ai_resolver.resolve_fun_choice.call_count,11)
        self.assertEqual(value._fun_metrics['ending_answers_clicked'],1)
        value._exit_fun_challenge.assert_called_once()

    def test_stale_model_answer_is_not_clicked(self):
        value = self.make_worker()
        value._fun_records = Mock(side_effect=[page(0,'原题'),page(0,'变题')]*3)
        with self.assertRaisesRegex(TaskIncomplete,'过期'):
            value.funny_answer()
        value.tasker.controller.post_click.assert_not_called()
        self.assertEqual(value._fun_metrics['stale_answers_discarded'],3)

    def test_real_ocr_jitter_discards_old_answer_and_requeries_without_reusing_it(self):
        fixture=Path(__file__).parent/'fixtures/quiz-20261013/fun-ocr-jitter.json'
        evidence=json.loads(fixture.read_text('utf-8'))
        value=self.make_worker()
        value._fun_options=Mock(side_effect=[evidence['before_options'],evidence['after_options'],
            evidence['after_options'],evidence['after_options'],[]])
        value._fun_records=Mock(side_effect=[evidence['before'],evidence['after'],
            evidence['after'],evidence['after'],[dict(text='挑战结束',box=[250,250,200,40])]])
        with self.assertRaisesRegex(TaskIncomplete,'本局结束'):
            value.funny_answer()
        self.assertEqual(value.ai_resolver.resolve_fun_choice.call_count,2)
        self.assertEqual(value._fun_metrics['stale_answers_discarded'],1)
        self.assertEqual(value._fun_metrics['questions_clicked'],1)
        value.tasker.controller.post_click.assert_called_once()

    def test_changed_question_without_confirmed_correct_count_is_not_success(self):
        value = self.make_worker()
        value._fun_records = Mock(side_effect=[page(0,'原题'),page(0,'原题'),page(0,'变题')])
        with self.assertRaisesRegex(TaskIncomplete,'计数未确认'):
            value.funny_answer()
        self.assertEqual(value._fun_metrics['confirmed_correct'],0)
        value._exit_fun_challenge.assert_not_called()

    def test_unchanged_page_stops_without_repeated_answer_clicks(self):
        value = self.make_worker()
        value._fun_records = Mock(return_value=page(0))
        with self.assertRaisesRegex(TaskIncomplete,'停止重复点击'):
            value.funny_answer()
        self.assertEqual(value.tasker.controller.post_click.call_count,1)

    def test_competitive_room_is_recognized_without_starting_matching(self):
        value = worker()
        value._reset_home = Mock(); value._sleep = Mock()
        value._fun_records = Mock(return_value=[dict(text='双人对战',box=[290,58,140,40])])
        self.assertEqual(value._open_fun_challenge(),[dict(text='双人对战',box=[290,58,140,40])])
        self.assertEqual(value._fun_mode,'双人对战')
        value.tasker.controller.post_click.assert_not_called()

    def test_verification_is_handed_to_user_without_input(self):
        value = worker()
        value._screen_records = Mock(return_value=[dict(text='访问验证',box=[200,300,200,40])])
        value.pause = Mock(side_effect=TaskIncomplete('本人验证'))
        with self.assertRaisesRegex(TaskIncomplete,'本人验证'):
            value._fun_records()
        value.tasker.controller.post_click.assert_not_called()

    def test_ambiguous_counter_and_duplicate_options_are_rejected(self):
        records = page(2)
        self.assertEqual(correct_count(records),2)
        self.assertIsNone(correct_count(records+[dict(text='已答对3题',box=[400,180,180,30])]))
        records.append(dict(text='A. 重复',box=[60,650,500,40]))
        self.assertEqual(options_from_records(records),[])

    def test_unlabeled_challenge_uses_real_button_geometry(self):
        import json
        from PIL import Image
        from fun_challenge import challenge_options
        fixture = Path(__file__).resolve().parents[1] / 'navigation'
        if not fixture.exists():
            fixture = Path(__file__).parent / 'fixtures' / 'fun-challenge'
        records = json.loads((fixture/'ocr.json').read_text('utf-8'))
        screen = np.array(Image.open(fixture/'screen.png'))[:,:,::-1]
        options = challenge_options(records,screen)
        self.assertEqual([row['text'] for row in options],['错误','正确'])
        self.assertIsNotNone(fingerprint(records,options))

    def test_app_without_counter_counts_only_verified_next_question(self):
        value = self.make_worker()
        frames=[]
        for count in range(10):
            frame=[row for row in page(count,f'题目{count}') if not row['text'].startswith('已答对')]
            frames.extend([frame,frame])
        frame=[row for row in page(10,'题目10') if not row['text'].startswith('已答对')]
        frames.extend([frame,frame,[dict(text='挑战结束',box=[200,600,300,60]),
                                  dict(text='本次答对10题',box=[200,730,260,40])]])
        value._fun_records=Mock(side_effect=frames)
        value.funny_answer()
        self.assertEqual(value._fun_metrics['confirmed_correct'],10)
        value._exit_fun_challenge.assert_called_once()

    def test_actual_three_answer_rule_reduces_calls_and_normally_ends_round(self):
        rules='趣味答题“挑战答题”每次答对3道及以上积10分；“四人赛”首局胜利积10分每日上限10分'
        value=self.make_worker()
        value.run_report=SimpleNamespace(snapshot=Mock(return_value=dict(visible_task_rules={'趣味答题':rules},
            tasks={'趣味答题':dict(before=dict(value=0,maximum=10))})))
        frames=[]
        for count in range(4):
            frames.extend([page(count,f'题目{count}'),page(count,f'题目{count}')])
        frames.append([dict(text='挑战结束',box=[200,600,300,60]),dict(text='本次答对3题',box=[200,730,260,40])])
        value._fun_records=Mock(side_effect=frames)
        value.funny_answer()
        self.assertEqual(value._fun_metrics['target_correct'],3)
        self.assertEqual(value._fun_metrics['confirmed_correct'],3)
        self.assertEqual(value._fun_metrics['model_calls'],4)
        self.assertEqual(value._fun_metrics['ending_answers_clicked'],1)
        self.assertEqual(value.tasker.controller.post_click.call_args.args,(310,570))
        value._exit_fun_challenge.assert_called_once()

    def test_only_observed_rule_granting_full_score_can_reduce_target(self):
        self.assertEqual(challenge_target('“挑战答题”每次答对3道及以上积10分',10),3)
        self.assertEqual(challenge_target('“挑战答题”每次答对3道及以上积5分',10),10)
        self.assertEqual(challenge_target('“四人赛”每次答对3道及以上积10分',10),10)
        self.assertEqual(challenge_target('',10),10)

    def test_unlabeled_choice_contract_accepts_zero_and_rejects_invalid_indices(self):
        for answer,expected in [(0,0),(1,1),(True,None),(-1,None),(2,None),('0',None)]:
            resolver=AIResolver('offline-key','deepseek-flash'); resolver.session.close()
            resolver.session=httpx.Client(transport=httpx.MockTransport(lambda request:
                httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(dict(index=answer,uncertain=False))},'finish_reason':'stop'}]})))
            try:
                self.assertEqual(resolver.resolve_fun_choice([np.zeros((80,160,3),np.uint8)],['错误','正确']),expected)
            finally:
                resolver.session.close()

    def test_timed_mode_does_not_retry_a_connection_timeout(self):
        calls=[]
        def fail(request):
            calls.append(request)
            self.assertEqual(request.extensions['timeout']['read'],6)
            body=json.loads(request.content)
            self.assertEqual(body['max_tokens'],64)
            self.assertEqual(body['thinking'],dict(type='disabled'))
            self.assertNotIn('reasoning_effort',body)
            raise httpx.ConnectTimeout('offline')
        resolver=AIResolver('offline-key','deepseek-flash'); resolver.session.close()
        resolver.session=httpx.Client(transport=httpx.MockTransport(fail))
        try:
            self.assertIsNone(resolver.resolve_fun_choice([np.zeros((80,160,3),np.uint8)],['甲','乙']))
            self.assertEqual(len(calls),1)
        finally:
            resolver.session.close()

    def test_selected_button_disappearance_does_not_make_old_question_new(self):
        records=page(0)
        original=options_from_records(records)
        old_question=question_text(records,original)
        remaining=[original[1]]
        self.assertEqual(question_text(records,remaining,original[0]['box'][1]),old_question)

    def test_manual_and_saved_plans_keep_daily_quiz_last(self):
        plan=webui.TaskModel(tasklist=['每日答题','选读文章','趣味答题','趣味答题'])
        self.assertEqual(plan.tasklist,['选读文章','趣味答题','每日答题'])


if __name__ == '__main__':
    unittest.main()
