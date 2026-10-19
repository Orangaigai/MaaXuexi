"""Persistent daily scores, same-day reruns and Beijing date boundaries."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
from points_history import PointsHistory
from task_support import ALIASES


def report(run='run_1', status='completed', started='2026-10-19T08:00:00+08:00', finished='2026-10-19T08:20:00+08:00'):
    return dict(run_id=run, status=status, started_at=started, finished_at=finished,
                tasks={'登录': dict(state='verified', before=dict(value=0, maximum=1), after=dict(value=1, maximum=1))})


class PointsHistoryTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'history.sqlite3';self.store=PointsHistory(self.path)

    def test_reruns_update_daily_score_without_adding_or_duplicating_runs(self):
        first=report();self.store.record_run(first);self.store.record_run(first)
        self.store.record_run(report('run_2'))
        reopened=PointsHistory(self.path).month('2026-10')
        self.assertEqual(reopened['month_points'],1)
        self.assertEqual(reopened['days'][0]['runs'],2)

    def test_old_import_does_not_replace_a_new_actual_score(self):
        self.store.observe({'登录':dict(value=1,maximum=1)},observed_at='2026-10-19T10:00:00+08:00')
        old=report();old['tasks']['登录']['after']['value']=0
        self.store.record_run(old)
        self.assertEqual(self.store.month('2026-10')['days'][0]['points'],1)

    def test_failure_without_score_is_unknown_and_observed_zero_is_real_zero(self):
        failed=report(status='failed');failed['tasks']['登录']=dict(state='failed')
        self.store.record_run(failed)
        self.assertIsNone(self.store.month('2026-10')['days'][0]['points'])
        self.store.observe({'登录':dict(value=0,maximum=1)},observed_at='2026-10-19T10:00:00+08:00')
        self.assertEqual(self.store.month('2026-10')['days'][0]['points'],0)

    def test_utc_observation_uses_beijing_calendar_day(self):
        self.store.observe({'登录':dict(value=1,maximum=1)},observed_at='2026-10-18T18:00:00+00:00')
        self.assertEqual(self.store.month('2026-10')['days'][0]['day'],'2026-10-19')

    def test_cross_midnight_result_is_not_assigned_to_previous_day(self):
        value=report(started='2026-10-18T23:59:00+08:00',finished='2026-10-19T00:01:00+08:00')
        self.store.record_run(value)
        self.assertEqual(self.store.month('2026-10')['days'][0]['points'],0)

    def test_invalid_scores_and_private_report_fields_are_not_stored(self):
        self.store.observe({'登录':dict(value=True,maximum=1),'订阅':dict(value=3,maximum=2),
            '密钥':dict(value=1,maximum=1)},observed_at='2026-10-19T10:00:00+08:00')
        self.assertEqual(self.store.month('2026-10')['days'],[])
        value=report();value['api_key']='DO_NOT_STORE_THIS';value['tasks']['登录']['body']='PRIVATE_BODY'
        self.store.record_run(value)
        content=self.path.read_bytes()
        self.assertNotIn(b'DO_NOT_STORE_THIS',content);self.assertNotIn(b'PRIVATE_BODY',content)

    def test_task_state_and_reason_survive_restart_without_a_score(self):
        value=report(status='needs_human')
        value['tasks']={'趣味答题':dict(state='needs_human',reason='请本人完成当前访问验证',metrics={'mode':'四人赛'})}
        self.store.record_run(value)
        row=PointsHistory(self.path).month('2026-10')['days'][0]
        self.assertIsNone(row['points'])
        self.assertEqual(row['results']['趣味答题'],dict(state='needs_human',reason='请本人完成当前访问验证',mode='四人赛'))

    def test_old_running_snapshot_does_not_erase_completed_task_result(self):
        finished=report();finished['tasks']['登录']['reason']='积分已核实'
        self.store.record_run(finished)
        stale=report(status='running',finished=None);stale['tasks']['登录']['state']='running'
        self.store.record_run(stale)
        self.assertEqual(self.store.month('2026-10')['days'][0]['results']['登录'],
                         dict(state='verified',reason='积分已核实',mode=''))

    def test_existing_score_database_is_extended_without_losing_records(self):
        from contextlib import closing
        import sqlite3
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE scores (day TEXT,task TEXT,value INTEGER,maximum INTEGER,observed_at TEXT,PRIMARY KEY(day,task))')
            db.execute('INSERT INTO scores VALUES(?,?,?,?,?)',('2026-10-18','登录',1,1,'2026-10-18T08:00:00+08:00'))
        self.store.record_run(report())
        days=self.store.month('2026-10')['days']
        self.assertEqual([row['points'] for row in days],[1,1])
        self.assertEqual(days[1]['results']['登录']['state'],'verified')

    def test_missing_tasks_are_marked_as_partial_snapshot(self):
        self.store.record_run(report())
        row=self.store.month('2026-10')['days'][0]
        self.assertEqual(row['known_tasks'],1);self.assertFalse(row['complete_snapshot'])

    def test_concurrent_observations_keep_each_tasks_own_score(self):
        def save(name):
            PointsHistory(self.path).observe({name:dict(value=1,maximum=2)},observed_at='2026-10-19T10:00:00+08:00')
        with ThreadPoolExecutor(max_workers=4) as executor:list(executor.map(save,ALIASES))
        row=self.store.month('2026-10')['days'][0]
        self.assertEqual(row['points'],8);self.assertTrue(row['complete_snapshot'])

    def test_newer_pending_run_does_not_report_day_as_completed(self):
        self.store.record_run(report())
        self.store.record_run(report('run_2',status='needs_human',started='2026-10-19T09:00:00+08:00',finished='2026-10-19T09:05:00+08:00'))
        self.assertEqual(self.store.month('2026-10')['days'][0]['status'],'needs_human')

    def test_invalid_month_is_rejected_and_empty_month_has_no_invented_dates(self):
        for month in ('2026-13','2026-1',"2026-10' OR 1=1",'1969-12'):
            with self.subTest(month=month),self.assertRaises(ValueError):self.store.month(month)
        self.assertEqual(self.store.month('2026-02')['days'],[])

    def test_platform_history_is_idempotent_and_does_not_invent_task_results(self):
        rows=[dict(day='2026-10-18',points=44),dict(day='2026-09-27',points=0)]
        for _ in range(2):self.store.import_daily_totals(rows,observed_at='2026-10-19T19:00:00+08:00')
        october=PointsHistory(self.path).month('2026-10')
        day=october['days'][0]
        self.assertEqual(october['month_points'],44)
        self.assertEqual(october['recorded_days'],1)
        self.assertEqual(day['points_source'],'xuexi_points_detail')
        self.assertEqual(day['tasks'],{})
        self.assertEqual(day['runs'],0)
        self.assertNotIn('results',day)
        self.assertEqual(day['status'],'observed')
        self.assertEqual(self.store.month('2026-09')['days'][0]['points'],0)

    def test_historical_total_takes_precedence_over_an_incomplete_old_task_snapshot(self):
        self.store.observe({'登录':dict(value=1,maximum=1)},observed_at='2026-10-18T08:00:00+08:00')
        self.store.import_daily_totals([dict(day='2026-10-18',points=44)],observed_at='2026-10-19T19:00:00+08:00')
        row=self.store.month('2026-10')['days'][0]
        self.assertEqual(row['points'],44)
        self.assertEqual(row['tasks']['登录']['value'],1)
        self.assertFalse(row['complete_snapshot'])

    def test_invalid_history_batch_is_atomic_and_cannot_write_today_or_future(self):
        for invalid in (dict(day='2026-10-19',points=42),dict(day='2026-10-20',points=1),
                dict(day='2026-02-30',points=1),dict(day='20261017',points=1),
                dict(day='2026-10-17',points=True),dict(day='2026-10-17',points=-1),
                dict(day='2026-10-17',points=1001)):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                self.store.import_daily_totals([dict(day='2026-10-18',points=44),invalid],observed_at='2026-10-19T19:00:00+08:00')
            self.assertEqual(self.store.month('2026-10')['days'],[])
        with self.assertRaises(ValueError):
            self.store.import_daily_totals([dict(day='2026-10-18',points=44),dict(day='2026-10-18',points=40)],
                observed_at='2026-10-19T19:00:00+08:00')

    def test_old_history_cannot_replace_newer_confirmed_total_and_today_is_unchanged(self):
        self.store.record_run(report())
        self.store.import_daily_totals([dict(day='2026-10-18',points=44)],observed_at='2026-10-19T19:00:00+08:00')
        self.store.import_daily_totals([dict(day='2026-10-18',points=40)],observed_at='2026-10-19T18:00:00+08:00')
        month=self.store.month('2026-10')
        self.assertEqual([row['points'] for row in month['days']],[44,1])
        self.assertEqual(month['month_points'],45)
        self.assertEqual(month['recorded_days'],2)
        self.assertNotIn('points_source',month['days'][1])


if __name__=='__main__':unittest.main()
