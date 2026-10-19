"""Realistic OCR overlap, reconciled storage and explicit import-mode boundaries."""
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from history_ledger import checked_ledger
from history_import import HistoryImport
from ledger_reading import LedgerReading, LedgerReadError, merge_frames, parse_ledger_frame, join_ledger_crops
from points_history import BEIJING, PointsHistory
from history_reader import HistoryReaderMixin
from task_support import TaskCancelled,TaskIncomplete
import threading


def entry(clock='10:00', name='阅读文章', points=1, **extra):
    return dict(time=clock,name=name,points=points,only_total=False,**extra)


def text(value,x,y,w=120,h=25):
    return dict(text=value,box=[x,y,w,h])


class LedgerParsingTests(unittest.TestCase):
    def test_overlapping_crops_use_interior_text_and_keep_a_complete_crossing_row(self):
        top=[text('10:31',40,700),text('阅读文音',40,800),text('+1分',590,800)]
        lower=[text('10:31',40,700),text('阅读文章',40,800),text('+1分',590,800)]
        records=join_ledger_crops([(top,850),(lower,1255)])
        self.assertEqual(parse_ledger_frame(records)[0]['name'],'阅读文章')
        self.assertEqual(sum(row['text']=='10:31' for row in records),1)

    def test_timestamp_wrapped_description_and_split_amount(self):
        rows=[text('10：32',40,720),text('订阅科普平台',40,760),text('（只计入总积分）',40,795),
              text('+',580,760,18),text('1分',610,760,50)]
        parsed=parse_ledger_frame(rows)
        self.assertEqual(parsed,[dict(time='10:32',name='订阅科普平台(只计入总积分)',
                                      points=1,only_total=True,y=720)])

    def test_decorative_quote_variants_do_not_change_the_subscription_name(self):
        for name in ['订阅“科普平台”','订阅“科普平台','订阅“科普平台\'']:
            rows=[text('10:32',40,720),text(name,40,760),text('+1分',590,760)]
            self.assertEqual(parse_ledger_frame(rows)[0]['name'],'订阅科普平台')

    def test_long_description_merged_with_score_keeps_the_wrapped_final_character(self):
        rows=[text('01:46',40,700),text('订阅“某大学”，只计入总积+1分',60,740,627),text('分',60,786)]
        self.assertEqual(parse_ledger_frame(rows),[dict(time='01:46',
            name='订阅某大学,只计入总积分',points=1,only_total=True,y=700)])

    def test_merged_and_separate_score_must_agree(self):
        rows=[text('01:46',40,700),text('订阅“某大学”+1分',60,740,627),text('+2分',600,740)]
        self.assertEqual(parse_ledger_frame(rows),[])

    def test_partial_next_timestamp_is_not_appended_to_previous_description(self):
        rows=[text('10:32',40,1140),text('阅读文章',40,1180),text('+1分',590,1180),text('10:31',40,1230)]
        self.assertEqual(parse_ledger_frame(rows)[0]['name'],'阅读文章')

    def test_clipped_dotted_clock_separates_rows_without_becoming_a_verified_time(self):
        rows=[text('15:39',40,1108),text('强国运动',40,1150),text('+1分',590,1150),
              text('01.11',40,1232)]
        self.assertEqual(parse_ledger_frame(rows)[0]['name'],'强国运动')
        rows=[text('15:39',40,700),text('强国运动',40,740),text('+1分',590,740),
              text('01.11',40,820),text('阅读文章',40,860),text('+1分',590,860),text('01:00',40,940)]
        self.assertEqual(parse_ledger_frame(rows),[dict(time='15:39',name='强国运动',points=1,only_total=False,y=700)])

    def test_partial_bottom_row_and_unanchored_scores_are_ignored(self):
        rows=[text('10:32',40,1200),text('阅读文章',40,1240),text('+1分',590,1240),
              text('年度积分5000',40,640),text('1分',590,680)]
        self.assertEqual(parse_ledger_frame(rows),[])

    def test_plausible_but_edge_clipped_description_is_deferred_to_next_frame(self):
        clipped=[text('01:53',40,1172),text('文音时长',60,1220,130,30),text('+1分',600,1215,90,37)]
        self.assertEqual(parse_ledger_frame(clipped,bottom=1255),[])
        clear=[text('01:53',40,813),text('文章时长',60,860),text('+1分',600,860)]
        self.assertEqual(parse_ledger_frame(clear,bottom=1255)[0]['name'],'文章时长')

    def test_clipped_or_invalid_timestamps_and_missing_score_are_not_guessed(self):
        for clock in ['10:','25:31','10:79']:
            self.assertEqual(parse_ledger_frame([text(clock,40,720),text('阅读文章',40,760),text('+1分',590,760)]),[])
        self.assertEqual(parse_ledger_frame([text('10:31',40,720),text('阅读文章',40,760)]),[])

    def test_scroll_overlap_preserves_identical_real_transactions(self):
        a,b,c=entry('10:03'),entry('10:02'),entry('10:01')
        self.assertEqual(merge_frames([a,b,b],[b,b,c],forward=True),[a,b,b,c])
        self.assertEqual(merge_frames([b,b,c],[a,b,b],forward=False),[a,b,b,c])

    def test_no_overlap_is_reported_instead_of_deduplicating_unrelated_rows(self):
        with self.assertRaises(LedgerReadError):
            merge_frames([entry('10:02')],[entry('10:01')],forward=True)

    def test_day_requires_correct_total_and_reverse_chronological_order(self):
        for points,rows in [(2,[entry()]),(2,[entry('10:00'),entry('11:00')])]:
            with self.assertRaises(ValueError):checked_ledger('2026-01-01',points,rows)
        self.assertEqual(checked_ledger('2026-01-01',0,[])['entries'],[])

    def test_names_are_data_and_negative_adjustments_are_preserved(self):
        rows=[entry('10:00','<b>平台项目</b>',3),entry('09:00','积分调整',-1)]
        self.assertEqual(checked_ledger('2026-01-01',2,rows)['entries'],rows)

    def test_invalid_records_are_rejected_without_coercing_types(self):
        for day,points,rows in [('20260101',1,[entry()]),('2026-01-01',True,[entry()]),
                                ('2026-01-01',1,[entry(name='')]),('2026-01-01',1,[entry(clock='2:01')])]:
            with self.assertRaises(ValueError):checked_ledger(day,points,rows)

    def test_complete_two_passes_must_agree_even_if_sums_match(self):
        reader=LedgerReading()
        reader._ledger_select=Mock(return_value=1)
        reader._sleep=Mock();reader._ledger_top=Mock()
        reader._ledger_pass=Mock(side_effect=[[entry(name='阅读文章')],[entry(name='视听学习')]])
        with self.assertRaises(LedgerReadError):reader._read_day_ledger('2026-01-01',1)

    def test_isolated_zero_is_read_only_next_to_explicit_daily_label(self):
        import numpy as np
        reader=LedgerReading();reader.tasker=Mock()
        reader.tasker.controller.cached_image=np.zeros((1280,720,3),dtype=np.uint8)
        def detail(label):
            return SimpleNamespace(status=SimpleNamespace(succeeded=True),nodes=[SimpleNamespace(
                recognition=SimpleNamespace(all_results=[SimpleNamespace(text=label,box=[0,0,140,30]),
                    SimpleNamespace(text='0',box=[180,0,20,30])]))])
        reader.tasker.post_recognition.return_value.wait.return_value.get.return_value=detail('当日积分：')
        self.assertEqual(reader._ledger_header_points(),0)
        reader.tasker.post_recognition.return_value.wait.return_value.get.return_value=detail('年度积分：')
        self.assertIsNone(reader._ledger_header_points())

    def test_selected_zero_day_can_use_local_header_ocr_without_guessing_from_blank(self):
        reader=LedgerReading();reader.history_date=datetime.now(BEIJING).date()
        day=reader.history_date.isoformat()
        rows=[text('积分明细',300,50),text('今日',100,135),text(day,40,640)]
        reader._history_records=Mock(return_value=rows)
        reader._ledger_header_points=Mock(return_value=0)
        reader.tasker=Mock();reader._sleep=Mock()
        self.assertEqual(reader._ledger_select(day),0)
        reader._ledger_header_points.assert_called_once()

    def test_cache_only_skips_matching_day_totals_and_excludes_today(self):
        reader=LedgerReading();reader.history_date=datetime.now(BEIJING).date()
        older=(reader.history_date-timedelta(days=2)).isoformat()
        recent=(reader.history_date-timedelta(days=1)).isoformat()
        reader.ledger_cache={older:dict(points=1),recent:dict(points=99)}
        reader.history_progress=Mock();reader.send_log=Mock()
        reader._read_day_ledger=Mock(return_value=dict(day=recent,points=1,entries=[entry()]))
        result=reader._read_history_ledgers({older:1,recent:1,reader.history_date.isoformat():1})
        reader._read_day_ledger.assert_called_once_with(recent,1)
        self.assertEqual(result['cached_ledger_days'],1)

    def test_transient_ocr_failure_gets_one_complete_reread_then_remains_bounded(self):
        reader=LedgerReading();reader.history_date=datetime.now(BEIJING).date()
        day=(reader.history_date-timedelta(days=1)).isoformat()
        reader.history_progress=Mock();reader.send_log=Mock()
        reader._read_day_ledger=Mock(side_effect=[LedgerReadError('画面尚未一致'),dict(day=day,points=1,entries=[entry()])])
        self.assertEqual(len(reader._read_history_ledgers({day:1})['ledgers']),1)
        self.assertEqual(reader._read_day_ledger.call_count,2)
        reader._read_day_ledger=Mock(side_effect=LedgerReadError('仍未一致'))
        self.assertEqual(len(reader._read_history_ledgers({day:1})['unconfirmed_ledgers']),1)
        self.assertEqual(reader._read_day_ledger.call_count,2)

    def test_cancel_or_platform_failure_is_not_retried_as_an_ocr_failure(self):
        for error in [TaskCancelled(),TaskIncomplete('需要本人验证')]:
            reader=LedgerReading();reader.history_date=datetime.now(BEIJING).date()
            reader.history_progress=Mock();reader._read_day_ledger=Mock(side_effect=error)
            day=(reader.history_date-timedelta(days=1)).isoformat()
            with self.assertRaises(type(error)):reader._read_history_ledgers({day:1})
            self.assertEqual(reader._read_day_ledger.call_count,1)

    def test_quick_collector_never_enters_the_slow_detail_reader(self):
        reader=HistoryReaderMixin()
        now=datetime.now(BEIJING);today=now.date().isoformat()
        yesterday=(now.date()-timedelta(days=1)).isoformat()
        reader._open_history=Mock(return_value=({},now.isoformat(),2))
        reader._history_pass=Mock(return_value={today:2,yesterday:1})
        reader._read_history_ledgers=Mock()
        reader._history_records=Mock(return_value=[])
        reader.tasker=Mock();reader.ai_resolver=Mock()
        result=reader.collect(Mock(),threading.Event())
        self.assertEqual(result['rows'],[dict(day=yesterday,points=1)])
        reader._read_history_ledgers.assert_not_called()
        reader.tasker.post_stop.assert_called_once()
        reader.ai_resolver.session.close.assert_called_once()


class LedgerPersistenceTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root=Path(folder.name);self.store=PointsHistory(self.root/'points.sqlite3')
        self.now=datetime.now(BEIJING);self.day=(self.now.date()-timedelta(days=1)).isoformat()
        self.snapshot=dict(day=self.day,points=2,entries=[entry(),entry()])

    def test_repeat_import_replaces_day_snapshot_without_losing_genuine_duplicates(self):
        self.assertEqual(self.store.import_day_ledgers([self.snapshot])['added'],1)
        self.assertEqual(self.store.import_day_ledgers([self.snapshot])['unchanged'],1)
        self.assertEqual(self.store.ledger_day(self.day)['entries'],self.snapshot['entries'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM score_transactions').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM scores').fetchone()[0],0)

    def test_late_older_import_cannot_replace_a_newer_verified_snapshot(self):
        self.store.import_day_ledgers([self.snapshot],observed_at=self.now.isoformat())
        older=self.now-timedelta(seconds=1)
        other=dict(day=self.day,points=1,entries=[entry()])
        self.assertEqual(self.store.import_day_ledgers([other],observed_at=older.isoformat())['ignored'],1)
        self.assertEqual(self.store.ledger_day(self.day)['summary']['points'],2)

    def test_invalid_batch_is_rejected_before_any_day_is_written(self):
        bad=dict(day=(self.now.date()-timedelta(days=2)).isoformat(),points=99,entries=[])
        with self.assertRaises(ValueError):self.store.import_day_ledgers([self.snapshot,bad])
        self.assertFalse(self.store.ledger_day(self.day)['available'])

    def test_today_and_future_are_not_imported_as_finished_history(self):
        with self.assertRaises(ValueError):
            self.store.import_day_ledgers([dict(day=self.now.date().isoformat(),points=0,entries=[])])

    def test_verified_zero_day_is_available_while_unknown_day_is_not(self):
        self.store.import_day_ledgers([dict(day=self.day,points=0,entries=[])])
        self.assertTrue(self.store.ledger_day(self.day)['available'])
        unknown=(self.now.date()-timedelta(days=2)).isoformat()
        self.assertFalse(self.store.ledger_day(unknown)['available'])

    def test_month_has_ledger_metadata_without_fabricating_task_scores(self):
        self.store.import_daily_totals([dict(day=self.day,points=2)])
        self.store.import_day_ledgers([self.snapshot])
        row=next(row for row in self.store.month(self.day[:7])['days'] if row['day']==self.day)
        self.assertTrue(row['ledger']['matches_total'])
        self.assertEqual(row['ledger']['entries'],2)
        self.assertEqual(row['known_tasks'],0)
        self.assertEqual(row['runs'],0)

    def test_full_and_quick_jobs_pass_the_explicit_mode_to_reader(self):
        result=dict(rows=[dict(day=self.day,points=2)],ledgers=[self.snapshot],read_days=2,
                    date_from=self.day,date_to=self.now.date().isoformat(),source='screen_ocr_verified')
        reader=Mock(return_value=result)
        job=HistoryImport(self.store,reader,self.root/'choice.json')
        job.start(include_details=True);job.thread.join(3)
        self.assertEqual(job.snapshot()['job']['phase'],'completed')
        self.assertTrue(reader.call_args.kwargs['include_details'])
        self.assertEqual(job.snapshot()['job']['ledger_counts']['entries'],2)
        reader.reset_mock();job.start();job.thread.join(3)
        self.assertEqual(reader.call_args.kwargs,{})
        self.assertFalse(job.snapshot()['job']['include_details'])

    def test_partial_details_keep_verified_totals_and_do_not_claim_complete_onboarding(self):
        result=dict(rows=[dict(day=self.day,points=2)],unconfirmed_ledgers=[dict(day=self.day,reason='两轮不一致')],
                    read_days=2,date_from=self.day,date_to=self.now.date().isoformat(),source='screen_ocr_verified')
        job=HistoryImport(self.store,Mock(return_value=result),self.root/'choice.json')
        job.start(include_details=True);job.thread.join(3)
        self.assertEqual(job.snapshot()['job']['phase'],'partial')
        self.assertTrue(job.snapshot()['show_onboarding'])
        self.assertTrue(self.store.has_imported_history())
        self.assertFalse(self.store.ledger_day(self.day)['available'])


if __name__=='__main__':unittest.main()
