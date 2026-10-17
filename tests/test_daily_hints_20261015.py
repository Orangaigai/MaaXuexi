import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import httpx
import numpy as np

from answer_client import AIResolver
from daily_hints import exact_hint_choice, question_from_records
from test_optimization import worker

OBSERVED=json.loads((Path(__file__).parent/'fixtures/first-question-20261015/observed.json').read_text('utf-8'))


class DailyHintTests(unittest.TestCase):
    def test_actual_question_matches_the_complete_hint_statement(self):
        question=question_from_records(OBSERVED['before'],OBSERVED['options'])
        self.assertTrue(question.startswith('通过汇款'))
        self.assertNotIn('出版社',question)
        self.assertEqual(exact_hint_choice('单选题',OBSERVED['options'],OBSERVED['hint_red_texts'],question),['A'])

    def test_changed_or_negated_statement_is_not_inferred_from_long_hint(self):
        question=question_from_records(OBSERVED['before'],OBSERVED['options'])
        for modified in (question.replace('可以马上','不可以马上'),question+'这句话是错误的','另一个不相关的长句子用于测试匹配条件'):
            self.assertIsNone(exact_hint_choice('单选题',OBSERVED['options'],OBSERVED['hint_red_texts'],modified))

    def test_answer_follows_option_text_instead_of_assuming_letter_a(self):
        options={'A':('错误',[80,620,50,40]),'B':('正确',[80,730,50,40])}
        question=question_from_records(OBSERVED['before'],OBSERVED['options'])
        self.assertEqual(exact_hint_choice('单选题',options,OBSERVED['hint_red_texts'],question),['B'])

    def test_duplicate_option_and_ambiguous_hint_are_not_guessed(self):
        options={'A':('正确',[80,620,50,40]),'B':('正确',[80,730,50,40])}
        self.assertIsNone(exact_hint_choice('单选题',options,['正确']))
        self.assertIsNone(exact_hint_choice('单选题',OBSERVED['options'],['正确','错误']))

    def test_multi_choice_requires_every_hint_to_have_one_exact_option(self):
        options={'A':('甲',[80,620,50,40]),'B':('乙',[80,730,50,40]),'C':('丙',[80,840,50,40])}
        self.assertEqual(exact_hint_choice('多选题',options,['丙','甲']),['A','C'])
        for hints in (['甲','乙','无关文字'],['甲','甲'],['甲乙丙三个字'],['甲的解释']):
            self.assertIsNone(exact_hint_choice('多选题',options,hints))

    def test_normal_mode_reads_hint_and_never_uses_legacy_guessing(self):
        value=worker()
        value._sleep=Mock()
        value._get_options=Mock(return_value=OBSERVED['options'])
        value._screen_records=Mock(return_value=OBSERVED['before'])
        value._get_red_texts=Mock(return_value=OBSERVED['hint_red_texts'])
        value.tasker.controller.post_screencap.return_value.get.return_value=np.zeros((1280,720,3),np.uint8)
        value.ai_resolver=SimpleNamespace(resolve_choice=Mock())
        value._fast_try_answer=Mock(return_value=['B'])
        imgs,options,red_texts,count=value._prepare('单选题')
        self.assertEqual(value._determine_answer('单选题',options,red_texts,imgs,count),(['A'],True))
        value.ai_resolver.resolve_choice.assert_not_called()
        value._fast_try_answer.assert_not_called()


class ProviderFailureTests(unittest.TestCase):
    def resolver(self, transport):
        value=AIResolver('offline-fixture','deepseek-flash')
        value.session.close()
        value.session=httpx.Client(transport=httpx.MockTransport(transport))
        value._wait=Mock(return_value=True)
        self.addCleanup(value.session.close)
        return value

    def test_temporary_503_gets_one_retry_and_can_recover(self):
        calls=[]
        def transport(request):
            calls.append(request)
            if len(calls)==1:
                return httpx.Response(503)
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"choices":["A"],"uncertain":false}'}}]})
        value=self.resolver(transport)
        self.assertEqual(value.resolve_choice([np.zeros((20,20,3),np.uint8)]),['A'])
        self.assertEqual(len(calls),2)
        self.assertEqual(value.last_request_attempts,2)
        self.assertIsNone(value.last_error)

    def test_persistent_503_keeps_real_error_and_stops_after_two_requests(self):
        calls=[]
        value=self.resolver(lambda request:(calls.append(request) or httpx.Response(503)))
        self.assertIsNone(value.resolve_choice([np.zeros((20,20,3),np.uint8)]))
        self.assertEqual(value.last_error,'http_503')
        self.assertEqual(len(calls),2)

    def test_read_timeout_is_not_retried_or_relabelled_as_bad_answer(self):
        calls=[]
        def transport(request):
            calls.append(request)
            raise httpx.ReadTimeout('offline read timeout')
        value=self.resolver(transport)
        self.assertIsNone(value.resolve_choice([np.zeros((20,20,3),np.uint8)]))
        self.assertEqual(value.last_error,'read_timeout')
        self.assertEqual(len(calls),1)

    def test_authentication_error_is_not_retried(self):
        calls=[]
        value=self.resolver(lambda request:(calls.append(request) or httpx.Response(401)))
        self.assertIsNone(value.resolve_choice([np.zeros((20,20,3),np.uint8)]))
        self.assertEqual(value.last_error,'http_401')
        self.assertEqual(len(calls),1)

    def test_other_answer_types_preserve_service_error_and_timed_modes_do_not_retry(self):
        image=[np.zeros((20,20,3),np.uint8)]
        for method,args,expected in (
                ('resolve_blank',(image,True,2),2),('resolve_click_blank',(image,),2),
                ('resolve_fun_choice',(image,['甲','乙']),1),
                ('resolve_race_choice',(image,['甲','乙'],'测试完整题目文字'),1)):
            with self.subTest(method=method):
                value=self.resolver(lambda request:httpx.Response(503))
                self.assertIsNone(getattr(value,method)(*args))
                self.assertEqual(value.last_error,'http_503')
                self.assertEqual(value.last_request_attempts,expected)


if __name__=='__main__':
    unittest.main()
