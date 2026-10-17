"""Progress-based articles, strict click hints, complete OCR frames and visual routing."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from io import BytesIO
from base64 import b64decode
import httpx
import numpy as np
from PIL import Image

from test_optimization import worker,job
from daily_hints import exact_hint_parts
from screen_reading import content_roi,read_screen
from question_visual import visual_evidence,visual_identity
from answer_client import AIResolver
from four_player import race_snapshot
from test_four_player import question
import test_four_player


class ArticleProgressTests(unittest.TestCase):
    def harness(self,scores):
        value=worker();value._read_article_batch=Mock();value._reset_home=Mock()
        value.read_learning_score=Mock(side_effect=[{'选读文章':dict(value=s,maximum=12)} for s in scores])
        return value

    def test_full_after_first_batch_does_not_start_another_or_recheck(self):
        value=self.harness([12])
        self.assertEqual(value.read_article(dict(value=0,maximum=12))['value'],12)
        value._read_article_batch.assert_called_once_with(6)
        value.read_learning_score.assert_called_once_with(['选读文章'])
        value._reset_home.assert_not_called()

    def test_one_missing_point_starts_one_article_instead_of_full_session(self):
        value=self.harness([12])
        value.read_article(dict(value=11,maximum=12))
        value._read_article_batch.assert_called_once_with(1)

    def test_partial_batches_continue_from_actual_score_and_remain_bounded(self):
        value=self.harness([8,10,12])
        self.assertEqual(value.read_article(dict(value=0,maximum=12))['value'],12)
        self.assertEqual([c.args[0] for c in value._read_article_batch.call_args_list],[6,2,2])
        self.assertEqual(value._reset_home.call_count,2)
        value=self.harness([0,0,0])
        self.assertEqual(value.read_article(dict(value=0,maximum=12))['value'],0)
        self.assertEqual(value._read_article_batch.call_count,3)

    def test_missing_score_does_not_blindly_repeat_reading(self):
        value=self.harness([]);value.read_learning_score.side_effect=None
        value.read_learning_score.return_value={}
        with self.assertRaisesRegex(RuntimeError,'未能读取实际积分'):
            value.read_article(dict(value=11,maximum=12))
        self.assertEqual(value._read_article_batch.call_count,1)

    def test_one_confirmed_article_has_a_minute_and_stops_before_next_card(self):
        value=worker();value._article_seen=set();value._sleep=Mock()
        value.detect=Mock(return_value=([[40,300,600,240]],['article']))
        value._has_unread_text=Mock(return_value=True)
        value._article_card_text=Mock(return_value='一篇新文章的标题')
        value.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=np.zeros((1280,720,3),np.uint8)
        with patch('content_tasks.randint',side_effect=lambda a,b:a):value._read_article_batch(1)
        self.assertEqual(value._article_seen,{'一篇新文章的标题'})
        self.assertEqual(sum(c.args[0] for c in value._sleep.call_args_list if c.args[0]>=20),60)
        self.assertEqual(value.tasker.controller.post_click.call_count,1)
        self.assertEqual(value.tasker.controller.post_swipe.call_count,3)


class ClickHintTests(unittest.TestCase):
    def test_real_four_character_layout_is_ordered_by_hint_not_button_position(self):
        self.assertEqual(exact_hint_parts(['黎平会议'],['议','黎','平','会']),['黎','平','会','议'])
        self.assertEqual(exact_hint_parts(['黎平','会议'],['议','黎','平','会']),['黎','平','会','议'])
        self.assertEqual(exact_hint_parts(['5-3'],['5+3','5-3']),['5-3'])

    def test_ambiguous_incomplete_duplicate_or_reused_button_is_not_guessed(self):
        for hints,options in [(['学习'],['学习','学','习']),(['甲乙'],['甲']),
                              (['甲甲'],['甲']),(['甲'],['甲','甲。']),([],['甲'])]:
            with self.subTest(hints=hints,options=options):self.assertIsNone(exact_hint_parts(hints,options))

    def test_default_mode_reads_click_hint_and_uses_strict_click_list(self):
        value=worker();value._sleep=Mock();value._get_red_texts=Mock(return_value=['黎平会议'])
        value._scan_click_options=Mock(return_value={s:[20,400,50,50] for s in ['议','黎','平','会']})
        value.ai_resolver=SimpleNamespace(resolve_click_blank=Mock())
        value._click_text_answers=Mock(return_value=True);value._fast_click_blanks=Mock()
        value.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=np.zeros((1280,720,3),np.uint8)
        images,options,hints,count=value._prepare('点选填空题')
        value._get_red_texts.assert_called_once()
        answer,matched=value._determine_answer('点选填空题',options,hints,images,count)
        self.assertEqual(answer,['黎','平','会','议']);self.assertTrue(matched)
        self.assertTrue(value._submit_answer('点选填空题',answer,matched))
        value._click_text_answers.assert_called_once_with(answer)
        value.ai_resolver.resolve_click_blank.assert_not_called();value._fast_click_blanks.assert_not_called()

    def test_ambiguous_hint_uses_model_even_if_legacy_fast_switch_is_on(self):
        value=worker();value.fast_answer=True
        value._scan_click_options=Mock(return_value={s:[] for s in ['学习','学','习']})
        value._fast_try_answer=Mock(return_value='错答案')
        value.ai_resolver=SimpleNamespace(resolve_click_blank=Mock(return_value=['学习']))
        self.assertEqual(value._determine_answer('点选填空题',{},['学习'],[np.zeros((10,10,3),np.uint8)]),(['学习'],False))
        value._fast_try_answer.assert_not_called();value.ai_resolver.resolve_click_blank.assert_called_once()


class CompleteScreenTests(unittest.TestCase):
    def screen_worker(self,frames):
        value=worker()
        value.tasker.controller.post_screencap.return_value.wait.return_value.get.side_effect=frames
        detail=job().wait().get()
        detail.nodes[0].recognition.all_results=[SimpleNamespace(text='完整题干',box=[0,80,200,30])]
        value.tasker.post_recognition=Mock(return_value=job())
        value.tasker.post_recognition.return_value.wait.return_value.get.return_value=detail
        return value

    def test_crop_preserves_text_at_all_four_edges_and_bottom_of_long_question(self):
        image=np.full((1280,720,3),255,np.uint8)
        image[36:55,0:20]=0;image[1220:1239,700:720]=0
        x,y,w,h=content_roi(image)
        self.assertEqual((x,w),(0,720));self.assertLessEqual(y,36);self.assertGreaterEqual(y+h,1239)

    def test_identical_new_capture_reuses_records_but_changed_pixel_forces_ocr(self):
        image=np.full((1280,720,3),255,np.uint8);image[100:120,50:90]=0
        changed=image.copy();changed[800,400]=0
        value=self.screen_worker([image,image.copy(),changed])
        records=read_screen(value);records[0]['text']='外部修改'
        self.assertEqual(read_screen(value)[0]['text'],'完整题干')
        self.assertTrue(value._last_screen_ocr_cached)
        read_screen(value)
        self.assertEqual(value.tasker.post_recognition.call_count,2)
        self.assertEqual(value.tasker.controller.post_screencap.call_count,3)
        value.tasker.post_task.assert_not_called()

    def test_trimmed_ocr_miss_retries_full_app_on_same_image(self):
        image=np.full((1280,720,3),255,np.uint8);image[400:440,200:400]=0
        value=self.screen_worker([image])
        good=value.tasker.post_recognition.return_value.wait.return_value.get.return_value
        value.tasker.post_recognition.return_value.wait.return_value.get.side_effect=[job(False).wait().get(),good]
        self.assertTrue(read_screen(value))
        self.assertEqual(value.tasker.post_recognition.call_count,2)
        self.assertEqual(value.tasker.post_recognition.call_args.args[1].roi,[0,35,720,1205])
        self.assertEqual(value.tasker.controller.post_screencap.call_count,1)

    def test_nonportrait_layout_keeps_complete_width_and_content(self):
        image=np.full((720,1280,3),255,np.uint8);image[1:20,:20]=0;image[700:719,1260:]=0
        self.assertEqual(content_roi(image),[0,0,1280,720])


class VisualRoutingTests(unittest.TestCase):
    def image_question(self):
        records=question('请选择正确的一项内容？')
        image=np.full((1280,720,3),255,np.uint8)
        for row in records:
            x,y,w,h=row['box'];image[y:y+h,x:x+w]=0
        options=race_snapshot(records,image)['options']
        return records,image,options

    def test_recognized_text_does_not_need_visual_but_unlabelled_diagram_does(self):
        records,image,options=self.image_question()
        self.assertFalse(visual_evidence(image,records,options)[0])
        image[350:440,280:283]=0;image[437:440,280:450]=0
        self.assertTrue(visual_evidence(image,records,options)[0])

    def test_photo_without_picture_keyword_and_unknown_geometry_use_vision(self):
        records,image,options=self.image_question()
        image[350:440,280:450]=np.random.default_rng(12).integers(0,255,(90,170,3),dtype=np.uint8)
        self.assertTrue(visual_evidence(image,records,options)[0])
        self.assertTrue(visual_evidence(None,records,options)[0])

    def test_visual_above_first_recognized_question_line_is_retained(self):
        records,image,options=self.image_question()
        image[170:230,280:450]=np.random.default_rng(11).integers(0,255,(60,170,3),dtype=np.uint8)
        needed,area=visual_evidence(image,records,options)
        self.assertTrue(needed);self.assertLessEqual(area[1],170)

    def test_changed_diagram_invalidates_answer_even_with_identical_ocr_text(self):
        records,image,options=self.image_question()
        image[350:440,280:283]=0;image[437:440,280:450]=0
        before=race_snapshot(records,image)
        changed=image.copy();changed[360:400,400:410]=0
        after=race_snapshot(records,changed)
        self.assertEqual(before['key'],after['key'])
        self.assertEqual(before['question_id'],after['question_id'])
        self.assertNotEqual(before['visual_key'],after['visual_key'])

    def test_race_handler_discards_model_answer_when_only_diagram_changes(self):
        records,image,options=self.image_question()
        image[350:440,280:283]=0;image[437:440,280:450]=0
        changed=image.copy();changed[360:400,400:410]=0
        value=test_four_player.FourPlayerTests().race_worker([]);calls=[0]
        def read():
            calls[0]+=1
            value.tasker.controller.cached_image=image if calls[0]<3 else changed
            return records if calls[0]<=3 else test_four_player.RESULT
        value._fun_records.side_effect=read
        with patch('four_player.ThreadPoolExecutor',test_four_player.ImmediateExecutor):
            value.four_player_answer(test_four_player.ROOM)
        self.assertEqual(value._fun_metrics['discarded_answers'],1)
        value.tasker.controller.post_click.assert_not_called()

    def test_visual_flag_sends_complete_frame_with_unchanged_options(self):
        resolver=AIResolver('offline','deepseek-flash');resolver.session.close();sent=[]
        def transport(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"index":1,"uncertain":false}'}}]})
        resolver.session=httpx.Client(transport=httpx.MockTransport(transport));self.addCleanup(resolver.session.close)
        image=np.full((1280,720,3),255,np.uint8);image[100,100]=[0,0,255]
        for method in (resolver.resolve_fun_choice,resolver.resolve_race_choice):
            self.assertEqual(method([image],['甲','乙'],'请选择正确的一项内容？',visual_required=True),1)
        for payload in sent:
            content=payload['messages'][1]['content']
            encoded=next(p['image_url']['url'] for p in content if p['type']=='image_url')
            decoded=Image.open(BytesIO(b64decode(encoded.split(',',1)[1])))
            self.assertEqual(decoded.size,(720,1280))
            self.assertEqual(json.loads(content[0]['text'])['options'],{'0':'甲','1':'乙'})

    def test_required_image_missing_does_not_send_a_text_only_guess(self):
        resolver=AIResolver('offline','deepseek-flash');resolver.session.close();resolver._resolve=Mock()
        self.assertIsNone(resolver.resolve_fun_choice([],['甲','乙'],'请选择正确的一项内容？',visual_required=True))
        self.assertEqual(resolver.last_error,'missing_visual_evidence');resolver._resolve.assert_not_called()


if __name__=='__main__':unittest.main()
