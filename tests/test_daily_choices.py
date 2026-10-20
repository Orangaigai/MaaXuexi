"""Real long-option layout: keep every frame and select while scrolling upward."""
import unittest
import sys
from pathlib import Path
from unittest.mock import Mock
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from daily_choices import visible_options,scan_to_hint,select_scrolled_choices
from test_optimization import worker,job
from task_support import TaskIncomplete
from test_daily_gaps import sample

def label(letter,y=500,text='图片选项'):
    return dict(text=letter+'. '+text,box=[97,y,200,34])

class LongChoiceTests(unittest.TestCase):
    def test_long_four_image_question_skips_hint_only_after_all_options_are_collected(self):
        records,image,_=sample('通过四个信息栏选择岗位。')
        records=[r for r in records if r['text'] not in ('来源：教材','A. 甲','B. 乙','查看提示','出题：平台')]
        image[:]=255
        for left in (200,278,356,434):
            image[350,left:left+55]=[186,179,171]
            image[325:345,left-12:left-5]=20;image[325:345,left+58:left+65]=20
        records.append(label('A',988))
        frames=iter([(records,image),([label('B')],np.full_like(image,230)),
                     ([label('C')],np.full_like(image,210)),
                     ([label('D'),dict(text='查看提示',box=[547,1175,109,27])],np.full_like(image,190))])
        value=worker();value._sleep=Mock();value._captcha_present=Mock(return_value=False)
        value._daily_gap_shortcut_enabled=True
        value._get_options=Mock(return_value={'A':('图片选项',[97,988,40,34])})
        def read():
            rows,frame=next(frames);value._screen_ocr_cache=(frame,rows);return rows
        value._screen_records=Mock(side_effect=read)
        value.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=image
        value.tasker.post_task.return_value=job(False)
        images,options,reds,_=value._prepare('多选题')
        self.assertEqual(value._daily_direct_choices,list('ABCD'))
        self.assertEqual(sorted(options),list('ABCD'))
        self.assertEqual(len(images),4);self.assertEqual(reds,[])
        self.assertEqual([call.args[0] for call in value.tasker.post_task.call_args_list],['查找提示'])

    def test_image_only_options_are_kept_but_tiny_letters_inside_images_are_not(self):
        self.assertEqual(visible_options([label('A',text=''),dict(text='B. 伪选项',box=[270,600,100,12])]),
                         {'A':('',[97,500,40,34])})

    def test_collect_four_overlapping_image_pages_until_real_hint(self):
        value=worker();value._captcha_present=Mock(return_value=False);value._sleep=Mock()
        first=np.full((1280,720,3),255,np.uint8)
        frames=[np.full_like(first,n) for n in (220,180,140)]
        rows=[[label('B')],[label('C')],[label('D'),dict(text='!查看提示',box=[547,1175,109,27])]]
        def screen():
            value._screen_ocr_cache=(frames.pop(0),rows.pop(0));return value._screen_ocr_cache[1]
        value._screen_records=Mock(side_effect=screen)
        images,options,complete,pages=scan_to_hint(value,[label('A')],first,{})
        self.assertTrue(complete);self.assertEqual(list(options),list('ABCD'))
        self.assertEqual((len(images),pages),(4,3))
        self.assertEqual(value.tasker.controller.post_swipe.call_count,3)

    def test_stationary_page_stops_after_one_swipe(self):
        value=worker();value._captcha_present=Mock(return_value=False);value._sleep=Mock()
        image=np.full((1280,720,3),255,np.uint8)
        value._screen_records=Mock(return_value=[label('A')]);value._screen_ocr_cache=(image,[label('A')])
        with self.assertRaises(TaskIncomplete):scan_to_hint(value,[label('A')],image,{})
        self.assertEqual(value.tasker.controller.post_swipe.call_count,1)

    def test_selected_options_are_never_toggled_off_on_overlapping_frames(self):
        value=worker();value._sleep=Mock()
        screen=0;clicks=[]
        def swipe(*args):
            nonlocal screen
            screen+=1;return job()
        def task(name,*args):
            visible=({'D'},{'C','D'},{'B','C'},{'A','B'})[min(screen,3)]
            letter=name[-1]
            if letter in visible:clicks.append(letter)
            return job(letter in visible)
        value.tasker.post_task.side_effect=task
        value.tasker.controller.post_swipe.side_effect=swipe
        self.assertTrue(select_scrolled_choices(value,list('ABCD'),3))
        self.assertEqual(clicks,list('DCBA'))

    def test_missing_option_never_submits_partial_answer(self):
        value=worker();value._sleep=Mock();value.tasker.post_task.return_value=job(False)
        with self.assertRaises(TaskIncomplete):select_scrolled_choices(value,['A'],1)
        self.assertEqual(value.tasker.controller.post_swipe.call_count,2)
