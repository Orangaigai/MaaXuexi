"""Timed-choice regressions: early dispatch, fresh-frame guard, input fidelity."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import httpx
import numpy as np
from answer_client import AIResolver
from four_player import race_snapshot, quiz_identity
from fun_challenge import question_body, question_text, fingerprint, options_from_records
import test_four_player
import test_fun_challenge
from test_four_player import ImmediateExecutor, question, ROOM, RESULT
from test_fun_challenge import page
from task_support import TaskIncomplete


class SpeedTests(unittest.TestCase):
    def entry_worker(self):
        value=test_fun_challenge.FunChallengeTests().make_worker()
        value._reset_home=Mock()
        value._fun_metrics={}
        value._fun_options=Mock(return_value=options_from_records(page(0)))
        return value

    def test_cold_center_waits_for_today_marker_without_guessing_card(self):
        folder=Path(__file__).parent/'fixtures/speed-20261016'
        missing=json.loads((folder/'center-missing-today.json').read_text('utf-8'))
        ready=json.loads((folder/'center-today-single.json').read_text('utf-8'))
        bank=[dict(text='挑战答题',box=[280,60,160,40]),dict(text='强国总题库',box=[180,480,250,40])]
        value=self.entry_worker()
        value._fun_records=Mock(side_effect=[missing]+[missing]*20+[ready,bank,page(0)])
        value._open_fun_challenge=__import__('types').MethodType(__import__('fun_challenge').FunChallenge._open_fun_challenge,value)
        self.assertEqual(value._open_fun_challenge(),page(0))
        value._reset_home.assert_not_called()
        self.assertEqual(value.tasker.controller.post_click.call_count,2)

    def test_score_returns_to_profile_and_reuses_native_center(self):
        ready=json.loads((Path(__file__).parent/'fixtures/speed-20261016/center-today-single.json').read_text('utf-8'))
        score=[dict(text='积分中心',box=[270,70,160,40])]
        profile=[dict(text='我要答题',box=[200,400,160,40]),dict(text='学习积分',box=[300,400,160,40])]
        bank=[dict(text='挑战答题',box=[280,60,160,40]),dict(text='强国总题库',box=[180,480,250,40])]
        value=self.entry_worker()
        value._fun_records=Mock(side_effect=[score,profile,profile,ready,bank,page(0)])
        value._open_fun_challenge=__import__('types').MethodType(__import__('fun_challenge').FunChallenge._open_fun_challenge,value)
        value._open_fun_challenge()
        self.assertEqual(value.tasker.post_task.call_args_list[0].args,('返回',))
        value._reset_home.assert_not_called()
    def test_single_text_route_preserves_negation_operators_and_order(self):
        resolver=AIResolver('offline','deepseek-flash');resolver.session.close()
        requests=[]
        def transport(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"index":1,"uncertain":false}'},'finish_reason':'stop'}]})
        resolver.session=httpx.Client(transport=httpx.MockTransport(transport))
        self.addCleanup(resolver.session.close)
        q='下列关于5-3=2的说法，哪一项不正确？'
        self.assertEqual(resolver.resolve_fun_choice([np.zeros((80,160,3),np.uint8)],['成立','不成立'],q),1)
        payload=requests[0]
        self.assertEqual(payload['thinking'],{'type':'disabled'})
        self.assertFalse(any(p['type']=='image_url' for p in payload['messages'][1]['content']))
        sent=json.loads(payload['messages'][1]['content'][0]['text'])
        self.assertEqual(sent['question'],q)
        self.assertEqual(sent['options'],{'0':'成立','1':'不成立'})

    def test_single_picture_and_missing_text_keep_visual_input_without_thinking(self):
        resolver=AIResolver('offline','deepseek-flash');resolver.session.close()
        requests=[]
        def transport(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"index":0,"uncertain":false}'},'finish_reason':'stop'}]})
        resolver.session=httpx.Client(transport=httpx.MockTransport(transport))
        self.addCleanup(resolver.session.close)
        for q in ['下表中的数据对应哪一个选项？',None]:
            self.assertEqual(resolver.resolve_fun_choice([np.zeros((80,160,3),np.uint8)],['甲','乙'],q),0)
        for payload in requests:
            self.assertTrue(any(p['type']=='image_url' for p in payload['messages'][1]['content']))
            self.assertEqual(payload['thinking'],{'type':'disabled'})

    def test_question_reader_keeps_symbols_for_model_and_identity(self):
        records=page(0,'下列5-3=2的计算，哪一项不正确？')
        options=options_from_records(records)
        self.assertIn('5-3=2',question_body(records,options))
        changed=page(0,'下列5+3=2的计算，哪一项不正确？')
        self.assertNotEqual(fingerprint(records,options),fingerprint(changed,options_from_records(changed)))
        self.assertNotEqual(quiz_identity('3.14'),quiz_identity('314'))
        self.assertEqual(quiz_identity('测试，标点？'),quiz_identity('测试标点'))

    def test_race_math_change_invalidates_early_answer(self):
        before=question('下列5+3的结果是什么？')
        after=question('下列5-3的结果是什么？')
        self.assertNotEqual(race_snapshot(before)['key'],race_snapshot(after)['key'])
        value=test_four_player.FourPlayerTests().race_worker([before,before,after,RESULT])
        with patch('four_player.ThreadPoolExecutor',ImmediateExecutor):
            value.four_player_answer(ROOM)
        self.assertEqual(value._fun_metrics['discarded_answers'],1)
        value.tasker.controller.post_click.assert_not_called()

    def test_dispatch_happens_before_second_confirmation_but_click_waits(self):
        value=test_four_player.FourPlayerTests().race_worker([])
        calls=[0]
        def records():
            calls[0]+=1
            if calls[0]==3:
                value.ai_resolver.resolve_race_choice.assert_called_once()
                value.tasker.controller.post_click.assert_not_called()
            return question() if calls[0]<=3 else RESULT
        value._fun_records.side_effect=records
        with patch('four_player.ThreadPoolExecutor',ImmediateExecutor):
            value.four_player_answer(ROOM)
        self.assertEqual(value._fun_metrics['questions_clicked'],1)
        self.assertEqual(len(value._fun_metrics['observed_to_click_seconds']),1)

    def test_partial_text_then_complete_text_can_get_a_new_answer(self):
        partial=question('中国的首都')
        full=question('中国的首都位于哪座城市？')
        value=test_four_player.FourPlayerTests().race_worker([partial,partial,full,full,full,RESULT])
        with patch('four_player.ThreadPoolExecutor',ImmediateExecutor):
            value.four_player_answer(ROOM)
        self.assertEqual(value._fun_metrics['discarded_answers'],1)
        self.assertEqual(value.ai_resolver.resolve_race_choice.call_count,2)
        self.assertEqual(value._fun_metrics['questions_clicked'],1)

    def test_single_slow_transition_does_not_request_or_click_old_question_again(self):
        value=test_fun_challenge.FunChallengeTests().make_worker()
        value._fun_records=Mock(side_effect=[page(0),page(0)]+[page(0)]*8+[page(1,'第二个不同题目'),page(1,'第二个不同题目'),
            [dict(text='挑战结束',box=[200,600,300,40])]])
        with self.assertRaisesRegex(TaskIncomplete,'本局结束'):
            value.funny_answer()
        self.assertEqual(value.ai_resolver.resolve_fun_choice.call_count,2)
        self.assertEqual(value._fun_metrics['questions_clicked'],2)
        self.assertTrue(all(call.args[0]<=.12 for call in value._sleep.call_args_list))


if __name__=='__main__':unittest.main()
