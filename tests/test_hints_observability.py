import hashlib,json,tempfile,unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from unittest.mock import Mock,patch
from types import SimpleNamespace
import numpy as np
import httpx
from blank_hints import MARKER,blank_context,exact_blank_hint,hint_paragraph
from daily_hints import clean,exact_hint_choice
from diagnostics import RunDiagnostics,load_replay,observed
from task_support import RunReport,TaskIncomplete
from test_optimization import worker,job
from answer_client import AIResolver


def row(text,x,y,w=150,h=30):return dict(text=text,box=[x,y,w,h])


class ExactSymbolsTests(unittest.TestCase):
    def test_operators_are_not_silently_removed(self):
        for hinted,option in [('5+3','5-3'),('1/2','12'),('-5','5'),('3.14','314'),('5%','5')]:
            self.assertIsNone(exact_hint_choice('单选题',{'A':(option,[]),'B':('其他',[])},[hinted]))

    def test_units_and_superscripts_remain_distinct(self):
        for hinted,option in [('ms','m/s'),('m²','m2'),('CO₂','CO2')]:
            self.assertIsNone(exact_hint_choice('单选题',{'A':(option,[]),'B':('其他',[])},[hinted]))

    def test_typographic_width_spaces_and_chinese_sentence_punctuation(self):
        self.assertEqual(clean('５＋３'),clean('5+3'))
        self.assertEqual(exact_hint_choice('单选题',{'A':('认真学习，增长知识。',[]),'B':('其他',[])},['认真学习, 增长知识']),['A'])

    def test_multi_choice_still_matches_ordered_symbols(self):
        options={'A':('5+3',[]),'B':('5-3',[]),'C':('m/s',[])}
        self.assertEqual(exact_hint_choice('多选题',options,['m/s','5-3']),['B','C'])

    def test_fast_switch_cannot_reintroduce_fuzzy_symbol_match(self):
        value=worker();value.fast_answer=True
        value._fast_try_answer=Mock(return_value=['A'])
        value.ai_resolver=SimpleNamespace(resolve_choice=Mock(return_value=['B']))
        self.assertEqual(value._determine_answer('单选题',{'A':('5-3',[]),'B':('8',[])},['5+3'],[]),(['B'],False))
        value._fast_try_answer.assert_not_called()


class BlankContextTests(unittest.TestCase):
    def context(self):return dict(template='中华人民共和国的首都是'+MARKER+'，这里有故宫。',length=2)

    def test_complete_surroundings_extract_exact_original_answer(self):
        self.assertEqual(exact_blank_hint(self.context(),'提示介绍。中华人民共和国的首都是北京，这里有故宫。还有其他内容。'),'北京')

    def test_unmatched_negation_units_and_length_are_not_guessed(self):
        for hint in ['中华人民共和国的首都不是北京，这里有故宫。','中华人民共和国的首都是北京市，这里有故宫。','首都是北京']:
            self.assertIsNone(exact_blank_hint(self.context(),hint))

    def test_repeated_or_different_matches_are_ambiguous(self):
        sentence='中华人民共和国的首都是北京，这里有故宫。'
        self.assertIsNone(exact_blank_hint(self.context(),sentence+sentence))
        self.assertIsNone(exact_blank_hint(self.context(),sentence+sentence.replace('北京','南京')))

    def test_incomplete_context_is_rejected(self):
        self.assertIsNone(exact_blank_hint(dict(template=MARKER+'首都',length=2),'北京首都'))
        self.assertIsNone(exact_blank_hint(dict(template='无空位',length=2),'北京'))

    def test_visible_slot_geometry_builds_complete_template(self):
        records=[row('填空题',60,200),row('中华人民共和国首都是',60,310,255),row('这里有故宫。',380,310,170),row('来源：教材',60,400)]
        result=blank_context(records,[[320,310,25,30],[347,310,25,30]])
        self.assertEqual(result,dict(template='中华人民共和国首都是'+MARKER+'这里有故宫。',length=2))

    def test_separate_inputs_or_ocr_spanning_gap_are_not_concatenated(self):
        records=[row('填空题',60,200),row('中华人民共和国首都是',60,310,255),row('来源：教材',60,400)]
        self.assertIsNone(blank_context(records,[[320,310,25,30],[450,310,25,30]]))
        self.assertIsNone(blank_context(records+[row('合并了题干和空位',100,310,500)],[[320,310,25,30],[347,310,25,30]]))
        self.assertIsNone(blank_context(records[:-1],[[320,310,25,30]]))

    def test_hint_panel_excludes_underlying_question(self):
        self.assertEqual(hint_paragraph([row('题目背景',30,300),row('提示',300,440),row('提示正文',30,560)]),'提示正文')
        self.assertEqual(hint_paragraph([row('普通页面',30,560)]),'')

    def test_context_answer_is_used_without_model_call(self):
        value=worker();value._blank_context=self.context()
        value._blank_hint_text='中华人民共和国的首都是北京，这里有故宫。'
        value.ai_resolver=SimpleNamespace(resolve_blank=Mock())
        self.assertEqual(value._determine_answer('填空题',{},['提示含较长文字'],[],2),('北京',True))
        value.ai_resolver.resolve_blank.assert_not_called()

    def test_ambiguous_context_preserves_model_fallback(self):
        value=worker();value._blank_context=self.context();value._blank_hint_text='不相干的提示'
        value.ai_resolver=SimpleNamespace(resolve_blank=Mock(return_value='北京'))
        self.assertEqual(value._determine_answer('填空题',{},[],[np.zeros((10,10,3),np.uint8)],2),('北京',False))

    def test_preparation_reads_full_hint_only_when_direct_red_answer_does_not_fit(self):
        for red,expected in [(['北京'],False),(['这段红字包含解释'],True)]:
            value=worker();value._sleep=Mock();value._get_red_texts=Mock(return_value=red)
            result=job().get.return_value
            result.nodes[0].recognition.filtered_results=[SimpleNamespace(box=[320,310,25,30]),SimpleNamespace(box=[347,310,25,30])]
            records=[row('填空题',60,200),row('中华人民共和国首都是',60,310,255),row('这里有故宫。',380,310,170),row('来源：教材',60,400)]
            value._screen_records=Mock(side_effect=[records,[row('提示',300,440),row('中华人民共和国首都是北京这里有故宫。',30,560,650)]])
            value.tasker.post_task=Mock(return_value=job());value.tasker.post_task.return_value.get.return_value=result
            value.tasker.controller.post_screencap.return_value.get.return_value=np.zeros((1280,720,3),np.uint8)
            _,_,_,count=value._prepare('填空题')
            self.assertEqual(count,2)
            self.assertEqual(bool(value._blank_hint_text),expected)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.report_patch=patch('task_support.REPORT_PATH',Path(self.temp.name)/'report.json');self.report_patch.start();self.addCleanup(self.report_patch.stop)
        self.report=RunReport(['每日答题'])
        self.diag=RunDiagnostics(self.report,Mock(),Path(self.temp.name)/'trace')

    def test_timing_summary_preserves_existing_result_and_separates_owner_wait(self):
        self.report.task('每日答题',state='verified')
        self.diag.record('截图',.1);self.diag.record('截图',.2);self.diag.record('等待本人处理',12)
        self.diag.flush();result=self.report.snapshot()
        self.assertEqual(result['tasks']['每日答题']['state'],'verified')
        self.assertEqual(result['diagnostics']['timings']['截图'],dict(count=2,seconds=.3,maximum_seconds=.2))
        self.assertEqual(result['diagnostics']['timings']['等待本人处理']['seconds'],12)

    def test_phase_records_failed_work_without_swallowing_error(self):
        with self.assertRaises(TaskIncomplete):
            with self.diag.phase('等待页面'):raise TaskIncomplete('test')
        self.assertIn('等待页面',self.report.snapshot()['diagnostics']['timings'])
        self.assertIsNone(self.report.snapshot()['phase'])

    def test_capture_is_bounded_and_hash_checked(self):
        for i in range(30):self.diag.capture('result',[],np.full((10,10,3),i,np.uint8))
        index=self.diag.folder/'index.json';self.assertEqual(len(load_replay(index)),24)
        first=load_replay(index)[0][1];first.write_bytes(b'changed')
        with self.assertRaises(ValueError):load_replay(index)

    def test_replay_rejects_paths_outside_its_directory(self):
        self.diag.capture('result',[],np.zeros((10,10,3),np.uint8))
        index=self.diag.folder/'index.json';data=json.loads(index.read_text('utf-8'));data['frames'][0]['file']='../other.png';index.write_text(json.dumps(data),'utf-8')
        with self.assertRaises(ValueError):load_replay(index)

    def test_model_timing_hook_does_not_change_request_contract(self):
        value=AIResolver('offline-key','deepseek-flash');value.session.close()
        value.session=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={})))
        self.addCleanup(value.session.close);value.timing_hook=Mock()
        self.assertEqual(value._post({'messages':[]}).status_code,200)
        value.timing_hook.assert_called_once()


if __name__=='__main__':unittest.main()
