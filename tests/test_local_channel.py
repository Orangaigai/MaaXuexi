"""Current UI fixtures, local score attribution and permission/connection handling."""
import json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import task_support
from local_channel import LocalChannelTasks,local_home_entry,local_platform_entry
from task_support import TaskIncomplete,parse_score_rows
from test_optimization import worker

FIXTURES=Path(__file__).parent/'fixtures'/'local-channel'
def load(name):return json.loads((FIXTURES/name).read_text('utf-8'))

class LocalChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        patcher=patch.object(task_support,'REPORT_PATH',Path(self.tmp.name)/'run.json')
        patcher.start();self.addCleanup(patcher.stop)

    def test_real_home_selects_current_region_and_other_regions_need_no_fixed_index(self):
        records=load('home.json')
        self.assertEqual(local_home_entry(records)['text'],'北京')
        other=[dict(r,text='湖北') if r['text']=='北京' else r for r in records]
        self.assertEqual(local_home_entry(other)['text'],'湖北')
        other.append(dict(text='上海',box=[580,125,55,30]))
        self.assertIsNone(local_home_entry(other))

    def test_real_location_explanation_does_not_authorize_clicking_the_platform_behind_it(self):
        records=load('location-intro.json')
        self.assertIsNone(local_platform_entry(records))
        visible=[r for r in records if r['text']!='权限申请目的说明']
        self.assertEqual(local_platform_entry(visible)['text'],'北京学习平台')
        visible.append(dict(text='访问验证',box=[300,134,110,31]))
        self.assertIsNone(local_platform_entry(visible))

    def test_local_score_is_separate_from_neighboring_comment_and_weekly_exercise(self):
        scores=parse_score_rows(load('score-zero.json'))
        self.assertEqual(scores['本地频道'],dict(value=0,maximum=1,evidence='score_fraction'))
        self.assertEqual(scores['订阅']['value'],2)
        self.assertNotIn('强国运动',scores)

    def test_merged_neighbor_icon_captions_are_read_in_the_first_icons_own_region(self):
        records=load('merged-platform.json')
        self.assertIsNone(local_platform_entry(records))
        value=worker()
        value.tasker.post_task.return_value.wait.return_value.get.return_value=SimpleNamespace(
            status=SimpleNamespace(failed=False),nodes=[SimpleNamespace(recognition=SimpleNamespace(
                all_results=[SimpleNamespace(text='北京学习平台',box=[20,353,116,29])]))])
        self.assertEqual(value._local_platform_target(records)['text'],'北京学习平台')
        task,override=value.tasker.post_task.call_args.args
        self.assertEqual(override[task]['roi'],[0,330,145,75])
        value.tasker.controller.post_click.assert_not_called()

    def test_already_full_local_task_never_opens_or_calls_model(self):
        value=worker();value._local_clear_location_prompt=Mock();value._local_task_entry=Mock()
        value.read_learning_score=Mock()
        self.assertEqual(value.learn_local_channel(dict(value=1,maximum=1)),dict(value=1,maximum=1))
        value._local_task_entry.assert_not_called();value.read_learning_score.assert_not_called()

    def test_real_after_score_is_used_instead_of_assuming_opening_awards_one_point(self):
        value=worker();landing=[r for r in load('location-intro.json') if r['text']!='权限申请目的说明']
        value._local_clear_location_prompt=Mock(side_effect=[[],landing,[]])
        value._local_task_entry=Mock();value._local_click=Mock();value._sleep=Mock();value._local_evidence=Mock()
        value._screen_records=Mock(return_value=[]);value.read_learning_score=Mock(return_value={'本地频道':dict(value=0,maximum=1)})
        after=value.learn_local_channel(dict(value=0,maximum=1))
        self.assertEqual(after['value'],0)
        value.read_learning_score.assert_called_once_with(['本地频道'])
        self.assertTrue(value._score_refresh_required)

    def test_rule_change_stops_before_any_new_local_navigation(self):
        value=worker();value._local_clear_location_prompt=Mock();value._local_task_entry=Mock()
        with self.assertRaises(TaskIncomplete):value.learn_local_channel(dict(value=0,maximum=2))
        value._local_task_entry.assert_not_called()

    def test_multiline_location_request_denies_only_a_confirmed_android_permission_dialog(self):
        value=worker();value._adb_command=('adb','-s','offline-fixture');value._sleep=Mock()
        deny=dict(text='不允许',box=[280,850,130,40])
        records=[dict(text='要允许“学习强国”获取此设备的',box=[120,450,480,30]),
                 dict(text='位置信息吗？',box=[250,490,220,30]),deny]
        value._screen_records=Mock(side_effect=[records,[]])
        with patch('local_channel.subprocess.run',return_value=SimpleNamespace(
            stdout='topResumedActivity=com.android.permissioncontroller/.GrantPermissionsActivity')):
            self.assertEqual(value._local_clear_location_prompt(),[])
        value.tasker.controller.post_click.assert_called_once_with(345,870)

    def test_lookalike_permission_page_is_not_clicked(self):
        value=worker();value._adb_command=('adb','-s','offline-fixture')
        value._screen_records=Mock(return_value=[dict(text='学习强国位置信息',box=[120,450,400,30]),
                                               dict(text='不允许',box=[280,850,130,40])])
        with patch('local_channel.subprocess.run',return_value=SimpleNamespace(stdout='topResumedActivity=other.app/.Activity')):
            with self.assertRaises(TaskIncomplete):value._local_clear_location_prompt()
        value.tasker.controller.post_click.assert_not_called()

    def test_main_worker_dispatches_local_task_and_reuses_its_verified_score(self):
        value=worker();value._local_clear_location_prompt=Mock();value._finish_existing_quiz=Mock()
        value.read_learning_score=Mock(return_value={'本地频道':dict(value=0,maximum=1)})
        value.learn_local_channel=Mock(return_value=dict(value=1,maximum=1))
        with patch('utils.read_report',return_value={}):value.task(['本地频道'])
        value.learn_local_channel.assert_called_once_with(dict(value=0,maximum=1))
        value.read_learning_score.assert_called_once_with(['本地频道'])
        self.assertEqual(value.run_report.snapshot()['status'],'completed')

    def test_disconnect_is_reported_as_device_failure_instead_of_none_attribute_error(self):
        value=worker();value.connected=True
        value.tasker.post_task.return_value.wait.return_value.get.return_value=None
        with self.assertRaisesRegex(TaskIncomplete,'模拟器已断开'):value._screen_records()
        self.assertFalse(value.connected)

    def test_ui_offers_supported_tasks_but_public_comment_is_opt_in(self):
        import ast,re,webui
        root=Path(__file__).resolve().parents[1]
        script=(root/'page/assets/local-provider.js').read_text('utf-8')
        names=ast.literal_eval(re.search(r'const names=(\[[^;]+\]);',script).group(1))
        self.assertEqual(names,list(task_support.ALIASES))
        example=json.loads((root/'config/local-task-plan.example.json').read_text('utf-8'))
        self.assertEqual(example['tasklist'],[name for name in names if name!='发表观点'])
        self.assertEqual(webui.TaskModel(tasklist=['本地频道']).tasklist,['本地频道'])

if __name__=='__main__':unittest.main()
