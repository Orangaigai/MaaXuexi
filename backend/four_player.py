"""Visible double/four-player matching, timed answers and settlement return."""
import hashlib
import json
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image
from task_support import TaskIncomplete,HumanRequired
from question_visual import visual_evidence, visual_identity


def quiz_identity(value):
    value = unicodedata.normalize('NFKC',value)
    # Ignore decorative OCR punctuation, but retain operators and numeric
    # decimal/ratio separators so 5+3, 5-3, 3.14 and 314 stay different.
    value = re.sub(r'(?<!\d)[.:]|[.:](?!\d)','',value)
    return re.sub(r"[^\w+\-−*/×÷=<>≤≥≠%.:°℃]+",'',value)


def detect_fun_mode(records, entered_today=False):
    modes = set()
    for row in records:
        text = row['text'].strip()
        x, y, w, h = row['box']
        if text in ('挑战答题', '四人赛', '双人对战') and 35 <= y < (440 if entered_today else 130):
            modes.add(text)
    if len(modes)==1:
        return next(iter(modes))
    if not modes and entered_today and two_player_room(records):
        return '双人对战'
    return None


def quiz_center(records):
    words = {row['text'].strip() for row in records}
    return {'答题练习', '答题竞赛', '每日答题'} <= words


def quiz_center_today_mode(records):
    """Bind the Today banner to the nearby card, not all center mode names."""
    if not quiz_center(records):
        return None
    markers = [row['box'] for row in records if row['text'].strip() == '今日趣味答题'
               and 650 <= row['box'][1] < 1230]
    if len(markers) != 1:
        return None
    x, y, w, h = markers[0]
    center = x + w/2
    candidates = []
    for row in records:
        mode = row['text'].strip()
        if mode not in ('挑战答题', '四人赛', '双人对战'):
            continue
        rx, ry, rw, rh = row['box']
        if ry >= 650 and -15 <= y-(ry+rh) <= 240 and rx-60 <= center <= rx+rw+60:
            candidates.append(mode)
    return candidates[0] if len(candidates) == 1 else None


def race_finished(records):
    words = ' '.join(row['text'] for row in records)
    if any(re.fullmatch(r'回顾本局|本局排名|比赛结束|本局结束|本场结束|对战结束|本局成绩',r['text'].strip()) for r in records):
        return True
    if (re.search(r'再来一局|继续挑战|返回首页', words) and
            re.search(r'第[一二三四1-4]名|本局得分|本局积分|恭喜获胜|未获胜|获得胜利', words)):
        return True
    # The current loss page says "继续加油！" and "积分+8", not "未获胜".
    # Require the summary, ranking headers and bottom action together so quiz
    # options containing those words cannot trigger an early return.
    clean=[(re.sub(r'\s+','',unicodedata.normalize('NFKC',r['text'])),r['box'][1]) for r in records]
    headers=''.join(text for text,y in clean if 500<=y<850)
    ranking='排名' in headers and ('正确数/总题数' in headers or '总分' in headers)
    summary=any(300<=y<550 and (re.fullmatch(r'继续加油[!。]?|再接再厉[!。]?',text)
                               or re.search(r'积分\+\d+(?:分)?',text)) for text,y in clean)
    replay=any(y>=900 and text in ('继续挑战','再来一局','返回首页') for text,y in clean)
    return bool(ranking and summary and replay)


def race_score_page(records):
    return any(r['text'].strip() in ('积分中心','学习积分','积分明细') and
               35 <= r['box'][1] < 150 for r in records)


def four_player_room(records):
    return (detect_fun_mode(records,True) == '四人赛' and
            sum(r['text'].strip() == '开始比赛' and r['box'][1] >= 1000 for r in records) == 1)


def two_player_room(records):
    words={r['text'].strip() for r in records}
    return {'邀请好友','随机匹配','开始对战'} <= words


def two_player_start_ready(records,image):
    buttons=[r['box'] for r in records if r['text'].strip()=='开始对战' and r['box'][1]>=850]
    if len(buttons)!=1 or image is None or image.shape[:2]!=(1280,720):
        return False
    x,y,w,h=buttons[0]
    pixels=image[y:y+h,x:x+w,:3].astype(np.int16)
    if not pixels.size:
        return False
    white=(pixels.min(axis=2)>215)&(pixels.max(axis=2)-pixels.min(axis=2)<30)
    return int(white.sum())>=max(30,round(w*h*.02))


def two_player_random_target(records,image):
    labels=[r for r in records if r['text'].strip()=='随机匹配' and r['box'][1]>=500]
    if len(labels)!=1 or image is None or image.shape[:2]!=(1280,720):
        return None
    x,y,w,h=labels[0]['box']
    center=round(x+w/2)
    left,right=max(0,center-90),min(720,center+90)
    top,bottom=max(35,y-180),max(35,y-30)
    pixels=image[top:bottom,left:right,:3].astype(np.int16)
    white=(pixels.min(axis=2)>245)&(pixels.max(axis=2)-pixels.min(axis=2)<15)
    ys,xs=np.nonzero(white)
    if len(xs)<3000:
        return None
    box=[int(xs.min())+left,int(ys.min())+top,int(xs.max()-xs.min()+1),int(ys.max()-ys.min()+1)]
    if not (80<=box[2]<=150 and 80<=box[3]<=150):
        return None
    return dict(text='随机匹配',box=box)


def race_network_error(records):
    popup_context = any((r['text'].strip() == '正在匹配中' and r['box'][1] < 300) or
                        (r['text'].strip() in ('确定','重试') and r['box'][1] >= 500)
                        for r in records)
    if popup_context:
        return next((r['text'].strip() for r in records if 350 <= r['box'][1] <= 1000 and
                     re.fullmatch(r'(?:网络开小差|网络异常|网络连接失败)(?:[，,。 ]*请稍后再试)?',r['text'].strip())),None)
    return None


def race_match_expired(records):
    return (any(re.fullmatch(r'(?:游戏已结束|匹配已结束|本局已失效|匹配超时)[！!。]?',r['text'].strip())
                and 350<=r['box'][1]<=1000 for r in records)
            and any(r['text'].strip()=='确定' and r['box'][1]>=500 for r in records))


def unlabeled_race_options(records,image):
    if image is None or image.shape[:2] != (1280,720):
        return []
    crop = image[270:1140,40:680,:3].astype(np.int16)
    low,high = crop.min(axis=2),crop.max(axis=2)
    mask = (low >= 225) & (high-low <= 18)
    rows = np.flatnonzero(mask.sum(axis=1) >= 450)
    groups = np.split(rows,np.flatnonzero(np.diff(rows)>1)+1)
    boxes = []
    for group in groups:
        if len(group) < 35 or not 40 <= group[-1]-group[0]+1 <= 180:
            continue
        x_columns = np.flatnonzero(mask[group[0]:group[-1]+1].mean(axis=0) > 0.65)
        if len(x_columns) < 450:
            continue
        boxes.append([int(x_columns[0])+40,int(group[0])+270,
                      int(x_columns[-1]-x_columns[0]+1),int(group[-1]-group[0]+1)])
    if not 2 <= len(boxes) <= 4:
        return []
    result = []
    for index,(x,y,w,h) in enumerate(boxes):
        lines = sorted((r for r in records if x <= r['box'][0] < x+w and
                        y <= r['box'][1] < y+h),key=lambda r:(r['box'][1],r['box'][0]))
        text = ' '.join(r['text'] for r in lines).strip()
        if not text:
            return []
        result.append(dict(letter='ABCD'[index],box=[x,y,w,h],text=text))
    return result


def race_snapshot(records,image=None,mode=None):
    """Letters anchor clickable options; names/timers never form an option."""
    # White cards occur in the score center too. Require a quiz identity before
    # interpreting any card as a clickable answer.
    if race_score_page(records) or race_network_error(records) or race_match_expired(records) or any(r['text'].strip() in
            ('成长总积分','积分规则','我要答题','答题中心','访问验证','开始比赛','开始对战','随机匹配','邀请好友') for r in records):
        return None
    if mode not in ('四人赛','双人对战') and not any((r['text'].strip() in ('四人赛','双人对战') and 35 <= r['box'][1] < 150) or
               (re.fullmatch(r'第\d+题',r['text'].strip()) and 120 <= r['box'][1] < 300)
               for r in records):
        return None
    if race_finished(records) or any(re.search(r'正确答案|回答正确|回答错误', r['text']) for r in records):
        return None
    choices = []
    for row in records:
        match = re.match(r'^([A-D])(?:[.．、:：\s]|$)', row['text'].strip())
        x, y, w, h = row['box']
        if match and 15 <= x < 190 and 270 <= y < 1135:
            choices.append(dict(letter=match[1], box=list(row['box']), text=''))
    choices.sort(key=lambda item:item['box'][1])
    body_headers=[r for r in records if 350<=r['box'][1]<=550 and 15<=r['box'][0]<190
                  and re.match(r'^\d{1,3}[.．、]\s*\S',r['text'].strip())]
    if len(body_headers)>1:
        return None
    # In this numbered layout the large white question card appears before any
    # answer buttons. It must not be interpreted as an unlabeled answer button.
    if body_headers and not choices:
        return None
    unlabeled = not choices
    if unlabeled:
        choices = unlabeled_race_options(records,image)
    if not 2 <= len(choices) <= 4 or [r['letter'] for r in choices] != list('ABCD'[:len(choices)]):
        return None
    if any(b['box'][1]-a['box'][1] < 45 for a,b in zip(choices,choices[1:])):
        return None
    for index, choice in enumerate(choices):
        if unlabeled:
            continue
        y = choice['box'][1]
        end = choices[index+1]['box'][1]-8 if index+1 < len(choices) else min(1150,y+150)
        lines = sorted((r for r in records if y-8 <= r['box'][1] < end and r['box'][0] >= 15
                        and not re.match(r'^(?:来源|出题|推荐单位)[：:]', r['text'])),
                       key=lambda r:(r['box'][1],r['box'][0]))
        text = ' '.join(r['text'] for r in lines)
        choice['text'] = re.sub(r'^[A-D][.．、:：\s]*','',text).strip()
        if not choice['text']:
            return None
    question_top=body_headers[0]['box'][1]-8 if body_headers else 150
    question_end=choices[0]['box'][1]-15
    source_rows=[r['box'][1] for r in records if question_top<=r['box'][1]<question_end
                 and re.match(r'^(?:来源|出题|推荐单位)[：:]',r['text'].strip())]
    if source_rows:
        question_end=min(question_end,min(source_rows)-12)
    question_rows = sorted((r for r in records if question_top <= r['box'][1] < question_end
                            and 15 <= r['box'][0] < 690 and r['box'][3] >= 18
                            and not re.fullmatch(r'(?:四人赛|双人对战|\d+(?:秒|分)?|第\d+题|\d+/\d+)',r['text'].strip())
                            and not re.match(r'^(?:来源|出题|推荐单位)[：:]',r['text'])),
                           key=lambda r:(r['box'][1]//18,r['box'][0]))
    question = ' '.join(r['text'] for r in question_rows)
    if len(re.sub(r'\W','',question)) < 5:
        return None
    number = next((r['text'] for r in records if r['box'][1] < 300 and
                   re.fullmatch(r'第\d+题|\d+/\d+',r['text'].strip())), '')
    if body_headers:
        number='第'+re.match(r'^(\d+)',body_headers[0]['text'].strip())[1]+'题'
    normalize = quiz_identity
    key = hashlib.sha256('|'.join(normalize(v) for v in
        [number,question]+[r['text'] for r in choices]).encode()).hexdigest()
    visual,area=visual_evidence(image,records,choices,question)
    picture_key=visual_identity(image,area) if visual and area is not None else None
    return dict(key=key, question=question, visual_required=visual,visual_area=area,
                visual_key=picture_key,question_id=number+'|'+normalize(question), options=choices,
                crop=[20, max(120,min(r['box'][1] for r in question_rows)-15),700,
                      min(1200,choices[-1]['box'][1]+150)])


class FourPlayerQuiz:
    def _resolve_race_timed(self,image,options,question,timing,visual_required=False):
        started = time.monotonic()
        try:
            return self.ai_resolver.resolve_race_choice([image],options,question,visual_required=visual_required)
        finally:
            timing.update(model_seconds=time.monotonic()-started,returned_at=time.monotonic())

    def two_player_answer(self,opening_records):
        self._fun_mode='双人对战'
        return self.four_player_answer(opening_records)

    def _retry_failed_matching(self,records,mode):
        if not self._fun_metrics.get('matching_request_failed'):
            return None
        from daily_flow import daily_scene
        known=(detect_fun_mode(records,True) in ('双人对战','四人赛')
               or daily_scene(records) in ('center','profile','score','home'))
        if not known and any(r['text'].strip()=='正在匹配中' for r in records):
            if not self._app_in_foreground():raise TaskIncomplete('失败的匹配页不在前台')
            self.tasker.post_task('返回').wait()
            records=self._fun_records()
            known=(detect_fun_mode(records,True) in ('双人对战','四人赛')
                   or daily_scene(records) in ('center','profile','score','home'))
        if not known:
            return None
        if getattr(self,'_fun_network_retry_count',0)>=1:
            raise HumanRequired('对战已使用一次网络恢复，仍匹配失败，保留现场')
        self._fun_network_retry_count=1
        self._fun_metrics['matching_request_failed']=False
        opening=self._open_fun_challenge(target_mode=mode)
        if detect_fun_mode(opening,True)!=mode:
            raise TaskIncomplete('恢复后的模式不一致，未跨模式匹配')
        target=two_player_random_target(opening,self.tasker.controller.cached_image) if mode=='双人对战' else None
        if mode=='双人对战' and target is None:raise TaskIncomplete('恢复后未定位随机匹配图标')
        if not self._fun_click([target] if target else opening,[r'随机匹配' if mode=='双人对战' else r'开始比赛']):
            raise TaskIncomplete('对战网络恢复请求未成功点击')
        self._fun_metrics['matching_retry_requests']=1
        self._fun_metrics['network_retry_clicks_current_run']=1
        self._fun_metrics['random_match_clicks' if mode=='双人对战' else 'start_button_clicks']+=1
        self._fun_metrics['awaiting_double_start']=(mode=='双人对战')
        self.send_log(mode+'：已从失败页面恢复，网络续试一次')
        return opening

    def _exit_competitive(self,records):
        from daily_flow import daily_scene
        report=getattr(self,'run_report',None)
        if report and '趣味答题' in report.snapshot()['tasks']:
            report.task('趣味答题',completion_pending=True,metrics=self._fun_metrics)
        result_back=False
        lobby_back=False
        exit_confirm_clicked=False
        for _ in range(10):
            exit_confirmation=(self._fun_metrics.get('mode')=='双人对战' and
                any(re.fullmatch(r'确定离开擂台吗[？?]?',r['text'].strip()) and 350<=r['box'][1]<=850 for r in records))
            if exit_confirmation:
                if not self._fun_metrics.get('round_finished'):
                    raise TaskIncomplete('未确认双人结算，未自动退出擂台')
                if not exit_confirm_clicked:
                    if not self._app_in_foreground() or not self._fun_click(records,[r'退出'],min_y=500):
                        raise TaskIncomplete('双人已结算，但未确认唯一退出按钮')
                    exit_confirm_clicked=True
                    self._fun_metrics['double_exit_confirmation_clicks']=1
                    self.send_log('双人对战：已结算，确认退出擂台，返回核分')
                self._sleep(.3)
                records=self._fun_records()
                continue
            scene=daily_scene(records)
            if scene in ('center','profile','score','home'):
                self._fun_metrics.update(exited=True,result_back_clicks=int(result_back))
                if report and '趣味答题' in report.snapshot()['tasks']:
                    report.task('趣味答题',completion_pending=False,metrics=self._fun_metrics)
                self.send_log('对战：已离开结果页，返回后核对实际积分')
                return
            if race_finished(records) and not result_back:
                returns=[r for r in records if r['text'].strip() in ('返回','返回首页','返回答题中心') and r['box'][1]>=500]
                if len(returns)==1:
                    self._fun_click(returns,[r'返回|返回首页|返回答题中心'])
                elif not returns:
                    if not self._app_in_foreground():raise TaskIncomplete('对战结果不在前台')
                    if self.tasker.post_task('返回').wait().get().status.failed:
                        raise TaskIncomplete('对战结果页返回失败')
                else:
                    raise TaskIncomplete('对战结果页返回按钮不唯一')
                result_back=True
                self.send_log('对战：已识别本局结算，返回一次，不再来一局')
            elif (four_player_room(records) or two_player_room(records)) and result_back and not lobby_back:
                self.tasker.post_task('返回').wait()
                lobby_back=True
            self._sleep(.3)
            records=self._fun_records()
        self._race_save('98-result-return-unconfirmed',records)
        raise TaskIncomplete('对战已结算，但返回未确认；保留现场，不重新匹配')

    def _race_guard_network(self, records):
        message = race_network_error(records)
        if message:
            self._fun_metrics['network_error'] = message
            self._race_save('96-network-error',records)
            self.send_log('对战页面报告：'+message+'；保存现场并结束此项，不重复匹配')
            raise TaskIncomplete('对战：'+message+'；未确认比赛，不重复匹配')

    def _race_time(self):
        # Human verification pauses are separate from active matching/play time.
        return time.monotonic() - getattr(self, '_paused_seconds', 0)

    def _race_score_exit(self, records):
        if not race_score_page(records):
            return False
        self._fun_metrics['exited_to_score'] = True
        self._race_save('97-returned-to-score',records)
        self.send_log('对战：当前已回积分页，未确认比赛作答或结算；核对实际积分，不重复匹配')
        return True

    def _race_save(self, tag, records):
        folder = getattr(self, '_race_evidence_dir', None)
        if folder is None:
            folder = Path('logs/fun-race') / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            folder.mkdir(parents=True,exist_ok=True)
            self._race_evidence_dir = folder
            self._fun_metrics['evidence_dir'] = str(folder)
        screen = self.tasker.controller.cached_image
        if screen is not None and screen.shape[:2] == (1280,720):
            Image.fromarray(screen[:,:,::-1]).save(folder / (tag+'.png'))
        (folder / (tag+'.json')).write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')

    def _race_answer_audit(self,pending,current,answer,elapsed,decision):
        folder=getattr(self,'_race_evidence_dir',None)
        if folder is None:
            return
        data=dict(question_key=pending['key'],question=pending['question'],options=pending['options'],
            answer_index=answer,seconds=round(elapsed,3),decision=decision,
            latest_question_key=current['key'] if current else None,
            http_status=getattr(self.ai_resolver,'last_http_status',None),
            error=getattr(self.ai_resolver,'last_error',None),correctness_confirmed=False)
        data.update(model_seconds=round(pending.get('timing',{}).get('model_seconds',elapsed),3),
                    observed_to_decision_seconds=round(time.monotonic()-pending['first_observed'],3) if 'first_observed' in pending else round(elapsed,3))
        (folder/f"answer-{pending['request_number']:02d}.json").write_text(
            json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

    def four_player_answer(self, opening_records):
        metrics = self._fun_metrics
        mode=getattr(self,'_fun_mode','四人赛')
        if mode not in ('四人赛','双人对战'):
            raise TaskIncomplete('对战：未确认双人或四人模式')
        metrics.update(mode=mode, target_correct=None, rounds_started=metrics.get('rounds_started',0),
                       round_finished=False, discarded_answers=0, uncertain_answers=0,
                       post_verification_start_clicks=0,start_button_clicks=0,random_match_clicks=0,
                       awaiting_double_start=(mode=='双人对战'),
                       count_evidence='visible match settlement and separately read score')
        metrics['matching_request_failed']=False
        self._race_evidence_dir = None
        self._race_save('00-room',opening_records)
        if detect_fun_mode(opening_records,True) != mode and not race_snapshot(opening_records,self.tasker.controller.cached_image,mode):
            raise TaskIncomplete('对战：未确认实际房间，不开始匹配')
        # The owner may already be in a live question, but never start twice.
        records = opening_records
        start_verification_generation = getattr(self,'_fun_verification_generation',0)
        if not race_snapshot(records,self.tasker.controller.cached_image,mode):
            # A confirmed error from the previous request doesn't belong to this
            # new request. Its retry budget still remains shared by the run.
            metrics['expired_match_acknowledged']=False
            metrics['matching_error_acknowledged']=False
            start = r'随机匹配' if mode=='双人对战' else r'开始比赛'
            target=two_player_random_target(records,self.tasker.controller.cached_image) if mode=='双人对战' else None
            if mode=='双人对战' and target is None:
                raise TaskIncomplete('双人对战：未定位随机匹配图标')
            if not self._fun_click([target] if target else records,[start]):
                raise TaskIncomplete('对战：未确认唯一匹配按钮')
            metrics['rounds_started'] = 1
            metrics['random_match_clicks' if mode=='双人对战' else 'start_button_clicks']+=1
            self.send_log('趣味答题：已确认'+mode+'，请求一局，等待匹配')
        report=getattr(self,'run_report',None)
        if report:report.update(phase=mode+'：等待匹配')
        matching_deadline = self._race_time()+80
        while not race_snapshot(records,self.tasker.controller.cached_image,mode) and not race_finished(records):
            self._race_guard_network(records)
            recovered=self._retry_failed_matching(records,mode)
            if recovered is not None:
                records=recovered
                start_verification_generation=getattr(self,'_fun_verification_generation',0)
                matching_deadline=self._race_time()+80
            if self._race_score_exit(records):
                return
            verified_generation = getattr(self,'_fun_verification_generation',0)
            if (mode=='双人对战' and metrics['awaiting_double_start']
                    and two_player_start_ready(records,self.tasker.controller.cached_image)):
                if not self._fun_click(records,[r'^开始对战$']):
                    raise TaskIncomplete('双人对战：未确认可用的开始对战按钮')
                metrics['start_button_clicks']+=1
                metrics['awaiting_double_start']=False
                start_verification_generation=verified_generation
                matching_deadline=self._race_time()+80
                self.send_log('双人对战：随机匹配已就绪，开始本局')
            elif verified_generation > start_verification_generation and (four_player_room(records) or two_player_room(records)):
                if metrics['post_verification_start_clicks'] >= 1:
                    raise TaskIncomplete('对战：本人验证后已继续开始一次，仍返回大厅；停止自动重试')
                continuation = r'^开始比赛$' if mode=='四人赛' else r'^随机匹配$'
                target=two_player_random_target(records,self.tasker.controller.cached_image) if mode=='双人对战' else None
                if mode=='双人对战' and target is None:
                    raise TaskIncomplete('双人对战：验证后未定位随机匹配图标')
                if not self._fun_click([target] if target else records,[continuation]):
                    raise TaskIncomplete('对战：本人验证后未确认唯一匹配按钮')
                start_verification_generation = verified_generation
                metrics['post_verification_start_clicks'] += 1
                if mode=='双人对战':metrics['awaiting_double_start']=True
                matching_deadline = self._race_time()+80
                self.send_log('对战：本人验证已结束，当前仍在大厅；继续开始同一轮，不重开应用')
            if self._race_time() >= matching_deadline:
                self._race_save('01-match-timeout',records)
                raise TaskIncomplete('对战：80秒内未确认比赛题面，未重复匹配')
            self._sleep(0.25)
            records = self._fun_records()
        metrics['matched'] = True
        if report:report.update(phase=mode+'：比赛答题中')
        self._race_save('01-matched',records)
        self.send_log('对战：已进入比赛，按当前题面限时作答')
        executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='four-player-answer')
        pending = None
        attempted = set()
        attempted_questions = set()
        deadline = self._race_time()+240
        try:
            while self._race_time() < deadline:
                self._check_active()
                records = self._fun_records()
                self._race_guard_network(records)
                if self._race_score_exit(records):
                    return
                if race_finished(records):
                    metrics['round_finished'] = True
                    metrics['pending_answer_at_settlement'] = pending is not None
                    metrics['settlement_text'] = ' '.join(r['text'] for r in records)
                    self._race_save('99-settlement',records)
                    self.send_log('对战：已确认本局结算，返回核对实际积分；不重复匹配')
                    self._exit_competitive(records)
                    return
                current = race_snapshot(records,self.tasker.controller.cached_image,mode)
                if pending is not None:
                    if (not pending['future'].done() and current is not None
                            and (current['key'] != pending['key']
                                 or current.get('visual_key') != pending.get('visual_key'))
                            and not pending.get('cancel_requested')):
                        pending['cancel_requested']=True
                        cancel=getattr(self.ai_resolver,'cancel_pending',None)
                        if callable(cancel):cancel()
                    if pending['future'].done():
                        answer = pending['future'].result()
                        elapsed = time.monotonic()-pending['started']
                        metrics['model_seconds'].append(round(pending['timing'].get('model_seconds',elapsed),3))
                        if (current is None or current['key'] != pending['key'] or elapsed > 6
                                or current.get('visual_key') != pending.get('visual_key')):
                            decision='discarded'
                            metrics['discarded_answers'] += 1
                            self.send_log('对战：题面已切换或答案超时，丢弃旧答案')
                        elif type(answer) is not int or not 0 <= answer < len(current['options']):
                            decision='uncertain'
                            metrics['uncertain_answers'] += 1
                            self.send_log('对战：本题答案未确定，等待下一题')
                        else:
                            self._check_active()
                            option = current['options'][answer]
                            x,y,w,h = option['box']
                            if not self.tasker.controller.post_click(round(x+w/2),round(y+h/2)).wait().succeeded:
                                raise TaskIncomplete('对战：选项点击失败')
                            metrics['questions_clicked'] += 1
                            attempted_questions.add(pending['question_id'])
                            metrics.setdefault('observed_to_click_seconds',[]).append(round(time.monotonic()-pending['first_observed'],3))
                            decision='clicked'
                            self.send_log('对战：选择'+option['letter']+'，等待比赛推进')
                        self._race_answer_audit(pending,current,answer,elapsed,decision)
                        pending = None
                    self._sleep(0.05)
                    continue
                key = current['key']+'|'+(current.get('visual_key') or '') if current else None
                same_question = current and any(current['question_id'].startswith(old) for old in attempted_questions)
                if current and key not in attempted and not same_question:
                    if metrics['model_calls'] >= 30:
                        raise TaskIncomplete('对战：达到30题模型请求上限，停止本轮')
                    attempted.add(key)
                    image = self.tasker.controller.cached_image.copy()
                    # The text route sends no image; visual questions retain the full frame.
                    metrics['model_calls'] += 1
                    if report and '趣味答题' in report.snapshot()['tasks']:
                        report.task('趣味答题',metrics=metrics)
                    timing = {}
                    pending = dict(key=current['key'],visual_key=current.get('visual_key'),
                        question=current['question'],question_id=current['question_id'],
                        options=[r['text'] for r in current['options']],request_number=metrics['model_calls'],
                        first_observed=getattr(self,'_last_screen_read_started',time.monotonic()),timing=timing,
                        started=time.monotonic(),future=executor.submit(
                        self._resolve_race_timed,image,
                        [r['text'] for r in current['options']],current['question'],timing,current['visual_required']))
                    self._race_save(f"question-{metrics['model_calls']:02d}",records)
                    self.send_log('对战：完整题面已读取，后台答题并同时确认题面')
                self._sleep(0.05)
            self._race_save('98-round-timeout',records)
            raise TaskIncomplete('对战：240秒未确认结算，停止本轮，不再匹配')
        finally:
            cancel=getattr(self.ai_resolver,'cancel_pending',None)
            if callable(cancel):cancel()
            executor.shutdown(wait=False,cancel_futures=True)
