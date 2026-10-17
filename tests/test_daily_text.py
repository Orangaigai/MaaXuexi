"""Complete text requests, visual fallbacks and unchanged daily submission."""
import copy
import json
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import numpy as np
from answer_client import AIResolver
from daily_text import choice_page, complete_hint, request_text
from test_optimization import worker, job


def row(text, x, y, w=300, h=30):
    return dict(text=text, box=[x, y, w, h])


def page():
    return [row('单选题',65,200,120),row('下列哪一个算式结果是八？',65,300,500),
            row('来源：教材',65,400),row('A. 5+3',85,530),row('B. 5-3',85,640),
            row('出题：平台',65,800),row('查看提示',540,950,115)]


OPTIONS = {'A':('5+3',[85,530,35,40]),'B':('5-3',[85,640,35,40])}
HINT = [row('提示',325,440,65),row('五加三等于八。',35,560,300)]


class DailyTextTests(unittest.TestCase):
    def setUp(self):
        self.image=np.full((1280,720,3),255,np.uint8)

    def test_complete_page_keeps_operators_and_all_options(self):
        value=choice_page(page(),self.image,OPTIONS,'单选题')
        self.assertEqual(value['options'],{'A':'5+3','B':'5-3'})
        self.assertEqual(request_text(value,complete_hint(HINT,self.image))['hint'],'五加三等于八。')

    def test_full_frame_restores_multiline_option_instead_of_crop_text(self):
        records=page();records.insert(4,row('后一行仍属于选项A',125,580,420))
        value=choice_page(records,self.image,OPTIONS,'单选题')
        self.assertEqual(value['options']['A'],'5+3\n后一行仍属于选项A')

    def test_partial_or_scrolled_stems_missing_labels_and_footers_use_images(self):
        for missing in ['单选题','A. 5+3','出题：平台','查看提示']:
            records=[r for r in page() if r['text']!=missing]
            self.assertIsNone(choice_page(records,self.image,OPTIONS,'单选题'))
        self.assertIsNone(choice_page(page(),self.image,{'B':OPTIONS['B'],'C':OPTIONS['A']},'单选题'))

    def test_extra_label_or_clipped_option_cannot_make_a_text_request(self):
        self.assertIsNone(choice_page(page()+[row('C. 其他',85,750)],self.image,OPTIONS,'单选题'))
        records=page();records[3]['box'][2]=640
        self.assertIsNone(choice_page(records,self.image,OPTIONS,'单选题'))
        options=copy.deepcopy(OPTIONS);options['A']=('不一致的选项',[85,530,35,40])
        self.assertIsNone(choice_page(page(),self.image,options,'单选题'))

    def test_diagram_in_stem_or_option_and_image_dependent_words_keep_images(self):
        for y in [350,585]:
            image=self.image.copy();image[y:y+35,400:460]=0
            self.assertIsNone(choice_page(page(),image,OPTIONS,'单选题'))
        records=page();records[1]['text']='如图所示，下列哪项正确？'
        self.assertIsNone(choice_page(records,self.image,OPTIONS,'单选题'))

    def test_incomplete_clipped_or_visual_hint_cannot_be_text_only(self):
        for records in [HINT[:1],HINT[1:],HINT[:1]+[row('文字仍在继续',35,560)],
                        HINT[:1]+[row('末行。',35,1140)],HINT[:1]+[row('越界。',5,560)]]:
            self.assertEqual(complete_hint(records,self.image),'')
        image=self.image.copy();image[600:640,200:270]=0
        self.assertEqual(complete_hint(HINT,image),'')

    def test_actual_archived_page_and_template_options_parse_without_model(self):
        archive=json.loads((Path(__file__).parent/'fixtures/first-question-20261015/observed.json').read_text('utf-8'))
        result=choice_page(archive['before'],self.image,archive['options'],'单选题')
        self.assertEqual(result['options'],{'A':'正确','B':'错误'})
        self.assertIn('资金还没有到达',result['question'].replace('\n',''))

    def test_preparation_collects_full_hint_only_for_model_fallback(self):
        for red,extra_ocr in [(['5+3'],False),(['五加三等于八'],True)]:
            value=worker();value._sleep=Mock();value._get_options=Mock(return_value=OPTIONS)
            value._get_red_texts=Mock(return_value=red)
            frames=iter([page(),HINT])
            def read():
                records=next(frames);value._screen_ocr_cache=(self.image.copy(),records);return records
            value._screen_records=Mock(side_effect=read)
            value.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=self.image
            images,options,hints,_=value._prepare('单选题')
            self.assertEqual(value._screen_records.call_count,2 if extra_ocr else 1)
            self.assertEqual(bool(value._daily_choice_text),extra_ocr)
            value.ai_resolver=SimpleNamespace(resolve_choice=Mock(return_value=['A']))
            self.assertEqual(value._determine_answer('单选题',options,hints,images),(['A'],not extra_ocr))
            if extra_ocr:
                self.assertEqual(value.ai_resolver.resolve_choice.call_args.kwargs['text_input']['hint'],'五加三等于八。')
            else:value.ai_resolver.resolve_choice.assert_not_called()

    def test_scroll_preserves_original_images_and_does_not_add_hint_ocr(self):
        value=worker();value._sleep=Mock();value._get_options=Mock(return_value=OPTIONS)
        value._get_red_texts=Mock(return_value=['不是精确选项'])
        def read():value._screen_ocr_cache=(self.image,page());return page()
        value._screen_records=Mock(side_effect=read)
        value.tasker.post_task.side_effect=[job(False),job(),job()]
        value.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=self.image
        images,_,_,_=value._prepare('单选题')
        self.assertEqual(len(images),3);self.assertIsNone(value._daily_choice_text)
        value._screen_records.assert_called_once()

    def test_first_line_of_long_option_cannot_be_mistaken_for_whole_hint_match(self):
        value=worker();value._sleep=Mock();value._get_options=Mock(return_value=OPTIONS)
        value._get_red_texts=Mock(return_value=['5+3'])
        records=page();records.insert(4,row('但这不是选项的全部内容',125,580,420))
        frames=iter([records,HINT])
        def read():
            rows=next(frames);value._screen_ocr_cache=(self.image,rows);return rows
        value._screen_records=Mock(side_effect=read)
        value.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=self.image
        images,options,hints,_=value._prepare('单选题')
        self.assertIn('全部内容',options['A'][0])
        value.ai_resolver=SimpleNamespace(resolve_choice=Mock(return_value=['B']))
        self.assertEqual(value._determine_answer('单选题',options,hints,images),(['B'],False))
        value.ai_resolver.resolve_choice.assert_called_once()

    def resolver(self,answer):
        value=AIResolver('offline-key','deepseek-flash');value.session.close()
        self.requests=[]
        def transport(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(200,json={'model':'returned-version','usage':{'prompt_tokens':42},
                'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}]})
        value.session=httpx.Client(transport=httpx.MockTransport(transport));self.addCleanup(value.session.close)
        return value

    def test_actual_http_contract_omits_images_only_for_complete_payload(self):
        resolver=self.resolver({'choices':['A'],'uncertain':False})
        payload=request_text(choice_page(page(),self.image,OPTIONS,'单选题'),complete_hint(HINT,self.image))
        self.assertEqual(resolver.resolve_choice([self.image],text_input=payload),['A'])
        content=self.requests[0]['messages'][1]['content']
        self.assertEqual([p['type'] for p in content],['text'])
        self.assertEqual(json.loads(content[0]['text']),payload)
        self.assertEqual(resolver.last_input_mode,'text');self.assertEqual(resolver.last_usage,{'prompt_tokens':42})
        self.assertEqual(resolver.last_response_model,'returned-version')
        resolver.resolve_choice([self.image],text_input={'question':'不完整'})
        self.assertEqual(resolver.last_input_mode,'image')
        self.assertIn('image_url',[p['type'] for p in self.requests[-1]['messages'][1]['content']])

    def test_text_answers_are_limited_to_actual_options_and_uncertainty_is_preserved(self):
        payload=request_text(choice_page(page(),self.image,OPTIONS,'单选题'),'提示。')
        for answer in [{'choices':['C'],'uncertain':False},{'choices':['A'],'uncertain':True}]:
            self.assertIsNone(self.resolver(answer).resolve_choice([self.image],text_input=payload))

    def test_provider_non_object_response_remains_a_controlled_failure(self):
        resolver=self.resolver({'choices':['A'],'uncertain':False})
        resolver.session.close()
        resolver.session=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=[])))
        self.addCleanup(resolver.session.close)
        self.assertIsNone(resolver.resolve_choice([self.image]))
        self.assertEqual(resolver.last_error,'invalid_or_uncertain_answer')


if __name__=='__main__':unittest.main()
