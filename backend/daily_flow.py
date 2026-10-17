"""Recognize daily submission stages separately from owner-only verification."""
import json
import re
import time
from pathlib import Path
import numpy as np
from PIL import Image
from task_support import TaskIncomplete, HumanRequired
from four_player import quiz_center
from home_navigation import home_ready


def final_submission_evidence(submitted):
    """Keep the real fifth-answer Click, which can submit directly after CAPTCHA."""
    if getattr(getattr(submitted,'status',None),'succeeded',False) is not True:
        return None
    found=[]
    for node in submitted.nodes:
        action=getattr(node,'action',None)
        point=getattr(getattr(action,'result',None),'point',None)
        coordinates=(point if isinstance(point,(list,tuple)) else
                     (getattr(point,'x',None),getattr(point,'y',None)))
        if len(coordinates)!=2 or any(type(value) is not int for value in coordinates):
            continue
        x,y=coordinates
        if (getattr(node,'name',None)=='确定' and getattr(node,'completed',False) is True
                and getattr(action,'name',None)=='确定' and getattr(action,'action',None)=='Click'
                and getattr(action,'success',False) is True
                and type(getattr(action,'action_id',None)) is int and action.action_id>0
                and 500<=x<710 and 35<=y<165):
            found.append(dict(question_number=5,node='确定',action='Click',success=True,
                              action_id=action.action_id,point=[x,y]))
    return found[0] if len(found)==1 else None


def acceptance_ready(evidence):
    finish=evidence.get('submission_metrics',{})
    final=evidence.get('final_answer_submission') or {}
    final_sent=(final.get('question_number')==5 and final.get('node')=='确定'
                and final.get('action')=='Click' and final.get('success') is True
                and type(final.get('action_id')) is int and final['action_id']>0)
    return (evidence.get('questions_confirmed')==5
            and (finish.get('complete_clicks',0)>=1 or final_sent)
            and finish.get('result_observed') is True
            and finish.get('result_back_clicks',0)>=1
            and finish.get('left_completion_page') is True)


def verification_visible(records):
    titles = [row for row in records if re.fullmatch(
        r'访问验证|访问异常|安全验证|请完成验证|请进行验证', row['text'].strip())
        and 70 <= row['box'][1] <= 550]
    instructions = any(re.search(r'拖动.*滑块|按照说明.*滑块|滑动.*验证|完成.*拼图', row['text']) for row in records)
    quiz = any(row['text'].strip() in ('单选题','多选题','填空题','点选填空题') for row in records)
    return bool(titles and (instructions or not quiz or any(row['box'][1] < 180 for row in titles)))


def profile_page(records):
    words = {row['text'].strip() for row in records}
    return {'我要答题','学习积分'} <= words


def result_page(records):
    return any(re.fullmatch(r'本次答题|答题完成|完成答题|本次答对\s*\d+\s*题|获得\s*\+?\d+\s*(?:个)?积分|再来一组|再答一次',
                           row['text'].strip()) for row in records)


def selected_question(records):
    """Selected options can replace their A/B labels with check marks."""
    sources = [row['box'][1] for row in records if re.match(r'^来源[：:]', row['text'].strip())]
    publishers = [row['box'][1] for row in records if re.match(r'^出题[：:]', row['text'].strip())]
    if len(sources) != 1 or len(publishers) != 1 or sources[0] >= publishers[0]:
        return False
    rows = sorted((row for row in records if 95 <= row['box'][0] <= 220
                   and sources[0]+55 < row['box'][1] < publishers[0]-20
                   and row['box'][3] >= 18 and len(row['text'].strip()) >= 2),
                  key=lambda row: row['box'][1])
    baselines = []
    for row in rows:
        if not baselines or row['box'][1]-baselines[-1] >= 45:
            baselines.append(row['box'][1])
    return 2 <= len(baselines) <= 6


def completion_box(records, pending=False):
    if verification_visible(records):
        return None
    fifth = any(re.fullmatch(r'5\s*[/／]\s*5|第五题|第5题', row['text'].strip())
                and 130 <= row['box'][1] <= 430 for row in records)
    quiz = any(row['text'].strip() in ('单选题','多选题','填空题','点选填空题') for row in records)
    scrolled_quiz = (any(re.match(r'^出题[：:]',row['text'].strip()) for row in records)
                     and any(re.match(r'^[A-F][.．、]',row['text'].strip()) for row in records))
    scrolled_quiz = scrolled_quiz or selected_question(records)
    if not (fifth or result_page(records) or (pending and (quiz or scrolled_quiz))):
        return None
    boxes = [row['box'] for row in records if row['text'].strip() == '完成'
             and ((480 <= row['box'][0] < 710 and 35 <= row['box'][1] < 165)
                  or (20 <= row['box'][0] < 680 and 850 <= row['box'][1] < 1230))]
    return boxes[0] if len(boxes) == 1 else None


def daily_scene(records, pending=False):
    if verification_visible(records):
        return 'verification'
    if any(re.search(r'网络开小差|网络异常|网络连接失败',row['text']) for row in records) and any(row['text'].strip()=='重试' for row in records):
        return 'network'
    if any(re.fullmatch(r'加载中[.。…]*',row['text'].strip()) for row in records):
        return 'loading'
    if completion_box(records,pending):
        return 'completion'
    if result_page(records):
        return 'result'
    if quiz_center(records):
        return 'center'
    if profile_page(records):
        return 'profile'
    if any(row['text'].strip() in ('积分中心','学习积分','积分明细') and 35 <= row['box'][1] < 160 for row in records):
        return 'score'
    if home_ready(records):
        return 'home'
    return 'unknown'


def save_stage(worker, scene, records):
    metrics = getattr(worker,'_daily_finish_metrics',{})
    metrics.setdefault('stages',[]).append(scene)
    worker._daily_finish_metrics = metrics
    report = getattr(worker,'run_report',None)
    if report and '每日答题' in report.snapshot().get('tasks',{}):
        report.task('每日答题',submission_metrics=metrics,completion_pending=True)
        report.update(blocked_task='每日答题',phase={
            'completion':'每日答题：点击完成提交本轮', 'verification':'每日答题：等待本人验证',
            'result':'每日答题：返回答题中心', 'network':'每日答题：重试当前结果页',
        }.get(scene,'每日答题：等待提交结果'))
    image = getattr(worker.tasker.controller,'cached_image',None)
    if not isinstance(image,np.ndarray):
        return
    from diagnostics import RunDiagnostics
    diagnostic=getattr(worker,'diagnostics',None)
    if isinstance(diagnostic,RunDiagnostics):diagnostic.capture(scene,records,image)
    try:
        folder=Path('logs/daily-finish')/(report.snapshot()['run_id'] if report else time.strftime('%Y%m%d-%H%M%S'))
        folder.mkdir(parents=True,exist_ok=True)
        name=f'{len(metrics["stages"]):02d}-{scene}'
        (folder/(name+'.json')).write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        Image.fromarray(image[:,:,::-1]).save(folder/(name+'.png'))
    except (OSError,ValueError,KeyError):
        pass


def resume_ready(worker):
    records=worker._screen_records()
    scene=daily_scene(records,pending=True)
    if scene not in ('completion','result','center','profile','score','home','network'):
        worker._daily_resume_stable=0
        worker._daily_resume_scene=None
        return False
    worker._daily_resume_stable = getattr(worker,'_daily_resume_stable',0)+1 if getattr(worker,'_daily_resume_scene',None)==scene else 1
    worker._daily_resume_scene=scene
    if worker._daily_resume_stable < 2 or not worker._app_in_foreground():
        return False
    worker.send_log('每日答题：验证页面已离开，识别到网络错误，继续本轮有限重试' if scene=='network'
                    else '每日答题：验证页面已离开，已确认正常页面，继续本轮收尾')
    return True


def finish_daily_quiz(worker):
    worker._daily_completion_expected=True
    worker._daily_finish_metrics=dict(complete_clicks=0,result_back_clicks=0,verification_pauses=0,network_retries=0,stages=[])
    metrics=worker._daily_finish_metrics
    last_scene=None
    last_click=None
    network_at=None
    unknown_samples=0
    result_wait=0
    for _ in range(100):
        worker._check_active()
        records=worker._screen_records()
        scene=daily_scene(records,pending=True)
        if scene != last_scene:
            save_stage(worker,scene,records)
            last_scene=scene
        if scene=='verification':
            if metrics['verification_pauses']>=3:
                raise HumanRequired('每日答题连续出现访问验证，已保留现场，等待本人处理')
            metrics['verification_pauses']+=1
            worker._daily_resume_stable=0
            worker._daily_resume_scene=None
            worker.pause('每日答题出现真实访问验证，请本人完成',resume_when=worker._daily_resume_ready)
            # Restart the post-tap wait after owner verification. Keeping None
            # here made the single delayed recovery unreachable after a CAPTCHA.
            last_click=time.monotonic() if metrics['complete_clicks'] else None
            unknown_samples=0
            last_scene=None
            continue
        if scene=='network':
            now=time.monotonic()
            if network_at is not None and now-network_at<4:
                worker._sleep(.5)
                continue
            if metrics['network_retries']>=1:
                raise HumanRequired('每日答题结果页重试1次仍未恢复，已保留现场')
            retries=[row for row in records if row['text'].strip()=='重试' and row['box'][1]>=500]
            if len(retries)!=1 or not worker._app_in_foreground():
                raise TaskIncomplete('每日答题网络错误页的重试按钮未确认')
            x,y,w,h=retries[0]['box']
            if not worker.tasker.controller.post_click(round(x+w/2),round(y+h/2)).wait().succeeded:
                raise TaskIncomplete('每日答题结果页重试点击失败')
            metrics['network_retries']+=1
            network_at=now
            worker.send_log(f'每日答题：已点击当前页面重试（{metrics["network_retries"]}/1）')
            worker._sleep(1)
            continue
        if scene=='completion':
            if last_click is not None and time.monotonic()-last_click<3:
                worker._sleep(.5)
                continue
            if metrics['complete_clicks']>=1:
                # The fifth page remained after an accepted input command.
                # Re-read the exact CTA before one delayed recovery press;
                # _completion_button refreshes the frame and rejects verification.
                if metrics['complete_clicks']==1 and last_click is not None and time.monotonic()-last_click>=6:
                    if not worker._completion_button('Press',records=records):
                        worker._sleep(.5)
                        continue
                    metrics['complete_clicks']+=1
                    last_click=time.monotonic()
                    unknown_samples=0
                    worker.send_log('每日答题：完成后未确认页面推进，重新识别后发送120毫秒短按（2/2），等待提交结果')
                    worker._sleep(.5)
                    continue
                unknown_samples+=1
                if unknown_samples>=12:
                    raise TaskIncomplete(f'每日答题已点击完成{metrics["complete_clicks"]}次，等待后页面仍未离开；现场已保存')
                worker._sleep(.5)
                continue
            if not worker._completion_button('Click',records=records):
                worker._sleep(.5)
                continue
            metrics['complete_clicks']+=1
            last_click=time.monotonic()
            worker.send_log('每日答题：已发送完成点击命令，等待页面确认提交结果')
            unknown_samples=0
            worker._sleep(.5)
            continue
        if scene=='result':
            metrics['result_observed']=True
            if metrics['result_back_clicks']:
                result_wait+=1
                if result_wait>=12:
                    raise TaskIncomplete('每日答题结果页返回后仍未离开，未重复开始新题')
                worker._sleep(.5)
                continue
            if not worker._app_in_foreground():
                raise TaskIncomplete('每日答题结果不在前台，未发送返回操作')
            returns=[row['box'] for row in records if row['text'].strip() in ('返回','返回答题中心') and row['box'][1]>=500]
            if len(returns)==1:
                x,y,w,h=returns[0]
                if not worker.tasker.controller.post_click(round(x+w/2),round(y+h/2)).wait().succeeded:
                    raise TaskIncomplete('每日答题结果页返回按钮点击失败')
            elif not returns:
                action=worker.tasker.post_task('返回').wait().get()
                if action.status.failed:
                    raise TaskIncomplete('每日答题结果页返回操作失败')
            else:
                raise TaskIncomplete('每日答题结果页出现多个返回按钮，未随意点击')
            metrics['result_back_clicks']+=1
            worker.send_log('每日答题：已识别本轮结果，自动返回答题中心')
            worker._sleep(.5)
            continue
        if scene in ('center','profile','score','home'):
            metrics['left_completion_page']=True
            report=getattr(worker,'run_report',None)
            if report and '每日答题' in report.snapshot().get('tasks',{}):
                report.task('每日答题',completion_pending=False,submission_metrics=metrics)
                report.update(blocked_task=None,phase='每日答题：返回完成，正在核实实际积分')
            worker._daily_completion_expected=False
            worker.send_log('每日答题：已离开第五题和结果页，下一步核实实际积分')
            return
        unknown_samples+=1
        if unknown_samples>=24:
            raise TaskIncomplete('每日答题提交结果长时间未确认，现场已保存；未把普通完成页当成验证')
        worker._sleep(.5)
    raise TaskIncomplete('每日答题收尾超过观察上限，现场已保留')
