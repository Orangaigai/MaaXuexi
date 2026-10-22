"""Whole-stem gap geometry, wrapped ambiguity and evidence-bound hint skipping."""
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import Mock
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from daily_gaps import count_choice_gaps, all_options_from_gaps
from test_optimization import worker
from daily_text import choice_page
from daily_choices import visible_options


def sample(instruction='每个选项仅用一次，依次填入空白处。'):
    def row(text,x,y,w=350,h=30):return dict(text=text,box=[x,y,w,h])
    records=[row('多选题',65,200,120),row(instruction,65,300,580,60),
             row('来源：教材',65,420),row('A. 甲',85,530),row('B. 乙',85,640),
             row('出题：平台',65,800),row('查看提示',540,950,115)]
    options={'A':('甲',[85,530,35,40]),'B':('乙',[85,640,35,40])}
    image=np.full((1280,720,3),255,np.uint8)
    for left in (200,440):
        image[350:352,left:left+54]=180
        image[325:345,left-20:left-10]=30
        image[325:345,left+64:left+74]=30
    return records,image,options


class GapTests(unittest.TestCase):
    def test_real_interior_and_whole_line_start_blanks_with_multiline_options(self):
        for name in ('three-interior','three-line-start'):
            folder=Path(__file__).parent/'fixtures/daily-gaps-20261022'
            records=json.loads((folder/f'{name}.json').read_text('utf-8'))
            image=np.asarray(Image.open(folder/f'{name}.png').convert('RGB'))[:,:,::-1]
            value=worker();value._get_options=Mock(side_effect=AssertionError('unneeded template scan'))
            value._screen_records=Mock(return_value=records);value._screen_ocr_cache=(image,records)
            images,choices,reds,_=value._prepare('多选题')
            self.assertEqual(count_choice_gaps(records,image,choices),3)
            self.assertEqual(value._determine_answer('多选题',choices,reds,images),(list('ABC'),True))
            value.tasker.post_task.assert_not_called()
            value._get_options.assert_not_called()
            if name=='three-interior':self.assertIn('平大体一致',choices['C'][0])

    def test_count_groups_not_number_of_underscore_characters(self):
        records,image,options=sample()
        self.assertEqual(count_choice_gaps(records,image,options),2)
        page=choice_page(records,image,options,'多选题')
        self.assertEqual(all_options_from_gaps(page,records,image,options),['A','B'])

    def test_repeated_answers_or_negated_single_use_keep_hints(self):
        for instruction in ('甲的反义词是____，甲的反义词也是____。',
                            '每个选项可以重复使用，分别填入____和____。',
                            '不是每个选项仅用一次。'):
            records,image,options=sample(instruction)
            self.assertEqual(count_choice_gaps(records,image,options),2)
            page=choice_page(records,image,options,'多选题')
            self.assertIsNone(all_options_from_gaps(page,records,image,options))

    def test_more_options_than_gaps_keeps_hints(self):
        records,image,options=sample()
        options['C']=('丙',[85,730,35,40])
        page=dict(question_type='多选题',question=records[1]['text'])
        self.assertIsNone(all_options_from_gaps(page,records,image,options))

    def test_margin_fragments_of_one_wrapped_gap_are_not_two_gaps(self):
        records,image,options=sample()
        image[:]=255
        image[350:352,610:665]=180
        image[396:398,65:120]=180
        self.assertIsNone(count_choice_gaps(records,image,options))

    def test_antialiased_ends_do_not_double_count_one_thin_line(self):
        records,image,options=sample()
        image[351,200:202]=255
        image[351,252:254]=255
        self.assertEqual(count_choice_gaps(records,image,options),2)

    def test_unanchored_line_start_is_not_accepted_as_a_whole_blank(self):
        folder=Path(__file__).parent/'fixtures/daily-gaps-20261022'
        records=json.loads((folder/'three-line-start.json').read_text('utf-8'))
        image=np.asarray(Image.open(folder/'three-line-start.png').convert('RGB'))[:,:,::-1]
        records=[r for r in records if not r['text'].startswith('非法传销是指')]
        self.assertIsNone(count_choice_gaps(records,image,visible_options(records)))

    def test_dividers_thick_lines_and_absent_text_do_not_count_as_blanks(self):
        for modification in ('thick','divider','no_anchors'):
            records,image,options=sample()
            if modification=='thick':image[350:358,200:254]=180
            elif modification=='divider':image[380:382,65:655]=180
            else:image[325:345]=255
            with self.subTest(modification=modification):
                self.assertEqual(count_choice_gaps(records,image,options),2 if modification=='divider' else None)

    def test_partial_picture_or_single_choice_page_cannot_use_shortcut(self):
        records,image,options=sample()
        self.assertIsNone(all_options_from_gaps(None,records,image,options))
        self.assertIsNone(all_options_from_gaps(dict(question_type='单选题'),records,image,options))
        image[390:415,300:370]=0
        self.assertIsNone(choice_page(records,image,options,'多选题'))

    def test_shortcut_reuses_existing_frame_and_never_opens_or_reads_hint(self):
        records,image,options=sample()
        value=worker();value._get_options=Mock(return_value=options)
        value._daily_gap_shortcut_enabled=True
        value._screen_records=Mock(return_value=records);value._screen_ocr_cache=(image,records)
        value._get_red_texts=Mock();value.ai_resolver=Mock()
        images,choices,reds,blanks=value._prepare('多选题')
        self.assertEqual(value._determine_answer('多选题',choices,reds,images),(['A','B'],True))
        value.tasker.post_task.assert_not_called()
        value.tasker.controller.post_screencap.assert_not_called()
        value._screen_records.assert_called_once()
        value._get_red_texts.assert_not_called();value.ai_resolver.resolve_choice.assert_not_called()
        value._get_options.assert_not_called()

    def test_real_blue_grey_lines_above_reference_picture_count_four(self):
        records,image,options=sample('通过四个信息栏了解就业岗位。')
        image[:]=255
        records[1]['box']=[61,317,594,159]
        options={'A':('图片选项',[97,988,40,34])}
        for left in (200,278,356,434):
            image[439,left:left+55]=[186,179,171]
            image[407:431,left-12:left-5]=20
            image[407:431,left+58:left+65]=20
        # The first reference screenshot starts much lower; exclude its lines.
        records=[r for r in records if r['text'] not in ('来源：教材','A. 甲','B. 乙')]
        records.append(dict(text='图片内标题',box=[180,586,180,30]))
        image[600:602,200:400]=180
        self.assertEqual(count_choice_gaps(records,image,options),4)
        all_options={letter:('图片选项',[97,988,40,34]) for letter in 'ABCD'}
        self.assertEqual(all_options_from_gaps(dict(question_type='多选题',question=records[1]['text']),
                            records,image,all_options,gap_count=4),list('ABCD'))

    def test_explicitly_disabled_shortcut_keeps_hint_path_for_same_question_benchmark(self):
        records,image,options=sample()
        value=worker();value._get_options=Mock(return_value=options)
        value._daily_gap_shortcut_enabled=False
        value._screen_records=Mock(return_value=records);value._screen_ocr_cache=(image,records)
        value._sleep=Mock();value._get_red_texts=Mock(return_value=['甲'])
        value.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=image
        value._prepare('多选题')
        self.assertIsNone(value._daily_direct_choices)

    def test_next_preparation_clears_previous_direct_answer(self):
        records,image,options=sample('选择正确答案。')
        options['C']=('丙',[85,730,35,40])
        records.append(dict(text='C. 丙',box=[85,730,200,30]))
        value=worker();value._get_options=Mock(return_value=options)
        value._screen_records=Mock(return_value=records);value._screen_ocr_cache=(image,records)
        value._daily_direct_choices=['A','B'];value._sleep=Mock();value._get_red_texts=Mock(return_value=['甲'])
        value.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=image
        value._prepare('多选题')
        self.assertIsNone(value._daily_direct_choices)
        self.assertEqual([call.args[0] for call in value.tasker.post_task.call_args_list],['查找提示','查看提示','关闭提示'])

    def test_complete_illustrated_inventory_can_skip_hint_without_becoming_text_only(self):
        records,image,options=sample()
        # A reference illustration is not evidence that an extra option exists.
        image[390:415,300:370]=0
        value=worker();value._get_options=Mock(side_effect=AssertionError('unneeded template scan'))
        value._screen_records=Mock(return_value=records);value._screen_ocr_cache=(image,records)
        images,choices,reds,_=value._prepare('多选题')
        self.assertEqual(value._daily_direct_choices,['A','B'])
        self.assertIsNone(value._daily_choice_page)
        self.assertIsNone(value._daily_choice_text)
        self.assertEqual(value._determine_answer('多选题',choices,reds,images),(['A','B'],True))
        value.tasker.post_task.assert_not_called()
