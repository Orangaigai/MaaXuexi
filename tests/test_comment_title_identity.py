"""Actual long-dash OCR drift must not become an article identity change."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock
import numpy as np
from PIL import Image
from comment_reading import title_line_text
from comment_tasks import comment_article, article_title_key
from task_support import TaskIncomplete
from test_optimization import worker

FIXTURES=Path(__file__).parent/'fixtures'

class ArticleTitleIdentityTests(unittest.TestCase):
    def setUp(self):
        self.rows=json.loads((FIXTURES/'comment-title-dash.json').read_text('utf-8'))
        self.image=np.full((1280,720,3),255,np.uint8)
        self.image[150:300]=np.asarray(Image.open(FIXTURES/'comment-title-dash.png').convert('RGB'))

    def test_real_header_variants_have_same_identity_only_with_pixel_evidence(self):
        before=copy.deepcopy(self.rows['before'])
        raw=comment_article(before,require_body=False)
        observed=comment_article(self.rows['after'],require_body=False)
        self.assertNotEqual(article_title_key(raw['title']),article_title_key(observed['title']))
        fixed=comment_article(before,require_body=False,image=self.image)
        self.assertEqual(article_title_key(fixed['title']),article_title_key(observed['title']))
        self.assertEqual(before,self.rows['before'])

    def test_initial_fallback_font_and_final_font_both_match_the_same_heading(self):
        initial=self.image.copy()
        initial[150:300]=np.asarray(Image.open(FIXTURES/'comment-title-dash-fallback.png').convert('RGB'))
        before=comment_article(self.rows['before'],require_body=False,image=initial)
        after=comment_article(self.rows['after'],require_body=False,image=self.image)
        self.assertEqual(article_title_key(before['title']),article_title_key(after['title']))
        # Two genuine Chinese 一 glyphs are wider than the initial short hyphens.
        row=next(r for r in self.rows['before'] if '一一' in r['text'])
        x,y,w,h=row['box'];glyph=w/len(row['text']);start=round(row['text'].index('一一')*glyph)
        literal=np.full_like(self.image,255)
        literal[y+20:y+23,x+start:x+start+31]=0
        literal[y+20:y+23,x+start+35:x+start+66]=0
        self.assertEqual(title_line_text(row['text'],row['box'],literal),row['text'])

    def test_genuine_chinese_characters_are_retained_without_a_long_stroke(self):
        for text in ['把事实一一列举清楚','一心一意学习生活知识','第一种方法与第二种方法']:
            self.assertEqual(title_line_text(text,[20,180,600,40],np.full_like(self.image,255)),text)
        image=np.full_like(self.image,255)
        image[200,220:250]=0;image[200,255:285]=0
        self.assertEqual(title_line_text('把事实一一列举清楚',[20,180,600,40],image),'把事实一一列举清楚')

    def test_long_stroke_elsewhere_does_not_delete_a_real_yiyi_word(self):
        text='知识科普把事实一一列举清楚'
        image=np.full_like(self.image,255);image[200:202,25:110]=0
        self.assertEqual(title_line_text(text,[20,180,600,40],image),text)

    def test_thick_image_shapes_are_not_title_separators(self):
        line=next(r for r in self.rows['before'] if '一一' in r['text'])
        image=self.image.copy();image[150:300]=255
        x,y,w,h=line['box'];glyph=w/len(line['text']);start=line['text'].index('一一')
        image[y+15:y+27,x+round(start*glyph):x+round((start+2)*glyph)]=0
        self.assertEqual(title_line_text(line['text'],line['box'],image),line['text'])

    def test_missing_image_or_invalid_box_keeps_the_original_text(self):
        for image,box in [(None,[0,0,100,40]),(np.zeros((20,20)),[0,0,10,10]),(self.image,[-1,0,100,40]),(self.image,[0,0,9999,40])]:
            self.assertEqual(title_line_text('标题一一分隔',box,image),'标题一一分隔')

    def test_full_read_and_return_accept_the_same_title_across_the_ocr_drift(self):
        first=self.rows['before']+[dict(text='文章提供了具体的科普知识以及适用范围。'*6,box=[25,420,660,32])]
        last=[dict(text='本文最后还说明了知识的适用条件。',box=[25,220,600,32]),
              dict(text='阅读 1234',box=[25,800,200,25]),dict(text='点赞 22',box=[400,800,200,25])]
        value=worker();value._sleep=Mock();value._comment_article_bar=Mock()
        value.tasker.controller.cached_image=self.image
        value._screen_records=Mock(side_effect=[first,last,last,self.rows['after']])
        article=comment_article(first,require_body=False,image=self.image)
        result=value._comment_read_full_article(article)
        self.assertIn(last[0]['text'],result['body'])
        self.assertEqual(value.tasker.controller.post_swipe.call_count,2)
        value.tasker.controller.post_input_text.assert_not_called()

    def test_reading_waits_for_end_but_excludes_editor_credit_and_embedded_video_text(self):
        first=self.rows['before']+[dict(text='正文介绍了相关知识，并说明内容的适用范围。'*5,box=[25,420,650,32]),
                                  dict(text='责任编辑：测试',box=[25,650,200,25])]
        last=[dict(text='视频字幕和推荐内容不属于文章正文',box=[25,220,600,32]),
              dict(text='阅读 1234',box=[25,800,200,25]),dict(text='点赞 22',box=[400,800,200,25])]
        value=worker();value._sleep=Mock();value._comment_article_bar=Mock();value._comment_article_top=Mock()
        value.tasker.controller.cached_image=self.image;value._screen_records=Mock(side_effect=[first,last])
        article=comment_article(first,require_body=False,image=self.image)
        result=value._comment_read_full_article(article)
        self.assertIn('正文介绍',result['body']);self.assertNotIn('责任编辑',result['body']);self.assertNotIn('视频字幕',result['body'])
        value.tasker.controller.post_swipe.assert_called_once()
        value._comment_article_top.assert_called_once()

    def test_a_different_chinese_word_still_stops_before_reading_or_input(self):
        before=self.rows['before'];changed=copy.deepcopy(before)
        for row in changed:row['text']=row['text'].replace('冠状动脉','冠状静脉')
        value=worker();value.tasker.controller.cached_image=self.image
        value._screen_records=Mock(return_value=changed)
        original=comment_article(before,require_body=False,image=self.image)
        with self.assertRaisesRegex(TaskIncomplete,'标题未一致'):
            value._comment_read_full_article(original)
        value.tasker.controller.post_swipe.assert_not_called();value.tasker.controller.post_input_text.assert_not_called()

if __name__=='__main__':unittest.main()
