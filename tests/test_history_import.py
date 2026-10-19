"""History parsing, durable first-use choices, cancellation and device ownership."""
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from history_reader import parse_day_summary, parse_history_chart, confirmed_history, HistoryReaderMixin
from history_import import HistoryImport
from points_history import PointsHistory, BEIJING
from task_support import TaskCancelled, TaskIncomplete


def text(value, x, y, width=60):
    return dict(text=value, box=[x, y, width, 30])


class HistoryParsingTests(unittest.TestCase):
    def test_merged_dates_are_matched_by_column_and_zero_is_retained(self):
        rows = [text('10.1710.18', 100, 135, 200), text('今日', 330, 135),
                text('24分', 122, 240), text('0分', 222, 500), text('42分', 330, 200)]
        self.assertEqual(parse_history_chart(rows, date(2026, 10, 19)),
                         {'2026-10-17': 24, '2026-10-18': 0, '2026-10-19': 42})

    def test_previous_year_and_invalid_or_future_axis_labels(self):
        rows = [text('12.31', 100, 135), text('02.30', 200, 135), text('01.03', 300, 135),
                text('5分', 100, 240), text('5分', 200, 240), text('5分', 300, 240)]
        self.assertEqual(parse_history_chart(rows, date(2026, 1, 2)), {'2025-12-31': 5})

    def test_annual_and_transaction_points_cannot_be_chart_scores(self):
        rows = [text('今日', 330, 135), text('年度积分3300', 330, 690),
                text('阅读 +1分', 330, 850), text('1分', 330, 850)]
        self.assertEqual(parse_history_chart(rows, date(2026, 10, 19)), {})
        self.assertEqual(parse_day_summary(rows), (None, None))

    def test_header_requires_full_date_and_explicit_daily_label(self):
        rows = [text('2026.10.19', 40, 590, 180), text('当日积分42', 420, 640, 180),
                text('年度积分3300', 40, 640, 180)]
        self.assertEqual(parse_day_summary(rows), (date(2026, 10, 19), 42))

    def test_conflicting_or_ambiguous_columns_are_not_guessed(self):
        rows = [text('10.18', 100, 135), text('今日', 160, 135), text('42分', 130, 230),
                text('1分', 100, 280), text('2分', 100, 350)]
        self.assertEqual(parse_history_chart(rows, date(2026, 10, 19)), {})

    def test_two_passes_reject_changed_or_unread_days(self):
        self.assertEqual(confirmed_history({'a': 1, 'b': 0, 'c': 4}, {'a': 2, 'b': 0}), {'b': 0})

    def test_after_human_verification_the_reader_uses_the_new_screen(self):
        reader = HistoryReaderMixin()
        reader._screen_records = Mock(side_effect=[[text('访问验证', 30, 50)], [text('积分明细', 300, 50)]])
        reader._guard_verification = Mock(return_value=True)
        self.assertEqual(reader._history_records()[0]['text'], '积分明细')


class HistoryJobTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.store = PointsHistory(self.root / 'points.sqlite3')
        self.now = datetime.now(BEIJING)
        self.day = (self.now.date() - timedelta(days=1)).isoformat()
        self.result = dict(rows=[dict(day=self.day, points=31)], read_days=2, unconfirmed_days=0,
            current_scores={'登录': dict(value=1, maximum=1)}, current_observed_at=self.now.isoformat(),
            date_from=self.day, date_to=self.now.date().isoformat(), source='screen_ocr_verified')

    def manager(self, reader=None):
        return HistoryImport(self.store, reader or Mock(return_value=self.result), self.root / 'choice.json')

    def finish(self, manager):
        manager.thread.join(3)
        self.assertFalse(manager.running)
        return manager.snapshot()

    def test_first_choice_can_be_skipped_and_survives_restart(self):
        job = self.manager()
        self.assertTrue(job.snapshot()['show_onboarding'])
        job.skip()
        self.assertEqual(self.manager().snapshot()['choice'], 'today')
        self.assertFalse(self.manager().snapshot()['show_onboarding'])
        self.assertEqual(self.store.month(self.day[:7])['days'], [])

    def test_legacy_history_does_not_repeat_first_use_prompt(self):
        self.store.import_daily_totals(self.result['rows'])
        self.assertEqual(self.manager().snapshot()['choice'], 'imported')

    def test_completed_import_records_totals_and_today_without_fake_runs(self):
        job = self.manager()
        job.start()
        state = self.finish(job)
        self.assertEqual(state['job']['phase'], 'completed')
        self.assertEqual(state['job']['counts'], dict(added=1, updated=0, unchanged=0, ignored=0))
        self.assertFalse(self.manager().snapshot()['show_onboarding'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM task_results').fetchone()[0], 0)

    def test_repeat_import_merges_by_date_instead_of_adding_points(self):
        job = self.manager()
        job.start()
        self.finish(job)
        job.start()
        state = self.finish(job)
        self.assertEqual(state['job']['counts']['unchanged'], 1)
        self.assertEqual(state['job']['counts']['added'], 0)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT SUM(points) FROM daily_totals').fetchone()[0], 31)

    def test_import_does_not_replace_a_newer_today_observation(self):
        newer = self.now + timedelta(seconds=10)
        self.store.observe({'登录': dict(value=0, maximum=1)}, observed_at=newer.isoformat())
        job = self.manager()
        job.start()
        self.finish(job)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT value FROM scores WHERE task='登录'").fetchone()[0], 0)

    def test_failed_reader_preserves_existing_records_and_ends_loading(self):
        self.store.import_daily_totals(self.result['rows'])
        job = self.manager(Mock(side_effect=TaskIncomplete('未读到日期，未保存。')))
        job.start()
        state = self.finish(job)
        self.assertEqual(state['job']['phase'], 'failed')
        self.assertEqual(state['job']['message'], '未读到日期，未保存。')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT points FROM daily_totals').fetchone()[0], 31)

    def test_cancelling_before_save_writes_no_partially_read_data(self):
        entered = threading.Event()
        def reader(progress, cancel):
            entered.set()
            if not cancel.wait(2):
                raise RuntimeError('cancel was not received')
            raise TaskCancelled()
        job = self.manager(reader)
        job.start()
        self.assertTrue(entered.wait(2))
        self.assertTrue(job.cancel())
        self.assertEqual(self.finish(job)['job']['phase'], 'cancelled')
        self.assertFalse(self.store.has_imported_history())

    def test_duplicate_start_keeps_one_reader_and_skip_cannot_race_it(self):
        release, entered = threading.Event(), threading.Event()
        def reader(progress, cancel):
            entered.set()
            release.wait(2)
            return self.result
        read = Mock(side_effect=reader)
        job = self.manager(read)
        first = job.start()
        self.assertTrue(entered.wait(2))
        try:
            self.assertEqual(job.start()['job']['id'], first['job']['id'])
            with self.assertRaises(ValueError):
                job.skip()
        finally:
            release.set()
            self.finish(job)
        read.assert_called_once()

    def test_partial_import_keeps_unconfirmed_days_unknown(self):
        self.result['unconfirmed_days'] = 1
        job = self.manager()
        job.start()
        state = self.finish(job)
        self.assertEqual(state['job']['phase'], 'partial')
        self.assertTrue(state['show_onboarding'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM daily_totals').fetchone()[0], 1)

    def test_restart_during_reading_shows_interruption_instead_of_permanent_spinner(self):
        (self.root / 'choice.json').write_text(json.dumps(dict(choice=None, job=dict(id='old', phase='reading'))))
        state = self.manager().snapshot()
        self.assertFalse(state['active'])
        self.assertEqual(state['job']['phase'], 'interrupted')

    def test_counts_distinguish_corrections_and_older_ignored_data(self):
        self.store.import_daily_totals(self.result['rows'], observed_at=self.now.isoformat())
        later = (self.now + timedelta(seconds=1)).isoformat()
        rows = [dict(day=self.day, points=32)]
        self.assertEqual(self.store.import_daily_totals(rows, observed_at=later, return_counts=True)['updated'], 1)
        counts = self.store.import_daily_totals(self.result['rows'], observed_at=self.now.isoformat(), return_counts=True)
        self.assertEqual(counts['ignored'], 1)


class HistoryApiTests(unittest.TestCase):
    def setUp(self):
        import webui
        self.webui = webui
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store = PointsHistory(Path(folder.name) / 'points.sqlite3')
        self.manager = HistoryImport(store, Mock(), Path(folder.name) / 'choice.json')
        self.state = Mock(history_import=self.manager, lock=threading.RLock(), completion=Mock())

    def test_learning_run_blocks_import_before_reading(self):
        from fastapi import HTTPException
        with patch.object(self.webui, 'app_state', self.state), patch.object(self.webui, 'task_running', return_value=True):
            with self.assertRaises(HTTPException) as caught:
                self.webui.start_history_import()
        self.assertEqual(caught.exception.status_code, 409)
        self.manager.reader.assert_not_called()

    def test_import_blocks_learning_before_worker_or_key_initialization(self):
        with patch.object(self.webui, 'app_state', self.state), patch.object(HistoryImport, 'running', new_callable=lambda: property(lambda _: True)), \
                patch.object(self.webui, 'ensure_worker') as worker:
            result = self.webui.start_selected(self.webui.TaskModel(tasklist=['登录']))
        self.assertEqual(result['status'], 'failed')
        worker.assert_not_called()

    def test_closing_session_blocks_import_and_inactive_cancel_has_conflict_status(self):
        from fastapi import HTTPException
        self.state.completion.cancel.return_value = False
        with patch.object(self.webui, 'app_state', self.state), patch.object(self.webui, 'task_running', return_value=False):
            with self.assertRaises(HTTPException):
                self.webui.start_history_import()
            with self.assertRaises(HTTPException):
                self.webui.cancel_history_import()
        self.manager.reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
