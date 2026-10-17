"""Real labels, paired input contracts, cost accounting and honest summaries."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import httpx
from answer_client import AIResolver
from quiz_benchmark import load_cases,evaluate,summarize,estimate_cost


FOLDER=Path(__file__).parent/'fixtures/model-eval'


class QuizBenchmarkTests(unittest.TestCase):
    def test_archived_answer_labels_have_independent_hint_or_feedback_evidence(self):
        cases=load_cases(FOLDER/'cases.json')
        self.assertEqual(len(cases),2)
        self.assertEqual(cases[1]['expected'],['B'])
        self.assertNotIn(cases[1]['evidence']['images'][0],cases[1]['images'])

    def test_missing_changed_or_escaped_evidence_is_rejected(self):
        for file in ['missing.png','../outside.png']:
            with tempfile.TemporaryDirectory() as temp:
                data=json.loads((FOLDER/'cases.json').read_text('utf-8'))
                data['cases'][0]['images'][0]['file']=file
                path=Path(temp)/'cases.json';path.write_text(json.dumps(data),'utf-8')
                with self.assertRaises(ValueError):load_cases(path)

    def test_model_self_claims_are_not_accepted_as_ground_truth(self):
        with tempfile.TemporaryDirectory() as temp:
            data=json.loads((FOLDER/'cases.json').read_text('utf-8'))
            data['cases'][0]['evidence']['kind']='model_says_correct'
            path=Path(temp)/'cases.json';path.write_text(json.dumps(data),'utf-8')
            with self.assertRaisesRegex(ValueError,'ground-truth'):load_cases(path)

    def test_paired_routes_never_send_expected_answer_or_feedback_image(self):
        calls=[]
        def respond(request):
            body=json.loads(request.content);calls.append(body)
            prompt=body['messages'][0]['content']
            result={'index':1,'uncertain':False} if '"index"' in prompt else {'choices':['A'],'uncertain':False}
            return httpx.Response(200,json={'model':'actual-model','usage':{'prompt_tokens':100,'completion_tokens':10},
                'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]})
        resolver=AIResolver('offline-test-key','deepseek-flash');resolver.session.close()
        resolver.session=httpx.Client(transport=httpx.MockTransport(respond));self.addCleanup(resolver.session.close)
        cases=load_cases(FOLDER/'cases.json')
        for case in cases:
            for route in ('text','image'):
                row=evaluate(resolver,case,route,FOLDER)
                self.assertTrue(row['correct']);self.assertEqual(row['response_model'],'actual-model')
                text=json.dumps(calls[-1]['messages'],ensure_ascii=False)
                self.assertNotIn('expected',text);self.assertNotIn('绿色正确',text)
                images=[part for part in calls[-1]['messages'][1]['content'] if part['type']=='image_url']
                self.assertEqual(len(images),len(case['images']) if route=='image' else 0)

    def test_cached_input_and_output_costs_are_separate(self):
        usage=dict(prompt_tokens=1000,prompt_cache_hit_tokens=400,completion_tokens=200)
        self.assertEqual(estimate_cost(usage,dict(input=2,cached_input=.2,output=3)),.00188)
        self.assertIsNone(estimate_cost(usage,None))
        self.assertIsNone(estimate_cost({},dict(input=2,cached_input=.2,output=3)))
        self.assertIsNone(estimate_cost(dict(prompt_tokens=1,completion_tokens=2,prompt_cache_hit_tokens=3),dict(input=1,cached_input=1,output=1)))

    def test_timeout_and_slow_correct_answer_stay_in_summary_denominator(self):
        rows=[dict(answer=['A'],correct=True,correct_in_time=True,seconds=1,estimated_cost=.01),
              dict(answer=['A'],correct=True,correct_in_time=False,seconds=5,estimated_cost=.02),
              dict(answer=None,correct=False,correct_in_time=False,seconds=12,estimated_cost=None)]
        result=summarize(rows)
        self.assertEqual((result['total'],result['correct'],result['correct_in_time'],result['failures']),(3,2,1,1))
        self.assertEqual(result['median_seconds'],5);self.assertEqual(result['p95_seconds'],12)
        self.assertIsNone(result['estimated_cost'])


if __name__=='__main__':unittest.main()
