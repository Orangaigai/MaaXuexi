"""Real collapsed subchannel regression; never broadens public comment scope."""
import json,sys,unittest
from pathlib import Path
import numpy as np
from PIL import Image
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from comment_tasks import comment_feed, comment_article, article_title_key
from test_optimization import worker

FIXTURES=Path(__file__).parent/'fixtures/comments'
COLLAPSED=json.loads((FIXTURES/'feed-collapsed.json').read_text('utf-8'))

class ScrolledCommentFeedTests(unittest.TestCase):
    def test_title_identity_ignores_punctuation_without_matching_another_article(self):
        self.assertEqual(article_title_key('卫生纸为什么要压花？'),article_title_key('卫生纸为什么要压花?'))
        self.assertNotEqual(article_title_key('卫生纸为什么要压花？'),article_title_key('卫生纸为什么要漂白？'))

    def test_collapsed_catalog_is_never_treated_as_an_article(self):
        self.assertIsNone(comment_article(COLLAPSED))

    def test_article_requires_its_own_visible_source_label(self):
        records=json.loads((FIXTURES/'article-late.json').read_text('utf-8'))
        self.assertIsNotNone(comment_article(records))
        records=[r for r in records if not r['text'].startswith('来源')]
        self.assertIsNone(comment_article(records))

    def test_article_ready_uses_same_complete_body_rule_as_generation(self):
        records=json.loads((FIXTURES/'article-late.json').read_text('utf-8'))
        self.assertIsNotNone(comment_article(records))
        initial=[r for r in records if r['box'][1]<900]
        initial_body=''.join(r['text'] for r in initial if 250<=r['box'][1]<1080)
        self.assertGreaterEqual(len(initial_body),60)
        self.assertIsNone(comment_article(initial))

    def value(self):
        value=worker()
        value.tasker.controller.cached_image=np.ascontiguousarray(
            np.asarray(Image.open(FIXTURES/'feed-collapsed.png').convert('RGB'))[:,:,::-1])
        return value

    def test_collapsed_real_feed_requires_previous_confirmed_life_subchannel(self):
        value=self.value()
        self.assertFalse(comment_feed(COLLAPSED))
        self.assertFalse(value._comment_life_feed(COLLAPSED))
        value._comment_life_selected=True
        self.assertTrue(value._comment_life_feed(COLLAPSED))

    def test_expanded_red_life_label_establishes_scope_before_scroll(self):
        value=self.value()
        records=json.loads((FIXTURES/'feed.json').read_text('utf-8'))
        image=np.zeros((1280,720,3),np.uint8)
        x,y,w,h=next(r['box'] for r in records if r['text']=='生活百科')
        image[y:y+h,x:x+w]=[0,0,220]
        value.tasker.controller.cached_image=image
        self.assertTrue(value._comment_life_feed(records))
        self.assertTrue(value._comment_life_selected)

    def test_unselected_science_heading_cannot_reuse_scope(self):
        value=self.value();value._comment_life_selected=True
        value.tasker.controller.cached_image[:]=128
        self.assertFalse(value._comment_life_feed(COLLAPSED))

    def test_captcha_and_article_still_fail_with_retained_scope(self):
        value=self.value();value._comment_life_selected=True
        captcha=COLLAPSED+[dict(text='访问验证',box=[250,300,200,40])]
        self.assertFalse(value._comment_life_feed(captcha))
        article=json.loads((FIXTURES/'article.json').read_text('utf-8'))
        self.assertFalse(value._comment_life_feed(article))

    def test_return_from_article_waits_for_scrolled_life_feed_before_channel_menu(self):
        value=self.value();value._comment_life_selected=True
        value._comment_ocr=Mock(return_value=[dict(text='欢迎发表观点',box=[25,1220,150,30])])
        def wait(predicate,*args,**kwargs):
            if predicate==value._comment_life_feed:
                self.assertTrue(predicate(COLLAPSED))
            return COLLAPSED
        value._comment_wait=Mock(side_effect=wait)
        value._comment_channel_menu=Mock(return_value=[dict(text='推荐',box=[20,300,80,35])])
        value._comment_click=Mock();value._wait_home_ready=Mock()
        value._comment_restore_home()
        value._comment_channel_menu.assert_called_once_with(COLLAPSED)
        value._comment_click.assert_called_once()

if __name__=='__main__':unittest.main()
