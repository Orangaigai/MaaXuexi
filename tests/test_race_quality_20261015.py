import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
from PIL import Image
import test_four_player
from test_optimization import worker
from four_player import race_snapshot

FIXTURES=Path(__file__).parent/'fixtures/race-quality'
def frame(number):
    path=FIXTURES/f'question-{number:02d}.json'
    return json.loads(path.read_text('utf-8')),np.array(Image.open(path.with_suffix('.png')))[:,:,::-1]

class RaceQualityTests(unittest.TestCase):
    def test_actual_question_body_excludes_players_and_multiline_source(self):
        rows,image=frame(3);snap=race_snapshot(rows,image,'四人赛')
        self.assertTrue(snap['question'].startswith('2.'))
        for noise in ('程锦','建辉','宇轩','参见','人民邮电','出版社2016'):
            self.assertNotIn(noise,snap['question'])
        self.assertEqual(len(snap['options']),2)

    def test_question_card_before_options_is_never_an_answer_set(self):
        for number in (2,4):
            rows,image=frame(number)
            self.assertIsNone(race_snapshot(rows,image,'四人赛'))

    def test_score_and_player_ocr_changes_do_not_expire_same_question_answer(self):
        rows,image=frame(1);changed=copy.deepcopy(rows)
        for row in changed:
            if row['box'][1]<350:row['text']='其他玩家与分数变化'
        before=race_snapshot(rows,image,'四人赛');after=race_snapshot(changed,image,'四人赛')
        self.assertEqual(before['key'],after['key'])
        self.assertEqual(before['question_id'],after['question_id'])

    def test_real_eleven_requests_reduce_to_seven_question_keys(self):
        snaps=[race_snapshot(*frame(n),'四人赛') for n in range(1,12)]
        self.assertEqual(sum(s is not None for s in snaps),9)
        self.assertEqual(len({s['key'] for s in snaps if s}),7)
        self.assertEqual(snaps[4]['key'],snaps[5]['key'])
        self.assertEqual(snaps[8]['key'],snaps[9]['key'])

    def test_same_stem_at_new_number_is_a_new_question(self):
        rows,image=frame(1);changed=copy.deepcopy(rows)
        next(r for r in changed if r['text'].startswith('1.'))['text']='2.'+next(r for r in rows if r['text'].startswith('1.'))['text'][2:]
        self.assertNotEqual(race_snapshot(rows,image,'四人赛')['question_id'],race_snapshot(changed,image,'四人赛')['question_id'])

    def test_live_handler_does_not_resolve_again_when_scoreboard_changes(self):
        rows,image=frame(1);changed=copy.deepcopy(rows)
        for row in changed:
            if row['box'][1]<350:row['text']='比分动画'
        value=test_four_player.FourPlayerTests().race_worker([rows]*4+[changed]*3+[test_four_player.RESULT])
        value.tasker.controller.cached_image=image
        with patch('four_player.ThreadPoolExecutor',test_four_player.ImmediateExecutor):
            value.four_player_answer(test_four_player.ROOM)
        self.assertEqual(value._fun_metrics['questions_clicked'],1)
        self.assertEqual(value.ai_resolver.resolve_race_choice.call_count,1)

    def test_future_answer_audit_records_index_and_timing_without_auth_headers(self):
        value=worker()
        with tempfile.TemporaryDirectory() as folder:
            value._race_evidence_dir=Path(folder)
            value.ai_resolver=SimpleNamespace(last_http_status=200,last_error=None,
                session=SimpleNamespace(headers={'Authorization':'Bearer PRIVATE-TEST-KEY'}))
            value._race_answer_audit(dict(key='old',question='测试题面',options=['甲','乙'],request_number=1),
                dict(key='new'),1,.734,'discarded')
            text=(Path(folder)/'answer-01.json').read_text('utf-8');audit=json.loads(text)
            self.assertEqual(audit['decision'],'discarded')
            self.assertEqual(audit['answer_index'],1)
            self.assertFalse(audit['correctness_confirmed'])
            self.assertNotIn('PRIVATE-TEST-KEY',text)

    def test_actual_network_then_timeout_is_confirmed_without_matching_or_ai(self):
        popup=[dict(text='网络开小差，请稍后再试',box=[129,603,267,25]),dict(text='确定',box=[532,677,49,30])]
        timeout=[dict(text='匹配超时！',box=[130,602,110,26]),dict(text='确定',box=[531,677,50,29])]
        profile=[dict(text='我要答题',box=[314,464,89,26]),dict(text='学习积分',box=[97,463,89,26])]
        value=worker();value._sleep=Mock();value._race_save=Mock()
        value._screen_records=Mock(side_effect=[popup,timeout,profile])
        self.assertEqual(value._fun_records(),profile)
        self.assertEqual(value.tasker.controller.post_click.call_count,2)
        value.tasker.post_task.assert_not_called()
