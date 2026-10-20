"""Whole-stem gap geometry, wrapped ambiguity and evidence-bound hint skipping."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from daily_gaps import count_choice_gaps, all_options_from_gaps
from test_optimization import worker
from daily_text import choice_page


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
    def test_count_groups_not_number_of_underscore_characters(self):
        records,image,options=sample()
        self.assertEqual(count_choice_gaps(records,image,options),2)
        page=choice_page(records,image,options,'多选题')
        self.assertEqual(all_options_from_gaps(page,records,image,options),['A','B'])

    def test_equal_counts_without_single_use_instruction_do_not_prove_all_answers(self):
        for instruction in ('甲的反义词是____，甲的反义词也是____。',
                            '每个选项可以重复使用，分别填入____和____。',
                            '不是每个选项仅用一次。', '请选择正确的两个答案。'):
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
        value._screen_records=Mock(return_value=records);value._screen_ocr_cache=(image,records)
        value._get_red_texts=Mock();value.ai_resolver=Mock()
        images,choices,reds,blanks=value._prepare('多选题')
        self.assertEqual(value._determine_answer('多选题',choices,reds,images),(['A','B'],True))
        value.tasker.post_task.assert_not_called()
        value.tasker.controller.post_screencap.assert_not_called()
        value._screen_records.assert_called_once()
        value._get_red_texts.assert_not_called();value.ai_resolver.resolve_choice.assert_not_called()

    def test_next_preparation_clears_previous_direct_answer(self):
        records,image,options=sample('选择正确答案。')
        value=worker();value._get_options=Mock(return_value=options)
        value._screen_records=Mock(return_value=records);value._screen_ocr_cache=(image,records)
        value._daily_direct_choices=['A','B'];value._sleep=Mock();value._get_red_texts=Mock(return_value=['甲'])
        value.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=image
        value._prepare('多选题')
        self.assertIsNone(value._daily_direct_choices)
        self.assertEqual([call.args[0] for call in value.tasker.post_task.call_args_list],['查找提示','查看提示','关闭提示'])
