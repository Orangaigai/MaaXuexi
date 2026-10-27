"""A complete brief source is usable; its evidence and review still apply."""
import unittest
from unittest.mock import Mock
import test_comment_reading as reading
from comment_drafts import ContextCommentDrafts, valid_article, validate_draft
from task_support import TaskUnavailable


class ShortCommentContextTests(unittest.TestCase):
    def test_complete_brief_text_is_read_without_a_return_to_top_before_review(self):
        body='香蕉会释放乙烯，这种物质可以促进果实成熟。'
        rows=reading.HEAD+[reading.row(body,450),reading.row('责任编辑：测试',700),
                           reading.row('阅读 123',900),reading.row('点赞 25',900,400)]
        value=reading.CommentReadingTests().value([rows])
        article=value._comment_read_full_article(reading.ARTICLE)
        self.assertEqual(article['body'],body)
        self.assertTrue(valid_article(article))
        value._comment_article_top.assert_not_called()
        self.assertIsNotNone(validate_draft(dict(skip=False,comment='原来香蕉自己就能催熟，长知识了！',evidence='香蕉会释放乙烯，这种物质可以促进果实成熟。'),article))

    def test_no_body_is_rejected_and_a_short_source_cannot_support_new_facts(self):
        article=dict(reading.ARTICLE,body='')
        self.assertFalse(valid_article(article))
        article['body']='香蕉会释放乙烯，这种物质可以促进果实成熟。'
        self.assertIsNone(validate_draft(dict(skip=False,comment='植物里还有不少小奥秘，挺有意思！',evidence='香蕉可以改善睡眠'),article))

    def test_short_preview_without_observed_end_is_not_used_for_publication(self):
        rows=reading.HEAD+[reading.row('植物的叶片运动也有值得观察的细节。',450)]
        value=reading.CommentReadingTests().value([rows]*12)
        with self.assertRaises(TaskUnavailable):value._comment_read_full_article(reading.ARTICLE)
        value._comment_article_top.assert_not_called()
        value.tasker.controller.post_input_text.assert_not_called()

    def test_review_failure_identifies_specific_flag_without_printing_rejected_text(self):
        engine=ContextCommentDrafts(Mock());engine.last_error='review_rejected'
        engine.last_reviews=dict(reviews=[dict(grounded=False,topic_related=True,neutral=True,
                                               within_scope=True,natural=True,specific=True,reason='扩大了原文结论')])
        summary=engine.rejection_summary()
        self.assertIn('事实依据',summary);self.assertIn('扩大了原文结论',summary)
        self.assertNotIn('自然语气',summary)

    def test_local_format_failure_is_distinct_from_model_scope_rejection(self):
        engine=ContextCommentDrafts(Mock());engine.last_error='draft_failed_local_rules'
        engine.last_candidates=[dict(skip=False,comment='“催熟”原来就是这么回事，长知识了！',evidence='香蕉会释放乙烯')]
        self.assertIn('标点',engine.rejection_summary())


if __name__=='__main__':unittest.main()
