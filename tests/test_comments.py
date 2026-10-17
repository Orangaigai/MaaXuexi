"""Public text limits, real UI fixtures, crash-safe send limits and dispatch."""
import json,sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

import comment_tasks
import task_support
import webui
from comment_tasks import COMMENT_MODE, COMMENT_SCOPE, CommentJournal, comment_policy, comment_draft_ready, comment_feed
from task_support import TaskIncomplete, TaskUnavailable, parse_score_rows
from test_optimization import worker

FIXTURES = Path(__file__).parent/'fixtures/comments'
DRAFT=json.loads((FIXTURES/'draft.json').read_text('utf-8'))
COMMENT_TEXT=DRAFT['comment']
ARTICLE=comment_tasks.comment_article(json.loads((FIXTURES/'article.json').read_text('utf-8')))
PREPARED=dict(article=ARTICLE,draft=DRAFT)


class CommentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for module, attr, name in ((comment_tasks, 'POLICY_PATH', 'policy.json'),
                                   (comment_tasks, 'JOURNAL_PATH', 'journal.sqlite3'),
                                   (task_support, 'REPORT_PATH', 'report.json')):
            patcher = patch.object(module, attr, Path(self.tmp.name)/name)
            patcher.start();self.addCleanup(patcher.stop)

    def approve(self, **changes):
        value = dict(enabled=True, mode=COMMENT_MODE, scope=COMMENT_SCOPE)
        value.update(changes)
        comment_tasks.POLICY_PATH.write_text(json.dumps(value), encoding='utf-8')

    def load(self, name):
        return json.loads((FIXTURES/name).read_text('utf-8'))

    def test_missing_or_invalid_policy_never_enables_public_posting(self):
        self.assertFalse(comment_policy()['enabled'])
        for value in ('{', 'null', '[]', 'true'):
            comment_tasks.POLICY_PATH.write_text(value, encoding='utf-8')
            self.assertFalse(comment_policy()['enabled'])

    def test_only_current_generation_mode_and_exact_scope_with_boolean_consent_are_accepted(self):
        self.approve();self.assertTrue(comment_policy()['enabled'])
        for changes in (dict(mode='fixed_text'),dict(scope='全部文章'),dict(enabled='true'),dict(enabled=1)):
            self.approve(**changes);self.assertFalse(comment_policy()['enabled'])

    def test_reserved_attempt_survives_a_new_process_even_without_confirmation(self):
        self.assertTrue(CommentJournal().reserve('生活科普文章',COMMENT_TEXT))
        other=CommentJournal()
        self.assertFalse(other.reserve('另一篇文章',COMMENT_TEXT))
        self.assertEqual(other.today()['state'],'attempted')
        other.credited();self.assertEqual(CommentJournal().today()['state'],'credited')

    def test_simultaneous_repeated_starts_can_reserve_only_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _: CommentJournal().reserve('科普文章',COMMENT_TEXT),range(4)))
        self.assertEqual(sum(results),1)

    def test_real_editor_requires_exact_short_text_and_own_publish_button(self):
        records=self.load('ready.json')
        self.assertEqual(comment_draft_ready(records,COMMENT_TEXT)['text'],'发布')
        for wrong in (COMMENT_TEXT+COMMENT_TEXT,'认真学习','好观点将会被优先展示'):
            altered=[dict(r,text=wrong) if r['text']==COMMENT_TEXT else r for r in records]
            self.assertIsNone(comment_draft_ready(altered,COMMENT_TEXT))
        self.assertIsNone(comment_draft_ready(records+[dict(text='访问验证',box=[300,150,120,30])],COMMENT_TEXT))
        self.assertIsNone(comment_draft_ready([r for r in records if r['text']!='发布'],COMMENT_TEXT))

    def test_article_with_same_text_cannot_be_mistaken_for_editor(self):
        records=[dict(text=COMMENT_TEXT,box=[25,450,400,35]),dict(text='发布',box=[630,1210,50,30])]
        self.assertIsNone(comment_draft_ready(records,COMMENT_TEXT))

    def test_real_feed_is_distinct_from_article_and_captcha(self):
        records=self.load('feed.json')
        self.assertTrue(comment_feed(records))
        self.assertFalse(comment_feed(self.load('ready.json')))
        self.assertFalse(comment_feed(records+[dict(text='访问验证',box=[300,150,120,30])]))

    def test_score_parser_separates_comment_from_local_and_weekly_exercise(self):
        scores=parse_score_rows(self.load('score.json'))
        self.assertEqual(scores['发表观点']['value'],0)
        self.assertEqual(scores['发表观点']['maximum'],1)
        self.assertEqual(scores['本地频道']['value'],0)
        self.assertNotIn('强国运动',scores)

    def test_full_score_never_opens_an_article_or_needs_public_consent(self):
        value=worker();value._prepare_comment_draft=Mock()
        self.assertEqual(value.publish_comment(dict(value=1,maximum=1))['value'],1)
        value._prepare_comment_draft.assert_not_called()

    def test_missing_consent_stops_before_article_navigation(self):
        value=worker();value._prepare_comment_draft=Mock()
        with self.assertRaises(TaskUnavailable):value.publish_comment(dict(value=0,maximum=1))
        value._prepare_comment_draft.assert_not_called()

    def test_ambiguous_previous_attempt_is_not_sent_again(self):
        self.approve();CommentJournal().reserve('旧文章',COMMENT_TEXT)
        value=worker();value._prepare_comment_draft=Mock()
        with self.assertRaisesRegex(TaskIncomplete,'未重复发送'):value.publish_comment(dict(value=0,maximum=1))
        value._prepare_comment_draft.assert_not_called()

    def publication_worker(self, score):
        self.approve();value=worker()
        value._prepare_comment_draft=Mock(return_value=PREPARED)
        value._comment_publish_target=Mock(return_value=dict(text='发布',box=[630,1210,50,30]))
        value._comment_click=Mock();value._comment_wait=Mock();value._comment_restore_home=Mock()
        value.read_learning_score=Mock(return_value={'发表观点':dict(value=score,maximum=1)})
        return value

    def test_only_actual_score_is_credited_and_no_extra_comment_for_zero_points(self):
        for score in (0,1):
            with self.subTest(score=score),patch.object(comment_tasks,'JOURNAL_PATH',Path(self.tmp.name)/f'{score}.sqlite3'):
                value=self.publication_worker(score)
                self.assertEqual(value.publish_comment(dict(value=0,maximum=1))['value'],score)
                value._comment_click.assert_called_once()
                self.assertEqual(CommentJournal().today()['state'],'credited' if score else 'attempted')

    def test_failed_click_retains_reservation_so_restart_cannot_duplicate(self):
        value=self.publication_worker(0)
        value._comment_click.side_effect=TaskIncomplete('timeout')
        with self.assertRaises(TaskIncomplete):value.publish_comment(dict(value=0,maximum=1))
        self.assertEqual(CommentJournal().today()['state'],'attempted')
        value._prepare_comment_draft.reset_mock()
        with self.assertRaisesRegex(TaskIncomplete,'未重复发送'):value.publish_comment(dict(value=0,maximum=1))
        value._prepare_comment_draft.assert_not_called()

    def test_changed_draft_or_revoked_consent_prevents_publish(self):
        value=self.publication_worker(0);value._comment_publish_target.return_value=None
        with self.assertRaises(TaskIncomplete):value.publish_comment(dict(value=0,maximum=1))
        value._comment_click.assert_not_called();self.assertIsNone(CommentJournal().today())
        value=self.publication_worker(0)
        def revoke():
            self.approve(enabled=False)
            return PREPARED
        value._prepare_comment_draft.side_effect=revoke
        with self.assertRaises(TaskIncomplete):value.publish_comment(dict(value=0,maximum=1))
        value._comment_click.assert_not_called();self.assertIsNone(CommentJournal().today())

    def test_main_worker_uses_verified_comment_score_without_extra_model_calls(self):
        value=worker();value._finish_existing_quiz=Mock()
        value.read_learning_score=Mock(return_value={'发表观点':dict(value=0,maximum=1)})
        value.publish_comment=Mock(return_value=dict(value=1,maximum=1))
        with patch('utils.read_report',return_value={}):value.task(['发表观点'])
        value.publish_comment.assert_called_once_with(dict(value=0,maximum=1))
        value.read_learning_score.assert_called_once_with(['发表观点'])
        self.assertEqual(value.run_report.snapshot()['status'],'completed')

    def test_expired_owner_verification_does_not_trigger_input_or_navigation_retry(self):
        value=worker();value._comment_open_article=Mock(return_value=ARTICLE)
        value.ai_resolver=Mock()
        value._screen_records=Mock(return_value=self.load('article.json'))
        value._comment_ocr=Mock(return_value=[dict(text='欢迎发表观点',box=[36,1225,128,25])])
        value._comment_click=Mock()
        value._comment_wait=Mock(side_effect=task_support.HumanRequired('等待验证超时'))
        with patch('comment_tasks.ContextCommentDrafts') as generator:
            generator.return_value.generate.return_value=DRAFT
            with self.assertRaises(task_support.HumanRequired):value._prepare_comment_draft()
        value.tasker.controller.post_input_text.assert_not_called()
        value.tasker.controller.post_swipe.assert_not_called()

    def test_changed_platform_cap_is_not_misreported_as_credited(self):
        value=self.publication_worker(0)
        value.read_learning_score.return_value={'发表观点':dict(value=2,maximum=2)}
        with self.assertRaisesRegex(TaskIncomplete,'核分规则发生变化'):
            value.publish_comment(dict(value=0,maximum=1))
        self.assertEqual(CommentJournal().today()['state'],'attempted')

    def test_default_schedule_and_api_do_not_enable_unapproved_comment(self):
        with patch.object(webui,'Path',side_effect=lambda path:Path(self.tmp.name)/path):
            plan=webui.get_task_plan()
        self.assertNotIn('发表观点',plan['tasklist'])
        with patch.object(webui,'task_running',return_value=False):
            with self.assertRaisesRegex(Exception,'409'):webui.save_task_plan(webui.TaskModel(tasklist=['发表观点']))
        self.assertNotIn('强国运动',task_support.ALIASES)

    def test_legacy_fixed_phrase_consent_cannot_enable_generated_comments(self):
        comment_tasks.POLICY_PATH.write_text(json.dumps(dict(enabled=True,text='认真学习，增长知识。',scope=COMMENT_SCOPE)),encoding='utf-8')
        self.assertFalse(comment_policy()['enabled'])

    def test_owner_resume_recognizes_article_when_bottom_placeholder_ocr_is_clipped(self):
        value=worker();value._last_comment_draft=PREPARED
        value._screen_records=Mock(return_value=self.load('article.json'))
        value._captcha_present=Mock(return_value=False)
        self.assertFalse(value._owner_resume_ready())
        self.assertTrue(value._owner_resume_ready())

    def test_article_history_is_retained_for_unconfirmed_sends_too(self):
        CommentJournal().reserve('AI没有心，却让你动了心',COMMENT_TEXT)
        self.assertEqual(CommentJournal().used_articles(),['AI没有心，却让你动了心'])
        CommentJournal().credited()
        self.assertEqual(CommentJournal().used_articles(),['AI没有心，却让你动了心'])

    def test_card_title_matching_ignores_ocr_punctuation_but_does_not_exclude_new_topics(self):
        titles=['AI没有心，却让你动了心']
        self.assertTrue(comment_tasks.comment_card_used('来源2026-10-16AI没有心,却让你动了心播报',titles))
        self.assertFalse(comment_tasks.comment_card_used('雨后为什么能看见彩虹',titles))


if __name__=='__main__':unittest.main()
