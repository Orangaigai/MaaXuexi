"""Same-frame missing-label probes and conservative layout fallbacks."""
import json,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from daily_choices import cached_options,hint_visible,visible_options
from daily_text import choice_inventory,choice_page
from test_daily_text import page,OPTIONS
from test_optimization import worker,job

class CachedDailyTests(unittest.TestCase):
    def setUp(self):self.image=np.full((1280,720,3),255,np.uint8)

    def test_missing_letters_probe_exact_cached_image_without_screen_or_task_calls(self):
        value=worker();value._check_active=Mock()
        value.tasker.post_recognition=Mock(return_value=job(False))
        records=page();result=cached_options(value,self.image,records)
        self.assertEqual(set(result),{'A','B'})
        self.assertEqual([c.args[1].template for c in value.tasker.post_recognition.call_args_list],
                         [['C.png'],['D.png'],['E.png'],['F.png']])
        for call in value.tasker.post_recognition.call_args_list:self.assertIs(call.args[2],self.image)
        value.tasker.post_task.assert_not_called();value.tasker.controller.post_screencap.assert_not_called()

    def test_image_label_without_text_survives_for_complete_picture_request(self):
        value=worker();value._check_active=Mock();match=job();detail=match.get.return_value
        detail.nodes[0].recognition=SimpleNamespace(hit=True,best_result=SimpleNamespace(box=[85,740,35,40]))
        value.tasker.post_recognition=Mock(side_effect=[match,job(False),job(False),job(False),job(False)])
        result=cached_options(value,self.image,page())
        self.assertEqual(result['C'],('',[85,740,35,40]))
        self.assertIsNone(choice_page(page(),self.image,result,'单选题'))
        self.assertEqual(value.tasker.post_recognition.call_args_list[1].args[0],'OCR')

    def test_known_hint_icon_is_normalized_but_instruction_text_is_not(self):
        records=page();records[-1]['text']='！查看提示'
        self.assertTrue(hint_visible(records));self.assertIsNotNone(choice_inventory(records,self.image,OPTIONS,'单选题'))
        for text in ('请查看提示作答','查看提示之后','答案：查看提示'):
            records[-1]['text']=text;self.assertFalse(hint_visible(records))
            self.assertIsNone(choice_inventory(records,self.image,OPTIONS,'单选题'))

    def test_replay_todays_ocr_keeps_missing_footer_on_image_path(self):
        root=Path(__file__).parent/'fixtures/daily-read-20261025'
        for number,expected in ((1,False),(2,False),(3,True),(4,True)):
            records=json.loads((root/f'question-{number}.json').read_text('utf-8'))
            question_type='多选题' if number in (1,4) else '单选题'
            options=visible_options(records)
            self.assertEqual(choice_inventory(records,self.image,options,question_type) is not None,expected)
            if number==2:self.assertIsNone(choice_page(records,self.image,options,question_type))

    def test_hint_already_visible_avoids_redundant_search_and_capture(self):
        value=worker();value._sleep=Mock();records=page()
        value._screen_records=Mock(return_value=records);value._screen_ocr_cache=(self.image,records)
        value._get_red_texts=Mock(return_value=['5+3'])
        images,_,_,_=value._prepare('单选题')
        self.assertIsNot(images[0],self.image);self.assertTrue(np.array_equal(images[0],self.image))
        self.assertEqual([c.args[0] for c in value.tasker.post_task.call_args_list],['查看提示','关闭提示'])
        self.assertEqual(value.tasker.controller.post_screencap.call_count,1)

if __name__=='__main__':unittest.main()
