"""Inspect short/used sources without spending the three text-draft slots."""
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_comment_reading as reading
import comment_tasks
from task_support import TaskIncomplete, TaskUnavailable
from test_optimization import worker


class CommentSelectionTests(unittest.TestCase):
    def prepare(self, outcomes):
        value = worker(); value.ai_resolver = Mock(); value.run_report = Mock()
        value._comment_open_article = Mock(side_effect=[dict(reading.ARTICLE, title=reading.TITLE+str(i))
                                                      for i in range(len(outcomes))])
        value._comment_read_full_article = Mock(side_effect=outcomes)
        value._comment_return_to_feed = Mock()
        value._comment_article_top = Mock()
        value._screen_records = Mock(side_effect=TaskIncomplete('到达发布前身份核对'))
        return value

    def test_three_bad_intros_do_not_prevent_later_text_generation(self):
        value = self.prepare([TaskUnavailable('正文不足80字') for _ in range(3)]+[reading.ARTICLE])
        drafts = Mock(); drafts.generate.return_value = reading.comments.DRAFT
        with patch('comment_tasks.ContextCommentDrafts', return_value=drafts):
            with self.assertRaisesRegex(TaskIncomplete, '身份核对'):
                value._prepare_comment_draft()
        self.assertEqual(value._comment_open_article.call_count, 4)
        self.assertEqual(drafts.generate.call_count, 1)
        self.assertEqual(value._comment_return_to_feed.call_count, 3)
        value.tasker.controller.post_input_text.assert_not_called()

    def test_eight_invalid_sources_stop_without_calling_model_or_publishing(self):
        value = self.prepare([TaskUnavailable('正文不足80字') for _ in range(8)])
        with patch('comment_tasks.ContextCommentDrafts') as drafts:
            with self.assertRaisesRegex(TaskUnavailable, '已检查8篇，实际生成短评0篇'):
                value._prepare_comment_draft()
        drafts.assert_not_called()
        self.assertEqual(value._comment_open_article.call_count, 8)
        self.assertEqual(value.run_report.task.call_args.kwargs['article_selection']['generated'], 0)
        value.tasker.controller.post_input_text.assert_not_called()

    def test_feed_exhaustion_reports_zero_model_calls_honestly(self):
        value = self.prepare([])
        value._comment_open_article.side_effect = TaskUnavailable('列表未找到文章')
        with self.assertRaisesRegex(TaskUnavailable, '已检查0篇，实际生成短评0篇；列表未找到文章'):
            value._prepare_comment_draft()

    def test_used_full_title_is_rejected_even_when_card_ocr_misses_it(self):
        value = self.prepare([reading.ARTICLE]); value._comment_last_card_title = 'OCR缺损卡片标题'
        value._comment_open_article.side_effect = [dict(reading.ARTICLE, title='识人“模板”一一内隐人格理论'),
                                                 TaskUnavailable('没有其他文章')]
        with patch('comment_tasks.CommentJournal') as journal, patch('comment_tasks.ContextCommentDrafts') as drafts:
            journal.return_value.used_articles.return_value = ['识人“模板”—内隐人格理论']
            with self.assertRaisesRegex(TaskUnavailable, '实际生成短评0篇'):
                value._prepare_comment_draft()
        value._comment_read_full_article.assert_not_called()
        self.assertIn('OCR缺损卡片标题', value._comment_rejected_cards)
        drafts.assert_not_called()

    def test_new_attempt_does_not_reuse_old_collapsed_channel_confirmation(self):
        value = self.prepare([]); value._comment_life_selected = True
        def check():
            self.assertFalse(value._comment_life_selected)
            raise TaskUnavailable('本轮无新文章')
        value._comment_open_article.side_effect = check
        with self.assertRaises(TaskUnavailable):
            value._prepare_comment_draft()

    def feed(self, title):
        value = reading.CommentReadingTests().feed_worker([title])
        value._comment_life_feed = Mock(return_value=True)
        value._comment_wait = Mock(return_value=reading.FIRST)
        return value

    def test_verified_feed_reuses_current_position_without_home_navigation(self):
        value = self.feed(reading.TITLE)
        with patch('comment_tasks.CommentJournal') as journal:
            journal.return_value.used_articles.return_value = []
            self.assertEqual(value._comment_open_article()['title'], reading.TITLE)
        value._reset_home.assert_not_called()
        value._comment_channel_menu.assert_not_called()
        self.assertEqual(value._comment_click.call_count, 1)

    def test_exact_card_rejection_does_not_skip_other_articles_from_same_date(self):
        value = self.feed('科普中国2026-10-14另一篇未曾发表的科普文章')
        value._comment_rejected_cards = {comment_tasks.article_title_key('科普中国2026-10-14以前发表过的另一篇文章')}
        with patch('comment_tasks.CommentJournal') as journal:
            journal.return_value.used_articles.return_value = []
            self.assertEqual(value._comment_open_article()['title'], reading.TITLE)

    def test_scroll_budget_is_shared_between_rejected_articles(self):
        titles = ['已发表过的第一篇科普文章', '已发表过的第二篇科普文章']
        value = reading.CommentReadingTests().feed_worker(titles)
        value._comment_feed_scrolls = comment_tasks.COMMENT_FEED_PAGES-2
        with patch('comment_tasks.CommentJournal') as journal:
            journal.return_value.used_articles.return_value = titles
            with self.assertRaises(TaskUnavailable):
                value._comment_open_article()
        self.assertEqual(value._comment_feed_scrolls, comment_tasks.COMMENT_FEED_PAGES-1)
        value.tasker.controller.post_swipe.assert_called_once()

    def test_return_requires_verified_article_and_original_channel(self):
        value = worker(); value._comment_article_bar = Mock(side_effect=TaskIncomplete('不是文章页'))
        value._comment_click = Mock()
        with self.assertRaises(TaskIncomplete):
            value._comment_return_to_feed()
        value._comment_click.assert_not_called()
        value._comment_article_bar.side_effect = None; value._comment_life_selected = False
        with self.assertRaises(TaskIncomplete):
            value._comment_return_to_feed()
        value._comment_click.assert_not_called()

    def test_return_uses_one_back_and_waits_for_original_feed(self):
        value = worker(); value._comment_article_bar = Mock(); value._comment_life_selected = True
        value._comment_click = Mock(); value._comment_wait = Mock()
        value._comment_return_to_feed()
        value._comment_click.assert_called_once()
        value._comment_wait.assert_called_once_with(value._comment_life_feed, timeout=12)

    def test_real_video_poster_is_excluded_from_collected_written_description(self):
        root = Path(__file__).parent/'fixtures/comment-media-20261026'
        pages = [json.loads((root/f'video-page-{i}.json').read_text('utf-8')) for i in (1, 2)]
        article = comment_tasks.comment_article(pages[0], require_body=False)
        value = reading.CommentReadingTests().value(pages)
        # This test isolates text extraction; source rules have their own tests.
        with patch('comment_tasks.valid_article', return_value=True):
            full = value._comment_read_full_article(article)
        self.assertIn('体检报告', full['body'])
        self.assertNotIn('体检日期', full['body'])
        self.assertNotIn('03:19', full['body'])
        value.tasker.controller.post_input_text.assert_not_called()


if __name__ == '__main__':
    unittest.main()
