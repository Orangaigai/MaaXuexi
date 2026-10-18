"""Full article context before generation; no publication on incomplete input."""
import unittest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
import comment_tasks
from comment_reading import article_page, append_page
from task_support import TaskIncomplete, TaskUnavailable
from test_optimization import worker
import test_comments as comments


def row(text, y, x=25):
    return dict(text=text, box=[x, y, 500, 32])


TITLE = '一篇普通科普文章的标题'
HEAD = [row(TITLE, 191), row('来源：科普中国', 324)]
FIRST = HEAD + [row('这是开头提供的具体知识内容。'*4, 450), row('这句话在两屏重叠区域出现。', 900)]
LAST = [row('这句话在两屏重叠区域出现。', 200), row('后文还说明这些结论具有适用条件。', 400),
        row('责任编辑：测试', 700), row('阅读 1,234', 1000), row('点赞 25', 1000, 400)]
ARTICLE = dict(title=TITLE, body='第一屏', scope=comment_tasks.COMMENT_SCOPE)


class CommentReadingTests(unittest.TestCase):
    def test_first_page_excludes_title_and_source(self):
        lines, end = article_page(FIRST, first=True)
        self.assertFalse(end)
        self.assertNotIn(TITLE, lines)
        self.assertNotIn('来源：科普中国', lines)
        self.assertIn(FIRST[-1]['text'], lines)

    def test_end_requires_real_statistics_and_excludes_footer(self):
        lines, end = article_page(LAST)
        self.assertTrue(end)
        self.assertEqual(lines, [LAST[0]['text'], LAST[1]['text']])
        self.assertFalse(article_page([row('责任编辑：正文中的示例', 400)])[1])
        shifted = [dict(r, box=[r['box'][0], 700, 500, 32]) if r['text'].startswith('点赞') else r for r in LAST]
        self.assertFalse(article_page(shifted)[1])

    def test_only_exact_overlapping_lines_are_deduplicated(self):
        line = '研究说明这个结论有适用条件。'
        changed = '研究说明这个结论没有适用条件。'
        self.assertEqual(append_page([line], [line, changed, '结论'], [line, '结论']), [line, changed, '结论'])

    def value(self, frames):
        value = worker()
        value._screen_records = Mock(side_effect=frames)
        value._comment_article_bar = Mock()
        value._comment_article_top = Mock()
        value._sleep = Mock()
        return value

    def test_reads_to_confirmed_end_before_returning_full_body(self):
        value = self.value([FIRST, LAST])
        full = value._comment_read_full_article(ARTICLE)
        self.assertIn(LAST[1]['text'], full['body'])
        self.assertEqual(full['body'].count(LAST[0]['text']), 1)
        self.assertNotIn('责任编辑', full['body'])
        value._comment_article_top.assert_called_once_with(TITLE, 1)
        value.tasker.controller.post_swipe.assert_called_once_with(350, 1100, 350, 500, 600)
        value.tasker.controller.post_input_text.assert_not_called()

    def test_twelve_pages_without_end_are_not_used_as_full_context(self):
        value = self.value([FIRST]*12)
        with self.assertRaisesRegex(TaskUnavailable, '未确认文章结尾'):
            value._comment_read_full_article(ARTICLE)
        self.assertEqual(value.tasker.controller.post_swipe.call_count, 11)
        value._comment_article_top.assert_not_called()
        value.tasker.controller.post_input_text.assert_not_called()

    def test_overlong_context_is_not_truncated_for_publication(self):
        value = self.value([HEAD+[row('文字'*2500, 500)]])
        with self.assertRaisesRegex(TaskUnavailable, '未截断'):
            value._comment_read_full_article(ARTICLE)
        value.tasker.controller.post_swipe.assert_not_called()

    def test_changed_title_does_not_start_scrolling(self):
        value = self.value([[dict(r, text='另一篇文章的标题') if r['text']==TITLE else r for r in FIRST]])
        with self.assertRaises(TaskIncomplete):
            value._comment_read_full_article(ARTICLE)
        value.tasker.controller.post_swipe.assert_not_called()

    def test_header_can_identify_short_first_screen_without_approving_it_for_generation(self):
        self.assertIsNone(comment_tasks.comment_article(HEAD))
        self.assertEqual(comment_tasks.comment_article(HEAD, require_body=False)['title'], TITLE)

    def test_return_to_top_requires_the_same_title(self):
        value = worker()
        value._screen_records = Mock(side_effect=[LAST, FIRST])
        value._comment_article_bar = Mock(); value._sleep = Mock()
        value._comment_article_top(TITLE, 1)
        value.tasker.controller.post_swipe.assert_called_once_with(350, 350, 350, 1100, 600)

    def test_missing_article_bar_does_not_scroll_or_input(self):
        value = worker(); value._comment_ocr = Mock(return_value=[])
        with self.assertRaises(TaskIncomplete):
            value._comment_article_bar()
        value.tasker.controller.post_swipe.assert_not_called()
        value.tasker.controller.post_input_text.assert_not_called()

    def preparation(self):
        value = worker(); value.ai_resolver = Mock()
        value._comment_open_article = Mock(side_effect=[dict(ARTICLE, title=TITLE+str(i)) for i in range(3)])
        value._comment_read_full_article = Mock(side_effect=lambda article:article)
        value._screen_records = Mock(side_effect=TaskIncomplete('已到发布前身份核对'))
        return value

    def test_rejected_draft_moves_to_new_article_before_any_input(self):
        value = self.preparation()
        rejected = Mock(last_error='draft_failed_local_rules'); rejected.generate.return_value = None
        accepted = Mock(); accepted.generate.return_value = comments.DRAFT
        with patch('comment_tasks.ContextCommentDrafts', side_effect=[rejected, accepted]):
            with self.assertRaisesRegex(TaskIncomplete, '身份核对'):
                value._prepare_comment_draft()
        self.assertEqual(value._comment_rejected_titles, [TITLE+'0'])
        self.assertEqual(value._comment_open_article.call_count, 2)
        value.tasker.controller.post_input_text.assert_not_called()
        value.tasker.controller.post_click.assert_not_called()

    def test_all_rejected_candidates_end_without_publication(self):
        value = self.preparation()
        rejected = Mock(last_error='review_rejected'); rejected.generate.return_value = None
        with patch('comment_tasks.ContextCommentDrafts', return_value=rejected):
            with self.assertRaises(TaskUnavailable):
                value._prepare_comment_draft()
        self.assertEqual(rejected.generate.call_count, 3)
        value._screen_records.assert_not_called()
        value.tasker.controller.post_input_text.assert_not_called()

    def test_model_service_failure_does_not_generate_again_on_another_article(self):
        value = self.preparation()
        failed = Mock(last_error='draft_unavailable'); failed.generate.return_value = None
        with patch('comment_tasks.ContextCommentDrafts', return_value=failed):
            with self.assertRaises(TaskIncomplete):
                value._prepare_comment_draft()
        self.assertEqual(failed.generate.call_count, 1)


if __name__ == '__main__':
    unittest.main()
