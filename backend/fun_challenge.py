"""Route today's single-player, double-player and four-player fun tasks."""
import hashlib
import copy
import re
import time
import numpy as np
from PIL import Image, ImageFilter

from task_support import TaskIncomplete, TaskUnavailable, HumanRequired
from question_visual import visual_evidence, visual_identity
from four_player import FourPlayerQuiz, detect_fun_mode, race_network_error, race_match_expired, quiz_center, race_snapshot, race_finished, quiz_identity, race_score_page


def options_from_records(records):
    options = []
    for row in records:
        match = re.match(r'^([A-F])(?:[.．、:：\s]|$)', row['text'].strip())
        if match and 130 < row['box'][1] < 1130 and 10 <= row['box'][0] < 200:
            options.append(dict(letter=match[1], box=row['box'], text=row['text']))
    options.sort(key=lambda row: row['box'][1])
    if not 2 <= len(options) <= 6 or [row['letter'] for row in options] != list('ABCDEF'[:len(options)]):
        return []
    for index, row in enumerate(options):
        end = options[index+1]['box'][1]-5 if index+1 < len(options) else min(1140, row['box'][1]+160)
        lines = sorted((item for item in records if row['box'][1]-8 <= item['box'][1] < end
                        and not re.match(r'^(?:出题|来源|推荐单位)[：:]', item['text'])),
                       key=lambda item: (item['box'][1], item['box'][0]))
        row['text'] = ' '.join(item['text'] for item in lines)
    return options


def challenge_options(records, image):
    """This app renders wide rounded choices without letter labels."""
    if image is None or image.shape[:2] != (1280,720):
        return []
    crop = image[270:1140,60:660,:3].astype(np.int16)
    lo, hi = crop.min(axis=2), crop.max(axis=2)
    mask = (lo >= 225) & (hi <= 249) & (hi-lo <= 15)
    mask = np.asarray(Image.fromarray(mask.astype(np.uint8)*255).filter(ImageFilter.MaxFilter(3))) > 0
    height, width = mask.shape
    boxes = []
    for sy,sx in zip(*np.nonzero(mask)):
        if not mask[sy,sx]:
            continue
        mask[sy,sx] = False
        stack = [(int(sy),int(sx))]
        xmin=xmax=int(sx); ymin=ymax=int(sy)
        while stack:
            y,x = stack.pop()
            xmin,xmax=min(xmin,x),max(xmax,x); ymin,ymax=min(ymin,y),max(ymax,y)
            for ny,nx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
                if 0<=ny<height and 0<=nx<width and mask[ny,nx]:
                    mask[ny,nx] = False; stack.append((ny,nx))
        w,h=xmax-xmin+1,ymax-ymin+1
        if 450<=w<=600 and 55<=h<=180:
            boxes.append([xmin+60,ymin+270,w,h])
    boxes.sort(key=lambda box:box[1])
    if not 2<=len(boxes)<=6:
        return []
    result=[]
    for index,box in enumerate(boxes):
        x,y,w,h=box
        lines=sorted((row for row in records if x+5<=row['box'][0]<x+w-5
                      and y+5<=row['box'][1]<y+h-5),key=lambda row:(row['box'][1],row['box'][0]))
        text=''.join(row['text'].strip() for row in lines)
        if not text:
            return []
        result.append(dict(letter='ABCDEF'[index],box=box,text=text))
    return result


def question_body(records, options, option_top=None):
    if not options:
        return ''
    sources = [row['box'][1] for row in records if re.match(r'^(来源|出题)[：:]',row['text'])]
    # Parts of the same source line can be a few pixels above its label.
    # Exclude the entire source baseline, including publisher fragments.
    source_y = min(sources)-18 if sources else 1140
    end_y = min(source_y,options[0]['box'][1],option_top if option_top is not None else 1140)
    rows = sorted((row for row in records if 240 <= row['box'][1] < end_y and row['box'][3]>=18
                   and not re.fullmatch(r'(?:挑战答题|已答对\d+(?:道|题)|\d+秒|\d+)', row['text'].strip())),key=lambda row:row['box'][1])
    lines=[]
    for row in rows:
        if not lines or abs(row['box'][1]-lines[-1][0]['box'][1])>18:
            lines.append([])
        lines[-1].append(row)
    return ' '.join(row['text'].strip() for line in lines for row in sorted(line,key=lambda row:row['box'][0]))


def question_text(records, options, option_top=None):
    return quiz_identity(question_body(records,options,option_top))


def fingerprint(records, options):
    question = question_text(records, options)
    if not question:
        return None
    return hashlib.sha256('|'.join([question]+[quiz_identity(row['text']) for row in options]).encode('utf-8')).hexdigest()


def correct_count(records):
    values = set()
    for row in records:
        match = re.fullmatch(r'(?:本次|已|累计)?答对\s*(\d+)\s*(?:道|题)(?:目)?', row['text'].strip())
        if match:
            values.add(int(match[1]))
        match = re.fullmatch(r'连续答对\s*[xX×]?\s*(\d+)',row['text'].strip())
        if match:
            values.add(int(match[1]))
    return values.pop() if len(values) == 1 else None


def wrong_or_finished(records):
    return any(re.search(r'回答错误|答错了|回答失误|本次挑战结束|挑战结束|再来一局|再来一次|结束本局', row['text']) for row in records)


def challenge_settled(records):
    return any(re.fullmatch(r'(?:本次)?(?:挑战|练习|答题)(?:已)?结束|再来一局|再来一次|再练一次|再练一组|再答一次|再次挑战',
                            row['text'].strip()) for row in records)


def challenge_target(rules, maximum=10):
    clean = re.sub(r'\s+','',rules or '')
    match = re.search(r'挑战答题.{0,8}?每次(?:连续)?答对(\d+)(?:道|题)(?:及以上)?积(\d+)分',clean)
    if match and 1 <= int(match[1]) <= 10 and int(match[2]) == maximum:
        return int(match[1])
    return 10


class FunChallenge(FourPlayerQuiz):
    def _fun_options(self, records):
        return challenge_options(records, self.tasker.controller.cached_image)

    def _fun_click(self, records, expressions, min_y=35):
        matches = [row for row in records if row['box'][1] >= min_y
                   and any(re.fullmatch(expression, row['text'].strip()) for expression in expressions)]
        if len(matches) != 1:
            return False
        self._check_active()
        x, y, w, h = matches[0]['box']
        if not (0 <= x < 720 and 35 <= y < 1240 and w > 0 and h > 0):
            raise TaskIncomplete('趣味答题按钮坐标无效')
        if not self.tasker.controller.post_click(round(x+w/2), round(y+h/2)).wait().succeeded:
            raise TaskIncomplete('趣味答题按钮点击失败')
        return True

    def _fun_records(self):
        if not isinstance(getattr(self,'_fun_metrics',None),dict):self._fun_metrics={}
        resumed = False
        verification_count = 0
        loading_started = None
        loading_samples = 0
        for _ in range(50):
            records = self._screen_records()
            measured = getattr(self,'_last_screen_read_seconds',None)
            if isinstance(measured,(int,float)):
                self._fun_metrics.setdefault('screen_read_seconds',[]).append(round(measured,3))
                self._fun_metrics['screen_read_seconds'] = self._fun_metrics['screen_read_seconds'][-300:]
            if self._captcha_present(records):
                if getattr(self,'_fun_metrics',{}).get('mode') in ('四人赛','双人对战'):
                    self._race_save('90-owner-verification',records)
                report=getattr(self,'run_report',None)
                if report and '趣味答题' in report.snapshot()['tasks']:
                    report.task('趣味答题',metrics=getattr(self,'_fun_metrics',{}))
                    report.update(blocked_task='趣味答题',phase='趣味答题：等待本人验证')
                if verification_count >= 3:
                    raise TaskIncomplete('趣味答题访问验证仍未完成')
                self._fun_resume_stable=0
                self.pause('趣味答题真实访问验证，请本人完成',resume_when=self._fun_resume_ready)
                verification_count += 1
                resumed = True
                loading_started = None
                loading_samples = 0
                continue
            if race_match_expired(records):
                self._race_save('95-match-expired',records)
                if getattr(self,'_fun_metrics',{}).get('expired_match_acknowledged'):
                    raise TaskIncomplete('匹配失效提示再次出现，已保留现场')
                if not self._fun_click(records,[r'确定'],min_y=500):
                    raise TaskIncomplete('匹配已失效，但未确认提示按钮')
                self._fun_metrics.update(expired_match_acknowledged=True,matching_request_failed=True)
                self.send_log('对战：原匹配已结束，已点击确定，检查返回页面')
                self._sleep(.5)
                continue
            network_error = race_network_error(records)
            if network_error:
                if hasattr(self,'_fun_metrics'):
                    self._fun_metrics.update(network_error=network_error, network_error_records=records)
                confirms=[r for r in records if r['text'].strip()=='确定' and r['box'][1]>=500]
                retry_buttons=[r for r in records if r['text'].strip()=='重试' and r['box'][1]>=500]
                if len(confirms)==1 and not retry_buttons:
                    if self._fun_metrics.get('matching_error_acknowledged'):
                        raise HumanRequired('匹配网络提示再次出现，已保留现场，不循环确认')
                    if not self._fun_click(records,[r'确定'],min_y=500):
                        raise TaskIncomplete('匹配网络提示确认失败')
                    self._fun_metrics.update(matching_error_acknowledged=True,matching_request_failed=True)
                    self.send_log('对战：已确认网络错误弹窗，检查大厅或返回页面')
                    self._sleep(.5)
                    continue
                now = self._race_time()
                last = getattr(self,'_fun_network_retry_at',None)
                if last is not None and now-last < 4:
                    self._sleep(.5)
                    continue
                count = getattr(self,'_fun_network_retry_count',0)
                if count >= 1:
                    raise HumanRequired('趣味答题已重试1次仍报告网络错误；保留现场，等待本人验收')
                if not self._fun_click(records,[r'重试'],min_y=500):
                    raise TaskIncomplete('趣味答题网络错误页未找到唯一的重试按钮；保留现场')
                self._fun_network_retry_count = count+1
                self._fun_network_retry_at = now
                if hasattr(self,'_fun_metrics'):
                    self._fun_metrics['network_retry_clicks_current_run'] = count+1
                    self._fun_metrics['network_retry_clicks'] = self._fun_metrics.get('network_retry_clicks',0)+1
                self.send_log(f'趣味答题：网络错误，已点击重试（{count+1}/1），等待本轮页面恢复')
                loading_started = None
                loading_samples = 0
                self._sleep(1)
                continue
            if any(re.fullmatch(r'加载中[.。…]*',row['text'].strip()) for row in records):
                if loading_started is None:
                    loading_started = self._race_time()
                loading_samples += 1
                if self._race_time()-loading_started >= 12 or loading_samples >= 25:
                    break
                self._sleep(.5)
                continue
            if resumed:
                self._fun_verification_generation = getattr(self,'_fun_verification_generation',0)+1
            return records
        if loading_started is not None:
            if hasattr(self,'_fun_metrics'):
                self._fun_metrics.update(loading_timeout=True, loading_records=records)
            error = HumanRequired if getattr(self,'_fun_network_retry_count',0) else TaskIncomplete
            raise error('趣味答题页面加载长时间未结束；现场已保留，未重复提交或重新答题')
        if getattr(self,'_fun_network_retry_count',0):
            raise HumanRequired('趣味答题重试后页面仍未恢复；保留现场，等待本人验收')
        raise TaskIncomplete('趣味答题访问验证仍未完成')

    def _fun_resume_ready(self):
        from daily_flow import daily_scene
        records=self._screen_records()
        if self._captcha_present(records):
            self._fun_resume_stable=0
            return False
        mode=getattr(self,'_fun_mode',None)
        live_question=race_snapshot(records,self.tasker.controller.cached_image,mode) is not None
        positive=(daily_scene(records) in ('center','profile','score','home','network')
                  or detect_fun_mode(records,True) in ('四人赛','双人对战')
                  or race_finished(records) or challenge_settled(records)
                  or live_question or race_match_expired(records) or bool(race_network_error(records))
                  or fingerprint(records,self._fun_options(records)) is not None)
        self._fun_resume_stable=getattr(self,'_fun_resume_stable',0)+1 if positive else 0
        ready=(live_question or self._fun_resume_stable>=2) and self._app_in_foreground()
        if ready:self.send_log('趣味答题：验证页面已离开，继续检查同一轮状态')
        return ready

    def _guard_fun_settlement_count(self, records):
        observed = correct_count(records)
        expected = getattr(self,'_fun_metrics',{}).get('confirmed_correct',0)
        if observed is not None and expected > 0 and observed < expected:
            self._fun_metrics.update(settlement_count_mismatch=True, observed_settlement_correct=observed)
            raise TaskIncomplete(f'趣味答题结束页显示答对{observed}题，与保存的{expected}题不一致；'
                                 '现场已保留，未重复提交或重新开局')

    def _open_fun_challenge(self,target_mode=None):
        current = self._fun_records()
        if race_score_page(current):
            self.send_log('趣味答题：从积分页返回，复用个人页或首页，不重开应用')
            self.tasker.post_task('返回').wait()
            self._sleep(.15)
            current = self._fun_records()
        mode = detect_fun_mode(current,True)
        if not quiz_center(current) and mode in ('四人赛','双人对战') and (target_mode is None or mode==target_mode):
            self._fun_mode=mode
            return current
        entries=(
            ('本机趣味个人页', '我的', [600,35,120,80]),
            ('本机趣味答题中心', '我要答题', [20,250,680,450]),
        )
        if quiz_center(current):
            entries=()
        elif {'我要答题','学习积分'} <= {row['text'].strip() for row in current}:
            entries=entries[1:]
        else:
            from daily_flow import daily_scene
            if daily_scene(current) != 'home' or not self._app_in_foreground():
                self._reset_home()
        for name, label, roi in entries:
            self._fun_records()
            result = self.tasker.post_task(name, {name: {
                'recognition':'OCR', 'expected':['^'+label+'$'], 'roi':roi,
                'action':'Click', 'timeout':8000,
            }}).wait().get()
            if result.status.failed:
                raise TaskUnavailable('趣味答题：未找到'+label+'入口')
            self._sleep(.5)
        opened_today = False
        opened_bank = False
        entry_deadline = self._race_time()+45
        marker_logged = False
        for _ in range(80):
            if self._race_time() >= entry_deadline:
                break
            records = self._fun_records()
            if quiz_center(records) and not opened_today and not target_mode and not marker_logged:
                if not any(r['text'].strip()=='今日趣味答题' for r in records):
                    self.send_log('趣味答题：答题中心已加载，等待今日计分标记；不盲选其它模式')
                    marker_logged = True
            if target_mode and quiz_center(records):
                # The center heading can appear before its cards finish loading.
                # Wait for the exact card, and don't click again during transition.
                if not opened_today and self._fun_click(records,[target_mode]):
                    opened_today=True
                self._sleep(.5)
                continue
            # A room title identifies the actual mode; center cards don't.
            mode = detect_fun_mode(records,opened_today)
            if not quiz_center(records) and mode in ('双人对战','四人赛'):
                self._fun_mode=mode
                return records
            if mode == '挑战答题':
                if not opened_bank and self._fun_click(records, [r'强国总题库']):
                    opened_bank = True
                    self.send_log('趣味答题：进入挑战答题的强国总题库')
                    self._sleep(2)
                    continue
            if opened_bank and self._fun_options(records):
                self.send_log('趣味答题：已确认单人挑战题目')
                return records
            if not opened_today and self._fun_click(records, [r'今日趣味答题']):
                opened_today = True
                self.send_log('趣味答题：进入今天的计分模式')
                self._sleep(2)
                continue
            self._sleep(1)
        raise TaskUnavailable('趣味答题：未确认今日计分模式入口或题面')

    def _wait_fun_settlement(self):
        started = self._race_time()
        next_progress = started
        for _ in range(100):
            self._check_active()
            elapsed = self._race_time()-started
            if elapsed >= 15:
                break
            records = self._fun_records()
            if challenge_settled(records):
                self._guard_fun_settlement_count(records)
                self._fun_metrics.update(settlement_observed=True, settlement_wait_seconds=round(elapsed,1),
                                         settlement_records=records, settlement_committed=False)
                report = getattr(self,'run_report',None)
                if report and '趣味答题' in report.snapshot()['tasks']:
                    report.task('趣味答题',completion_pending=True,metrics=self._fun_metrics)
                self.send_log('趣味答题：已识别挑战结束，准备点击结束本局提交')
                return
            if self._race_time() >= next_progress:
                self.send_log('趣味答题：已完成收尾作答，等待本局结束结果（最多15秒）')
                report = getattr(self,'run_report',None)
                if report:
                    report.update(phase='趣味答题：等待正常收尾结果')
                next_progress = self._race_time()+15
            self._sleep(.5)
        raise TaskIncomplete('趣味答题尚未确认本局结算，未点击放弃历史的退出按钮')

    def _exit_fun_challenge(self, records=None):
        self._check_active()
        records = self._fun_records() if records is None else records
        end_clicked = False
        result_back_clicked = False
        for _ in range(12):
            words = ' '.join(row['text'] for row in records)
            if re.search(r'(?:作答|答题)历史.*不(?:会)?保存', words):
                self._fun_click(records,[r'继续',r'继续答题'])
                raise TaskIncomplete('趣味答题退出提示作答历史不会保存，已取消退出，未将放弃练习当成结算')
            if '强国总题库' in words or ('我要答题' in words and not self._fun_options(records)):
                if hasattr(self,'_fun_metrics'):
                    self._fun_metrics.update(settlement_committed=True, exited=True)
                report = getattr(self,'run_report',None)
                if report and '趣味答题' in report.snapshot()['tasks']:
                    report.task('趣味答题',completion_pending=False,metrics=self._fun_metrics)
                self.send_log('趣味答题：已确认退出挑战，返回核对积分')
                return
            replay_page = (any(row['text'].strip() == '再来一局' and row['box'][1] >= 350 for row in records)
                           and not any(row['text'].strip() in ('结束本局','立即复活') for row in records))
            if replay_page:
                self._guard_fun_settlement_count(records)
                if not (end_clicked or self._fun_metrics.get('end_round_clicked')):
                    raise TaskIncomplete('趣味结果页缺少本轮提交记录，未继续导航或重新开局')
                if not result_back_clicked:
                    self._fun_metrics['result_page_observed'] = True
                    self.tasker.post_task('返回').wait()
                    result_back_clicked = True
                    self.send_log('趣味答题：已识别提交后的再来一局结果页，返回核分')
            elif not end_clicked and not result_back_clicked:
                self._guard_fun_settlement_count(records)
                if not challenge_settled(records) or not self._fun_click(records,[r'结束本局']):
                    raise TaskIncomplete('趣味答题未找到已结束本局的提交按钮，未用系统返回代替结算')
                end_clicked = True
                self._fun_metrics['end_round_clicked'] = True
                self.send_log('趣味答题：已点击结束本局，等待提交结果')
            self._sleep(0.5)
            records = self._fun_records()
        raise TaskIncomplete('趣味答题：已点击结束本局，但尚未确认提交结果；未重复点击')

    def _resume_fun_submission(self, previous):
        report = getattr(self,'run_report',None)
        old = previous.get('tasks',{}).get('趣味答题',{})
        metrics = old.get('metrics',{})
        if (report and '趣味答题' in report.snapshot()['tasks'] and
                report.snapshot()['tasks']['趣味答题'].get('state','pending')=='pending' and
                metrics.get('mode') in ('四人赛','双人对战') and not metrics.get('exited')):
            records=self._fun_records()
            if metrics.get('round_finished') or (metrics.get('rounds_started') and race_finished(records)):
                self._fun_metrics=copy.deepcopy(metrics)
                self._fun_metrics['round_finished']=True
                self._fun_mode=metrics['mode']
                report.task('趣味答题',state='running',completion_pending=True,metrics=self._fun_metrics)
                report.update(blocked_task='趣味答题',phase='趣味答题：恢复已有对战结算')
                self._exit_competitive(records)
                report.task('趣味答题',state='pending',completion_pending=False,metrics=self._fun_metrics)
                return True
            if metrics.get('matched') and race_snapshot(records,self.tasker.controller.cached_image,metrics['mode']):
                self._fun_metrics=copy.deepcopy(metrics)
                self._fun_mode=metrics['mode']
                report.task('趣味答题',state='running',completion_pending=True,metrics=self._fun_metrics)
                report.update(blocked_task='趣味答题',phase='趣味答题：恢复进行中的对战')
                self.four_player_answer(records)
                report.task('趣味答题',state='pending',completion_pending=False,metrics=self._fun_metrics)
                return True
        if (not report or '趣味答题' not in report.snapshot()['tasks'] or
                metrics.get('mode') != '挑战答题' or not metrics.get('settlement_observed') or
                metrics.get('confirmed_correct',0) < metrics.get('target_correct',10) or metrics.get('exited')):
            return False
        if report.snapshot()['tasks']['趣味答题'].get('state','pending') != 'pending':
            return False
        self._fun_metrics = copy.deepcopy(metrics)
        self._fun_metrics['resumed_submission'] = True
        report.task('趣味答题',state='running',completion_pending=True,metrics=self._fun_metrics)
        report.update(blocked_task='趣味答题',phase='趣味答题：检查已有本轮结算')
        records = self._fun_records()
        if wrong_or_finished(records):
            self._guard_fun_settlement_count(records)
        if not challenge_settled(records):
            report.task('趣味答题',state='pending',completion_pending=False)
            return False
        self.send_log('趣味答题：恢复已满足计分要求的本轮结算')
        self._exit_fun_challenge(records)
        report.task('趣味答题',state='pending',completion_pending=False,metrics=self._fun_metrics)
        return True

    def funny_answer(self):
        self._fun_mode = '挑战答题'
        report = getattr(self,'run_report',None)
        snapshot = report.snapshot() if report else {}
        rules = snapshot.get('visible_task_rules',{}).get('趣味答题','')
        maximum = (snapshot.get('tasks',{}).get('趣味答题',{}).get('before') or {}).get('maximum',10)
        target = challenge_target(rules,maximum)
        self._fun_metrics = dict(mode='挑战答题', target_correct=target, target_rules=rules, questions_clicked=0,
                                 model_calls=0, confirmed_correct=0, exited=False,
                                 ending_answers_clicked=0, count_evidence='single-player next-question transitions', model_seconds=[])
        opening_records = self._open_fun_challenge()
        self._fun_metrics['mode']=self._fun_mode
        if self._fun_mode == '四人赛':
            return self.four_player_answer(opening_records)
        if self._fun_mode == '双人对战':
            return self.two_player_answer(opening_records)
        pending = None
        highest = 0
        idle = 0
        idle_started = None
        for _ in range(500):
            self._check_active()
            records = self._fun_records()
            count = correct_count(records)
            options = self._fun_options(records)
            current = fingerprint(records, options)
            feedback = any(re.search(r'连续答对|答对了|回答正确',row['text']) for row in records)
            if pending and current and not feedback and question_text(records,options,pending[3]) != pending[2] and not wrong_or_finished(records):
                if count is not None and count != pending[1]+1:
                    raise TaskIncomplete('趣味答题题面改变但答对计数未确认，未误计成功')
                highest = pending[1]+1
                self._fun_metrics['confirmed_correct'] = highest
                self.send_log(f'趣味答题：已确认答对{highest}题并进入下一题')
                pending = None
            if count is not None:
                if count < highest:
                    raise TaskIncomplete('趣味答题计数回退，停止本轮并保留实际结果')
                highest = count
                self._fun_metrics['confirmed_correct'] = count
            finishing = highest >= target
            if finishing and wrong_or_finished(records):
                self.send_log(f'趣味答题：应用确认答对{highest}题，本局已结束，准备结算')
                self._wait_fun_settlement()
                self._exit_fun_challenge()
                self._fun_metrics['exited'] = True
                return
            if not finishing and wrong_or_finished(records):
                raise TaskIncomplete(f'趣味答题本局结束，应用确认答对{highest}题；积分由积分页复核')
            if finishing and not self._fun_metrics.get('normal_end_started'):
                self._fun_metrics['normal_end_started'] = True
                self.send_log(f'趣味答题：已满足答对{target}题的计分要求，完成正常收尾作答')
            if not current or pending:
                idle += 1
                if idle_started is None:
                    idle_started = time.monotonic()
                if time.monotonic()-idle_started >= 6 or idle >= 60:
                    raise TaskIncomplete('趣味答题未确认下一题或答对计数，已停止重复点击')
                self._sleep(0.12)
                continue
            if self._fun_metrics['model_calls'] >= 12:
                raise TaskIncomplete('趣味答题达到12次模型调用上限')
            if finishing and self._fun_metrics['ending_answers_clicked'] >= 2:
                raise TaskIncomplete('趣味答题两次收尾作答仍未结束，保留现场，未空等下一题超时')
            image = self.tasker.controller.cached_image.copy()
            visual,visual_area=visual_evidence(image,records,options,question_body(records,options))
            picture_key=visual_identity(image,visual_area) if visual and visual_area is not None else None
            first_observed = getattr(self,'_last_screen_read_started',time.monotonic())
            self._fun_metrics['model_calls'] += 1
            self.send_log(f'趣味答题：已确认答对{highest}题，DeepSeek识别下一题')
            started = time.monotonic()
            answer = self.ai_resolver.resolve_fun_choice([image], [row['text'] for row in options],
                question_body(records,options),visual_required=visual)
            self._fun_metrics['model_seconds'].append(round(time.monotonic()-started,2))
            self._check_active()
            fresh = self._fun_records()
            fresh_options = self._fun_options(fresh)
            if (fingerprint(fresh, fresh_options) != current or correct_count(fresh) != count
                    or (picture_key is not None and picture_key != visual_identity(self.tasker.controller.cached_image,visual_area))):
                self._fun_metrics['stale_evidence'] = dict(before=records, after=fresh,
                    before_options=options, after_options=fresh_options)
                discarded = self._fun_metrics.get('stale_answers_discarded',0)+1
                self._fun_metrics['stale_answers_discarded'] = discarded
                if discarded >= 3:
                    raise TaskIncomplete('趣味答题已丢弃3次过期答案，页面仍不稳定，停止本轮')
                self.send_log('趣味答题：识别文字有变化，丢弃本次答案，重新截图和请求答案（最多补充两次）')
                self._sleep(.5)
                continue
            if answer is None:
                raise TaskIncomplete('趣味答题答案无法确定：'+str(self.ai_resolver.last_error))
            selected_index = (answer+1) % len(fresh_options) if finishing and fresh_options else answer
            selected = next((row for index,row in enumerate(fresh_options) if index == selected_index), None)
            if selected is None:
                raise TaskIncomplete('趣味答题模型答案不在当前选项中')
            x, y, w, h = selected['box']
            if not self.tasker.controller.post_click(round(x+w/2), round(y+h/2)).wait().succeeded:
                raise TaskIncomplete('趣味答题选项点击失败')
            self._fun_metrics['questions_clicked'] += 1
            self._fun_metrics.setdefault('observed_to_click_seconds',[]).append(round(time.monotonic()-first_observed,3))
            if finishing:
                self._fun_metrics['ending_answers_clicked'] += 1
                self.send_log('趣味答题：计分要求已满足，已提交收尾选项，等待结束本局')
            else:
                self.send_log('趣味答题：已选择'+selected['text']+'，等待应用推进到下一题')
            pending = (current, highest, question_text(fresh,fresh_options),fresh_options[0]['box'][1])
            idle = 0
            idle_started = None
            self._sleep(0.12)
        raise TaskIncomplete('趣味答题达到有限步骤上限')
