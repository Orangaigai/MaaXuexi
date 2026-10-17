"""Subscription history, real catalog OCR and one-account score gating."""
import json
import sys
import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from subscription_store import SubscriptionStore, account_key
from subscription_tasks import SubscriptionTasks, subscription_accounts, subscription_scene, owned_list_button, category_selected, cancellation_action, MAX_ATTEMPTS
from task_support import RunReport, TaskIncomplete, HumanRequired, ordered_tasks, parse_score_rows
import task_support

FIXTURES = Path(__file__).parent/'fixtures'/'subscriptions'


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SubscriptionStore(Path(self.tmp.name)/'history.sqlite3')

    def test_new_once_cancelled_still_deduplicated(self):
        self.assertTrue(self.store.eligible('国学频道'))
        attempt=self.store.begin('国学频道')
        self.store.cleaned(attempt,completed=True)
        self.assertFalse(self.store.eligible('国学频道'))
        self.assertEqual(self.store.stats(),dict(accounts=1,completed_operations=1,pending_cleanup=0))
        self.store.cleaned(attempt,completed=True)
        self.assertEqual(self.store.stats()['completed_operations'],1)

    def test_intent_survives_reopen_before_any_success_response(self):
        attempt=self.store.begin('首创和园')
        reopened=SubscriptionStore(self.store.path)
        self.assertEqual(reopened.pending()[0]['id'],attempt)
        self.assertEqual(reopened.pending()[0]['completed_count'],0)
        with self.assertRaises(ValueError):reopened.begin('首创和园')

    def test_next_round_allows_retry_without_losing_results(self):
        attempt=self.store.begin('频道')
        self.assertFalse(self.store.eligible('频道'))
        self.assertFalse(self.store.finish_round(scanned_accounts=1))
        self.store.cleaned(attempt,completed=True)
        self.assertTrue(self.store.finish_round(scanned_accounts=1))
        self.assertTrue(self.store.eligible('频道'))
        self.assertEqual(self.store.progress()['current_round'],2)
        again=self.store.begin('频道')
        self.assertEqual(self.store.pending()[0]['completed_count'],1)
        self.store.cleaned(again,completed=True)
        self.assertEqual(self.store.stats()['accounts'],1)
        self.assertEqual(self.store.stats()['completed_operations'],2)

    def test_no_credit_also_deduplicated_and_empty_scan_never_rotates(self):
        attempt=self.store.begin('无效频道');self.store.cleaned(attempt)
        self.assertFalse(self.store.eligible('无效频道'))
        with self.assertRaises(ValueError):self.store.begin('无效频道')
        self.assertEqual(self.store.stats()['completed_operations'],0)
        self.assertFalse(self.store.finish_round(scanned_accounts=0))
        self.assertEqual(self.store.progress()['current_round'],1)

    def test_cursor_survives_restart_and_resets_on_round_change(self):
        attempt=self.store.begin('频道');self.store.cleaned(attempt)
        self.store.save_category_cursor(3)
        reopened=SubscriptionStore(self.store.path)
        self.assertEqual(reopened.progress(),dict(current_round=1,category_cursor=3))
        self.assertTrue(reopened.finish_round(1))
        self.assertEqual(reopened.progress(),dict(current_round=2,category_cursor=0))

    def test_legacy_journal_migration_preserves_history(self):
        path=Path(self.tmp.name)/'legacy.sqlite3'
        db=sqlite3.connect(path)
        db.execute('''CREATE TABLE attempts(id INTEGER PRIMARY KEY, account_key TEXT,
            name TEXT,kind TEXT,run_id TEXT,attempted_at TEXT,before_score INTEGER,
            after_score INTEGER,result TEXT,cleanup TEXT,cleaned_at TEXT)''')
        db.execute('INSERT INTO attempts VALUES(1,?,?,?,?,?,?,?,?,?,?)',
                   (account_key('频道'),'频道','强国号','old','2026-10-17T03:43:00+00:00',0,1,'credited','done',None))
        db.commit();db.close()
        migrated=SubscriptionStore(path)
        self.assertEqual(migrated.stats()['completed_operations'],0)
        self.assertTrue(path.with_name('legacy.before-counts.sqlite3').is_file())
        self.assertFalse(migrated.eligible('频道'))
        self.assertTrue(migrated.finish_round(1))
        self.assertTrue(migrated.eligible('频道'))

    def test_kind_distinguishes_same_display_name(self):
        self.assertNotEqual(account_key('同名','强国号'),account_key('同名','地方平台'))
        self.assertEqual(account_key(' 国 学 频道 '),account_key('国学频道'))


class RecognitionTests(unittest.TestCase):
    def test_cancelled_row_can_remain_visible_with_subscribe_button(self):
        rows=json.loads((FIXTURES/'mine-merged-button.json').read_text('utf-8'))
        value=SubscriptionTasks()
        self.assertTrue(value._sub_row_cancelled(rows,'复旦大学'))
        self.assertIsNone(owned_list_button(rows,'复旦大学'))
        self.assertIsNotNone(owned_list_button(rows,'复旦大学','订阅'))

    def test_description_merged_with_button_uses_only_the_right_hand_row_crop(self):
        from types import SimpleNamespace
        rows=json.loads((FIXTURES/'mine-merged-button.json').read_text('utf-8'))
        self.assertIsNone(owned_list_button(rows,'清华大学'))
        value=SubscriptionTasks();value.tasker=Mock()
        value._check_active=Mock();value._guard_verification=Mock();value._app_in_foreground=Mock(return_value=True)
        value.tasker.post_task.return_value.wait.return_value.get.return_value=SimpleNamespace(
            status=SimpleNamespace(failed=False),nodes=[SimpleNamespace(recognition=SimpleNamespace(
                all_results=[SimpleNamespace(text='已订阅',box=[622,347,75,26])]))])
        self.assertEqual(value._sub_list_button(rows,'清华大学')['text'],'已订阅')
        task,override=value.tasker.post_task.call_args.args
        self.assertEqual(override[task]['roi'],[615,320,72,60])
        self.assertEqual(override[task]['action'],'DoNothing')
        value.tasker.controller.post_click.assert_not_called()

    def test_observed_list_pairs_each_owned_name_with_its_own_button(self):
        rows=json.loads((FIXTURES/'mine-populated.json').read_text('utf-8'))
        for name,y in [('北京大学',223),('浙大学术期刊',347),('1905电影网',480),
                       ('人民日报',602),('中国教育出版网',714),('退役军人之家',824)]:
            self.assertEqual(owned_list_button(rows,name)['box'][1],y)
        self.assertIsNone(owned_list_button(rows,'退役军人'))
        self.assertIsNone(owned_list_button(rows,None))

    def test_cancel_confirmation_prefers_action_button_over_dialog_title(self):
        title=dict(text='取消订阅',box=[280,480,150,35])
        confirm=dict(text='确认',box=[450,720,80,35])
        self.assertEqual(cancellation_action([title,confirm]),confirm)
        self.assertIsNone(cancellation_action([title]))
        self.assertIsNone(cancellation_action([title,confirm,dict(text='访问验证',box=[300,134,110,31])]))
    def test_observed_red_category_marker_distinguishes_waiting_from_recommended(self):
        import numpy as np
        from PIL import Image
        records=json.loads((FIXTURES/'catalog.json').read_text('utf-8'))
        image=np.asarray(Image.open(FIXTURES/'catalog-selected.png').convert('RGB'))[:,:,::-1]
        self.assertTrue(category_selected(records,image,'待订阅'))
        self.assertFalse(category_selected(records,image,'推荐'))
    def test_observed_catalog_pairs_headers_in_visual_order(self):
        records=json.loads((FIXTURES/'catalog.json').read_text('utf-8'))
        self.assertEqual(subscription_scene(records),'catalog')
        names=[row['name'] for row in subscription_accounts(records)]
        self.assertEqual(names[:3],['中央新影发现之旅频道','首创和园','退役军人之家'])
        self.assertIn('国学频道',names)
        self.assertNotIn('牌传播力榜榜首',names)

    def test_ambiguous_header_or_truncated_name_is_not_clicked(self):
        button=dict(text='订阅',box=[640,145,45,25])
        name=dict(text='频道',box=[215,144,80,25])
        self.assertEqual(len(subscription_accounts([button,name])),1)
        self.assertEqual(subscription_accounts([button,name,dict(text='其他',box=[350,145,80,25])]),[])
        self.assertEqual(subscription_accounts([button,dict(name,text='频道…')]),[])

    def test_verification_overrides_background_catalog(self):
        rows=[dict(text='强国号',box=[245,65,80,32]),dict(text='地方平台',box=[365,68,105,30]),
              dict(text='访问验证',box=[303,134,113,31])]
        self.assertIsNone(subscription_scene(rows))

    def test_subscription_score_has_its_own_fraction(self):
        data=json.loads((Path(__file__).parent/'fixtures'/'score-ocr-fixtures.json').read_text('utf-8'))
        # The legacy fixture contains neighboring quiz and comment fractions too.
        scores={}
        for records in data.values():scores.update(parse_score_rows(records))
        self.assertEqual(scores['订阅']['maximum'],2)

    def test_subscription_before_quizzes(self):
        self.assertEqual(ordered_tasks(['每日答题','趣味答题','订阅','登录']),['登录','趣味答题','订阅','每日答题'])

    def test_another_accounts_button_does_not_authorize_cancel(self):
        rows=[dict(text='我的订阅',box=[307,55,103,30]),dict(text='添加',box=[641,53,58,34]),
              dict(text='原有关注',box=[105,200,120,25]),dict(text='已订阅',box=[622,223,75,26])]
        self.assertIsNone(owned_list_button(rows,'本机新增'))
        self.assertIsNotNone(owned_list_button(rows,'原有关注'))
        rows.append(dict(text='访问验证',box=[300,134,110,31]))
        self.assertIsNone(owned_list_button(rows,'原有关注'))


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=SubscriptionStore(Path(self.tmp.name)/'history.sqlite3')
        patcher=patch.object(task_support,'REPORT_PATH',Path(self.tmp.name)/'report.json')
        patcher.start();self.addCleanup(patcher.stop)
        self.value=SubscriptionTasks()
        self.value.run_report=RunReport(['订阅'])
        self.value._check_active=Mock();self.value.send_log=Mock()
        self.value._sub_cleanup=Mock(side_effect=lambda store:[store.cleaned(r['id'],completed=True) for r in store.pending()])

    def test_one_follow_one_score_then_stop_at_two_and_cleanup(self):
        value=self.value
        value._sub_score=Mock(side_effect=[dict(value=0,maximum=2),dict(value=2,maximum=2)])
        value._sub_candidate=Mock(side_effect=[dict(name='甲'),dict(name='乙')])
        def follow(account,store,before):
            store.begin(account['name'])
            return dict(value=before['value']+1,maximum=2)
        value._sub_follow=Mock(side_effect=follow)
        with patch('subscription_tasks.SubscriptionStore',return_value=self.store):value.subscribe()
        self.assertEqual(value._sub_candidate.call_count,2)
        self.assertEqual(value._sub_follow.call_count,2)
        value._sub_score.assert_called_once()
        self.assertEqual(self.store.pending(),[])
        self.assertEqual(value.run_report.snapshot()['tasks']['订阅']['after']['value'],2)

    def test_no_credit_cannot_loop_forever(self):
        value=self.value;value._sub_score=Mock(return_value=dict(value=0,maximum=2))
        value._sub_candidate=Mock(return_value=dict(name='candidate'))
        value._sub_follow=Mock(return_value=dict(value=0,maximum=2))
        with patch('subscription_tasks.SubscriptionStore',return_value=self.store):value.subscribe()
        self.assertEqual(value._sub_follow.call_count,MAX_ATTEMPTS)
        value._sub_cleanup.assert_called_once()

    def test_full_score_still_cleans_previous_pending_follow(self):
        self.store.begin('旧中断')
        self.value._sub_score=Mock(return_value=dict(value=2,maximum=2))
        self.value._sub_candidate=Mock()
        with patch('subscription_tasks.SubscriptionStore',return_value=self.store):self.value.subscribe()
        self.value._sub_candidate.assert_not_called()
        self.assertEqual(self.store.pending(),[])

    def test_no_candidates_preserves_history(self):
        self.value._sub_score=Mock(return_value=dict(value=0,maximum=2))
        self.value._sub_candidate=Mock(return_value=None)
        self.value._sub_follow=Mock()
        with patch('subscription_tasks.SubscriptionStore',return_value=self.store):self.value.subscribe()
        self.value._sub_follow.assert_not_called()
        self.assertEqual(self.value._sub_candidate.call_count,1)

    def test_cleanup_uses_already_observed_score_without_another_score_visit(self):
        self.value._sub_score=Mock(return_value=dict(value=1,maximum=2))
        self.value._sub_candidate=Mock(return_value=None)
        with patch('subscription_tasks.SubscriptionStore',return_value=self.store):
            self.assertEqual(self.value.subscribe(),dict(value=1,maximum=2))
        self.value._sub_score.assert_called_once()

    def test_finished_round_is_saved_for_next_run_without_immediate_replay(self):
        attempt=self.store.begin('已尝试');self.store.cleaned(attempt)
        self.value._sub_score=Mock(return_value=dict(value=0,maximum=2))
        def exhausted(store):
            self.value._sub_scan_observed=1
            return None
        self.value._sub_candidate=Mock(side_effect=exhausted)
        self.value._sub_follow=Mock()
        with patch('subscription_tasks.SubscriptionStore',return_value=self.store):self.value.subscribe()
        self.value._sub_candidate.assert_called_once()
        self.value._sub_follow.assert_not_called()
        self.assertEqual(self.store.progress()['current_round'],2)
        self.assertEqual(self.store.stats()['accounts'],1)

    def test_intent_saved_when_click_or_network_fails(self):
        value=self.value
        records=[dict(text='强国号',box=[245,65,80,32]),dict(text='地方平台',box=[365,68,105,30]),
                 dict(text='新频道',box=[215,144,80,25]),dict(text='订阅',box=[640,145,45,25])]
        value._sub_records=Mock(return_value=records)
        value._sub_click=Mock(side_effect=TaskIncomplete('network'))
        with self.assertRaises(TaskIncomplete):
            value._sub_follow(dict(name='新频道',kind='强国号'),self.store,dict(value=0,maximum=2))
        self.assertEqual(self.store.pending()[0]['name'],'新频道')
        self.assertEqual(self.store.pending()[0]['completed_count'],0)

    def test_unrelated_nonempty_follows_are_preserved(self):
        value=self.value
        self.store.begin('本机新增')
        records=[dict(text='我的订阅',box=[307,55,103,30]),dict(text='添加',box=[641,53,58,34]),
                 dict(text='用户原有关注',box=[215,180,150,25])]
        del value._sub_cleanup
        value._sub_mine=Mock(return_value=records);value._sub_records=Mock(return_value=records)
        value._sub_back=Mock();value._sleep=Mock();value.tasker=Mock();value._sub_click=Mock()
        value._sub_cleanup(self.store)
        value._sub_click.assert_not_called()
        self.assertEqual(self.store.pending(),[])
        self.assertEqual(self.store.stats()['completed_operations'],0)

    def test_duplicate_names_are_not_cancelled(self):
        value=self.value;self.store.begin('同名')
        records=[dict(text='我的订阅',box=[307,55,103,30]),dict(text='添加',box=[641,53,58,34]),
                 dict(text='同名',box=[215,180,150,25]),dict(text='同名',box=[215,380,150,25])]
        del value._sub_cleanup
        value._sub_mine=Mock(return_value=records);value._sub_records=Mock(return_value=records)
        value._sub_back=Mock();value._sleep=Mock();value._sub_click=Mock()
        with self.assertRaises(TaskIncomplete):value._sub_cleanup(self.store)
        value._sub_click.assert_not_called()

    def test_full_score_with_pending_cleanup_is_dispatched_by_main_worker(self):
        from test_optimization import worker
        value=worker();value._finish_existing_quiz=Mock();value.subscribe=Mock(return_value=dict(value=2,maximum=2))
        value.read_learning_score=Mock(return_value={'订阅':dict(value=2,maximum=2)})
        self.store.begin('中断遗留')
        with patch('utils.SubscriptionStore',return_value=self.store),patch('utils.read_report',return_value={}):
            value.task(['订阅'])
        value.subscribe.assert_called_once()
        value.read_learning_score.assert_called_once_with(['订阅'])
        self.assertEqual(value.run_report.snapshot()['tasks']['订阅']['state'],'verified')

    def test_empty_my_list_clears_failed_intent_without_incrementing_count(self):
        value=self.value;self.store.begin('未成功关注')
        records=[dict(text='我的订阅',box=[307,55,103,30]),dict(text='添加',box=[641,53,58,34]),
                 dict(text='暂无更多内容',box=[295,683,129,27])]
        del value._sub_cleanup
        value._sub_mine=Mock(return_value=records);value._sub_records=Mock(return_value=records)
        value._sub_back=Mock();value._sleep=Mock();value._sub_click=Mock();value._sub_evidence=Mock()
        value._sub_cleanup(self.store)
        self.assertEqual(self.store.stats()['completed_operations'],0)
        self.assertEqual(self.store.pending(),[])
        value._sub_click.assert_not_called()

    def test_live_verification_keeps_pending_follow_and_never_navigates_away(self):
        self.store.begin('待处理')
        self.value._sub_score=Mock(return_value=dict(value=0,maximum=2))
        self.value._sub_candidate=Mock(return_value=dict(name='candidate'))
        self.value._sub_follow=Mock(side_effect=HumanRequired('访问验证'))
        self.value._screen_records=Mock(return_value=[dict(text='访问验证',box=[303,134,113,31])])
        # Initial pending cleanup is separately mocked; retain it to test interruption.
        self.value._sub_cleanup=Mock()
        with patch('subscription_tasks.SubscriptionStore',return_value=self.store):
            with self.assertRaises(HumanRequired):self.value.subscribe()
        self.assertEqual(self.value._sub_cleanup.call_count,1)
        self.assertEqual(len(self.store.pending()),1)
        self.value._sub_candidate.assert_called_once()

    def test_expired_verification_cleans_pending_once_then_ends_without_retrying(self):
        self.value._sub_score=Mock(return_value=dict(value=0,maximum=2))
        self.value._sub_candidate=Mock(return_value=dict(name='candidate'))
        def interrupted(*args):
            self.store.begin('未成功关注')
            raise HumanRequired('验证退出但未加分')
        self.value._sub_follow=Mock(side_effect=interrupted)
        self.value._screen_records=Mock(return_value=[])
        self.value._sub_cleanup=Mock(side_effect=lambda store:[store.cleaned(r['id']) for r in store.pending()])
        with patch('subscription_tasks.SubscriptionStore',return_value=self.store):
            with self.assertRaises(HumanRequired):self.value.subscribe()
        self.value._sub_candidate.assert_called_once()
        self.value._sub_cleanup.assert_called_once()
        self.assertEqual(self.store.pending(),[])
        self.assertEqual(self.store.stats()['completed_operations'],0)

    def test_owner_wait_does_not_consume_the_short_page_transition_timeout(self):
        clock=[0.0];calls=[]
        def records():
            calls.append(1)
            if len(calls)==1:
                clock[0]=90.0;self.value._paused_seconds=90.0
                return [dict(ready=False)]
            return [dict(ready=True)]
        self.value._sub_records=Mock(side_effect=records)
        self.value._sleep=Mock(side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds))
        with patch('subscription_tasks.time.monotonic',side_effect=lambda:clock[0]):
            self.assertTrue(self.value._sub_wait(lambda rows:rows[0]['ready'],timeout=1)[0]['ready'])
        self.assertEqual(len(calls),2)

    def test_saved_follow_with_no_points_can_continue_after_owner_verification(self):
        records=[dict(text='强国号',box=[245,65,80,32]),dict(text='地方平台',box=[365,68,105,30]),
                 dict(text='频道',box=[215,144,80,25]),dict(text='订阅',box=[640,145,45,25])]
        self.value._sub_records=Mock(return_value=records)
        self.value._sub_click=Mock(side_effect=lambda row:setattr(self.value,'_sub_verification_seen',True))
        self.value._sub_score=Mock(return_value=dict(value=0,maximum=2))
        self.value._sub_evidence=Mock();self.value._sub_follow_saved=Mock(return_value=True)
        after=self.value._sub_follow(dict(name='频道',kind='强国号'),self.store,dict(value=0,maximum=2))
        self.assertEqual(after['value'],0)
        self.assertEqual(len(self.store.pending()),1)
        self.assertEqual(self.store.stats()['completed_operations'],0)

    def test_failed_follow_after_verification_stops_instead_of_trying_more_accounts(self):
        records=[dict(text='强国号',box=[245,65,80,32]),dict(text='地方平台',box=[365,68,105,30]),
                 dict(text='频道',box=[215,144,80,25]),dict(text='订阅',box=[640,145,45,25])]
        self.value._sub_records=Mock(return_value=records)
        self.value._sub_click=Mock(side_effect=lambda row:setattr(self.value,'_sub_verification_seen',True))
        self.value._sub_score=Mock(return_value=dict(value=0,maximum=2))
        self.value._sub_evidence=Mock();self.value._sub_follow_saved=Mock(return_value=False)
        with self.assertRaises(HumanRequired):
            self.value._sub_follow(dict(name='频道',kind='强国号'),self.store,dict(value=0,maximum=2))

    def test_my_subscription_list_resumes_after_verification_without_browser_continue(self):
        from test_optimization import worker
        value=worker()
        value._screen_records=Mock(return_value=json.loads((FIXTURES/'mine-populated.json').read_text('utf-8')))
        value._captcha_present=Mock(return_value=False)
        self.assertFalse(value._owner_resume_ready())
        self.assertTrue(value._owner_resume_ready())

    def test_merged_ai_and_my_home_header_uses_isolated_my_recognition(self):
        from test_optimization import worker
        value=worker()
        records=[dict(text='推荐',box=[20,124,59,32]),dict(text='要闻',box=[105,126,55,28]),
                 dict(text='思想',box=[191,125,54,29]),dict(text='|A|我的',box=[556,58,146,27])]
        value._sub_wait=Mock(return_value=[dict(text='学习积分'),dict(text='我要答题')])
        value._sub_open_profile(records)
        name,override=value.tasker.post_task.call_args.args
        self.assertEqual(override[name]['roi'],[642,35,78,80])
        self.assertEqual(override[name]['expected'],['^我的$'])
        self.assertEqual(override[name]['action'],'Click')
        value.tasker.controller.post_click.assert_not_called()

    def test_category_waits_in_its_own_rail_when_full_screen_text_is_not_ready(self):
        from test_optimization import worker
        value=worker()
        records=[dict(text='强国号',box=[245,65,80,32]),dict(text='地方平台',box=[365,68,105,30])]
        value._sub_wait=Mock(return_value=records);value._sleep=Mock()
        value._sub_select_category(records,'待订阅')
        name,override=value.tasker.post_task.call_args.args
        self.assertEqual(override[name]['roi'],[0,120,140,1060])
        self.assertEqual(override[name]['expected'],['^待订阅$'])
        self.assertEqual(override[name]['timeout'],8000)

    def test_cancel_uses_right_hand_list_button_and_counts_after_reload(self):
        value=self.value;self.store.begin('本机新增')
        rows=[dict(text='我的订阅',box=[307,55,103,30]),dict(text='添加',box=[641,53,58,34])]
        button=dict(text='已订阅',box=[622,223,75,26])
        cancel=dict(text='取消订阅',box=[280,1090,160,35])
        del value._sub_cleanup
        value._sub_mine=Mock(return_value=rows);value._sub_records=Mock(return_value=rows)
        value._sub_back=Mock();value._sleep=Mock();value._sub_click=Mock();value._sub_evidence=Mock()
        value._sub_find_owned=Mock(side_effect=[button,None])
        value._sub_confirm_removed=Mock()
        value._sub_wait=Mock(side_effect=[[cancel],rows])
        value._sub_cleanup(self.store)
        self.assertEqual(value._sub_click.call_args_list[0].args[0],button)
        self.assertEqual(value._sub_click.call_args_list[1].args[0],cancel)
        self.assertEqual(self.store.stats()['completed_operations'],1)
        self.assertEqual(self.store.pending(),[])
        value._sub_confirm_removed.assert_called_once_with({'本机新增'})

    def test_batch_cancels_top_visible_owned_row_without_entering_or_leaving_per_item(self):
        value=self.value;self.store.begin('乙');self.store.begin('甲')
        base=[dict(text='我的订阅',box=[307,55,103,30]),dict(text='添加',box=[641,53,58,34])]
        first=dict(text='已订阅',box=[622,223,75,26]);second=dict(text='已订阅',box=[622,347,75,26])
        both=base+[dict(text='甲',box=[105,201,100,30]),first,
                   dict(text='乙',box=[105,313,100,30]),second]
        one=base+[dict(text='乙',box=[105,201,100,30]),first]
        cancel=dict(text='取消订阅',box=[307,1133,103,31])
        del value._sub_cleanup
        value._sub_mine=Mock();value._sub_back=Mock();value._sleep=Mock()
        value._sub_records=Mock(side_effect=[both,one]);value._sub_click=Mock();value._sub_evidence=Mock()
        value._sub_wait=Mock(side_effect=[[cancel],one,[cancel],base])
        value._sub_find_owned=Mock();value._sub_confirm_removed=Mock()
        value._sub_cleanup(self.store)
        self.assertEqual([call.args[0] for call in value._sub_click.call_args_list],[first,cancel,first,cancel])
        value._sub_back.assert_called_once()
        self.assertEqual(value._sub_mine.call_count,2)
        value._sub_find_owned.assert_not_called()
        value._sub_confirm_removed.assert_called_once_with({'甲','乙'})
        self.assertEqual(self.store.stats()['completed_operations'],2)

    def test_batch_keeps_counts_pending_if_final_list_still_contains_a_removed_account(self):
        value=self.value;self.store.begin('本机新增')
        base=[dict(text='我的订阅',box=[307,55,103,30]),dict(text='添加',box=[641,53,58,34])]
        button=dict(text='已订阅',box=[622,223,75,26]);cancel=dict(text='取消订阅',box=[307,1133,103,31])
        rows=base+[dict(text='本机新增',box=[105,201,150,30]),button]
        del value._sub_cleanup
        value._sub_mine=Mock();value._sub_back=Mock();value._sleep=Mock()
        value._sub_records=Mock(return_value=rows);value._sub_click=Mock();value._sub_evidence=Mock()
        value._sub_wait=Mock(side_effect=[[cancel],base])
        with self.assertRaises(TaskIncomplete):value._sub_cleanup(self.store)
        self.assertEqual(self.store.stats()['completed_operations'],0)
        self.assertEqual(len(self.store.pending()),1)

    def test_failed_cancel_keeps_pending_and_does_not_increment(self):
        value=self.value;self.store.begin('本机新增')
        del value._sub_cleanup
        value._sub_mine=Mock();value._sub_records=Mock(return_value=[]);value._sub_back=Mock();value._sleep=Mock()
        value._sub_find_owned=Mock(return_value=dict(text='已订阅',box=[622,223,75,26]))
        value._sub_click=Mock();value._sub_wait=Mock(side_effect=TaskIncomplete('menu missing'))
        with self.assertRaises(TaskIncomplete):value._sub_cleanup(self.store)
        self.assertEqual(self.store.stats()['completed_operations'],0)
        self.assertEqual(len(self.store.pending()),1)
