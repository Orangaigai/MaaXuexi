"""Explicit owner-started retries preserve history and never loop in one run."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import numpy as np
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import comment_tasks
from comment_tasks import CommentJournal
from task_support import TaskIncomplete, RunReport
import test_comments as existing
COMMENT_TEXT=existing.COMMENT_TEXT


class AttendedJournalTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/'comments.sqlite3'

    def test_owner_retry_appends_without_erasing_old_row_and_is_once_per_run(self):
        first=CommentJournal(self.path)
        self.assertTrue(first.reserve('旧文',COMMENT_TEXT,attempt_id='old-run'))
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db: original=db.execute('SELECT * FROM comments').fetchall()
        second=CommentJournal(self.path)
        self.assertTrue(second.reserve('新文',COMMENT_TEXT,owner_retry=True,attempt_id='owner-run'))
        self.assertFalse(second.reserve('另一文',COMMENT_TEXT,owner_retry=True,attempt_id='owner-run'))
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            self.assertEqual(db.execute('SELECT * FROM comments').fetchall(),original)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM comment_attempts').fetchone()[0],2)
        self.assertEqual(set(second.used_articles()),{'旧文','新文'})

    def test_unattended_and_unidentified_retry_are_blocked(self):
        journal=CommentJournal(self.path);journal.reserve('旧文',COMMENT_TEXT)
        self.assertFalse(journal.reserve('新文',COMMENT_TEXT,attempt_id='scheduled'))
        self.assertFalse(journal.reserve('新文',COMMENT_TEXT,owner_retry=True))

    def test_credit_updates_only_its_attempt_and_stops_further_retries(self):
        journal=CommentJournal(self.path);journal.reserve('旧文',COMMENT_TEXT,attempt_id='first')
        journal=CommentJournal(self.path);journal.reserve('新文',COMMENT_TEXT,owner_retry=True,attempt_id='second')
        journal.credited()
        self.assertEqual(journal.today()['state'],'credited')
        self.assertFalse(journal.reserve('第三文',COMMENT_TEXT,owner_retry=True,attempt_id='third'))
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            self.assertEqual(db.execute('SELECT state FROM comment_attempts WHERE attempt_id=?',('first',)).fetchone()[0],'attempted')

    def test_existing_database_backfills_without_changing_original_rows(self):
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            db.execute('CREATE TABLE comments(day TEXT PRIMARY KEY,article TEXT,text TEXT,state TEXT,created_at TEXT)')
            db.execute('INSERT INTO comments VALUES(?,?,?,?,?)',('2026-10-17','历史文章',COMMENT_TEXT,'credited','2026-10-17T10:00:00+08:00'))
        self.assertEqual(CommentJournal(self.path).used_articles(),['历史文章'])
        self.assertEqual(CommentJournal(self.path).used_articles(),['历史文章'])
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM comment_attempts').fetchone()[0],1)

    def test_same_text_retry_credit_does_not_relabel_original_failed_attempt(self):
        journal=CommentJournal(self.path);journal.reserve('同一文章',COMMENT_TEXT,attempt_id='first')
        journal=CommentJournal(self.path);journal.reserve('同一文章',COMMENT_TEXT,owner_retry=True,attempt_id='second')
        journal.credited()
        with closing(sqlite3.connect(self.path,isolation_level=None)) as db:
            self.assertEqual(db.execute('SELECT state FROM comments').fetchone()[0],'attempted')
            self.assertEqual(db.execute('SELECT state FROM comment_attempts WHERE attempt_id=?',('first',)).fetchone()[0],'attempted')


class AttendedDispatchTests(unittest.TestCase):
    def test_return_from_article_reuses_guarded_home_recovery_without_history_clicks(self):
        value=existing.worker();value._guard_verification=Mock()
        value._screen_records=Mock(return_value=[])
        value._comment_ocr=Mock(return_value=[dict(text='欢迎发表观点',box=[30,1220,200,30])])
        value._comment_click=Mock();value._go_home=Mock();value._comment_wait=Mock()
        value._comment_restore_home()
        value._go_home.assert_called_once()
        value._comment_click.assert_not_called()
        value._comment_wait.assert_not_called()
        value.tasker.post_task.assert_not_called()

    def test_return_already_at_home_does_not_open_channel_menu(self):
        value=existing.worker();value._guard_verification=Mock()
        value._screen_records=Mock(return_value=[])
        value._comment_ocr=Mock(return_value=[dict(text='欢迎发表观点',box=[30,1220,200,30])])
        value._comment_click=Mock();value._comment_life_feed=Mock(return_value=False)
        value._comment_wait=Mock(side_effect=lambda predicate: [] if predicate([]) else self.fail('Home was not accepted'))
        value._comment_channel_menu=Mock();value._wait_home_ready=Mock()
        with patch.object(comment_tasks,'home_ready',return_value=True):value._comment_restore_home()
        value._comment_click.assert_not_called()
        value._comment_channel_menu.assert_not_called()

    def native_page(self):
        records=[dict(text='科普中国',box=[312,58,115,35])]
        records += [dict(text=text,box=[x,130,92,30]) for text,x in [('生活百科',174),('科学求真',302),('三农科普',430)]]
        image=np.full((1280,720,3),255,np.uint8)
        image[130:160,174:266]=[0,0,220]
        return records,image

    def test_native_channel_requires_header_tabs_and_selected_red_life_marker(self):
        value=existing.worker();records,image=self.native_page()
        value.tasker.controller.cached_image=image
        self.assertTrue(value._comment_native_life_page(records))
        self.assertFalse(value._comment_native_life_page(records[1:]))
        value.tasker.controller.cached_image[:]=255
        self.assertFalse(value._comment_native_life_page(records))

    def test_recovery_already_in_native_channel_avoids_its_link_back_to_the_article(self):
        value=existing.worker();records,image=self.native_page()
        value.tasker.controller.cached_image=image
        value._screen_records=Mock(return_value=records);value._guard_verification=Mock()
        value._comment_click=Mock();value._go_home=Mock();value._comment_ocr=Mock()
        value._comment_restore_home()
        value._go_home.assert_called_once()
        value._comment_click.assert_not_called()
        value._comment_ocr.assert_not_called()

    def test_unknown_page_does_not_restart_or_publish(self):
        value=existing.worker();value._screen_records=Mock(return_value=[])
        value._guard_verification=Mock();value._comment_ocr=Mock(return_value=[])
        value._go_home=Mock();value._comment_click=Mock()
        with self.assertRaises(TaskIncomplete):value._comment_restore_home()
        value._go_home.assert_not_called();value._comment_click.assert_not_called()

    def test_real_verification_prevents_article_return_click(self):
        value=existing.worker();value._comment_click=Mock()
        value._guard_verification=Mock(side_effect=TaskIncomplete('本人验证尚未完成'))
        with self.assertRaises(TaskIncomplete):value._comment_restore_home()
        value._comment_click.assert_not_called()

    def test_normal_worker_run_can_retry_but_scheduled_run_cannot(self):
        case=existing.CommentTests();case.setUp()
        try:
            first=CommentJournal();first.reserve('旧文',COMMENT_TEXT)
            scheduled=case.publication_worker(0);scheduled.unattended=True
            scheduled.run_report=RunReport(['发表观点'])
            with self.assertRaises(TaskIncomplete):scheduled.publish_comment(dict(value=0,maximum=1))
            scheduled._prepare_comment_draft.assert_not_called()
            manual=case.publication_worker(1);manual.unattended=False
            manual.run_report=RunReport(['发表观点'])
            self.assertEqual(manual.publish_comment(dict(value=0,maximum=1))['value'],1)
            manual._comment_click.assert_called_once()
            self.assertEqual(CommentJournal().today()['state'],'credited')
        finally: case.doCleanups()


if __name__=='__main__':unittest.main()
