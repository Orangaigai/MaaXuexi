"""Local task control and DeepSeek configuration for the installed runtime."""
import asyncio
import json
import os
import sqlite3
import subprocess
import threading
import traceback
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from queue import SimpleQueue
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, SecretStr, ConfigDict, field_validator, StrictBool

from key_store import key_configured, load_key, save_key
from provider_support import MODEL, invalidate_validation, provider_metadata, test_connection
from utils import MaaWorker
from task_support import VERSION, read_report, ordered_tasks, ALIASES, OPTIONAL_TASKS, TaskIncomplete
from comment_tasks import comment_policy
from schedule_support import (ScheduleOptions, read_schedule, save_schedule, set_execution_mode,
    daily_started_today, claim_daily_run, release_daily_claim, rearm_for_run)
from points_history import PointsHistory
from history_import import HistoryImport
from completion_actions import CompletionActions, read_options, save_options
from local_preferences import LocalPreferences, read_preferences, save_preferences

class CompletionOptions(BaseModel):
    auto_close: StrictBool | None = None
    close_emulator: StrictBool | None = None
    close_console: StrictBool | None = None
    model_config = ConfigDict(extra='forbid')

class HistoryImportOptions(BaseModel):
    include_details: StrictBool = False
    model_config = ConfigDict(extra='forbid')

class ConfigModel(BaseModel):
    api_key: SecretStr = SecretStr('')
    model: str = MODEL

class ExecutionMode(BaseModel):
    mode: Literal['manual','automatic']
    model_config = ConfigDict(extra='forbid')

class DeviceModel(BaseModel):
    name: str
    adb_path: str
    address: str
    screencap_methods: int
    input_methods: int
    config: dict

class TaskModel(BaseModel):
    tasklist: list[Literal['登录', '选读文章', '视听学习', '本地频道', '订阅', '发表观点', '趣味答题', '每日答题']]
    fast_answer: bool = False
    debug: bool = False
    unattended: StrictBool = False
    model_config = ConfigDict(extra="forbid")

    @field_validator('tasklist')
    @classmethod
    def normalize_tasks(cls, tasks):
        return ordered_tasks(tasks)

class TaskPlanModel(TaskModel):
    task_order: list[Literal['每日答题','订阅','发表观点','趣味答题']] | None = None

    @field_validator('task_order')
    @classmethod
    def valid_order(cls, order):
        if order is not None and (len(order)!=len(OPTIONAL_TASKS) or set(order)!=set(OPTIONAL_TASKS)):
            raise ValueError('请完整排列每日答题、订阅、发表观点和趣味答题，每项仅出现一次。')
        return order

class AppState:
    def __init__(self):
        self.message_conn = SimpleQueue()
        self.child_process = None
        self.worker = None
        self.history_message = deque(maxlen=600)
        self.message_sequence = 0
        self.log_session = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.current_tasks = []
        self.points = PointsHistory()
        self.history_import = HistoryImport(self.points, self.collect_history)
        self.completion = CompletionActions()
        self.shutdown = lambda: None
        self.shutting_down = False

    def collect_history(self, progress, cancel, *, include_details=False):
        from history_reader import collect_platform_history
        return collect_platform_history(self.message_conn, progress, cancel, self.points.ledger_index(),
                                        include_details=include_details)

app_state = AppState()

def task_running():
    return app_state.child_process is not None and app_state.child_process.is_alive()

def ensure_worker():
    with app_state.lock:
        if app_state.worker is None:
            app_state.worker = MaaWorker(app_state.message_conn, api_key=load_key(), model=MODEL)
            app_state.worker.score_observer = record_scores
        return app_state.worker

def record_scores(scores):
    try:
        app_state.points.observe(scores)
    except (OSError, sqlite3.Error):
        app_state.worker.send_log('积分记录暂未写入，任务结束时会再次保存。')

def configure_key(key):
    with app_state.lock:
        if task_running():
            raise HTTPException(409, '请先中止任务，再配置密钥。')
        if key:
            try:
                save_key(key)
            except ValueError:
                raise HTTPException(400, '密钥不能为空或含有空格。')
            invalidate_validation()
        worker = ensure_worker()
        worker.api_key = load_key()
        old_session = worker.ai_resolver.session
        worker.update_ai_models(model=MODEL)
        old_session.close()

async def collect_messages():
    next_cleanup=0
    next_completion=0
    while True:
        for _ in range(500):
            if app_state.message_conn.empty():
                break
            message = app_state.message_conn.get_nowait()
            app_state.history_message.append((app_state.message_sequence, message))
            app_state.message_sequence += 1
        if time.monotonic()>=next_cleanup:
            from log_retention import prune_logs
            next_cleanup=time.monotonic()+3600
            report=current_report()
            try:await asyncio.to_thread(prune_logs,keep_trace=report.get('run_id'))
            except OSError:pass
        if time.monotonic() >= next_completion:
            next_completion = time.monotonic() + .5
            with app_state.lock:
                if app_state.completion.state['state'] in ('pending', 'closing'):
                    app_state.completion.poll(current_report(), running=task_running() or app_state.history_import.running,
                                              shutdown=app_state.shutdown)
        await asyncio.sleep(0.05)

@asynccontextmanager
async def lifespan(app):
    app_state.completion = CompletionActions()
    app_state.shutting_down = False
    collector = asyncio.create_task(collect_messages())
    await asyncio.sleep(0.5)
    yield
    if app_state.worker:
        app_state.worker.stop_flag = True
        app_state.worker.pause_flag = False
    app_state.completion.cancel()
    app_state.history_import.cancel()
    collector.cancel()
    try:
        await collector
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)
app.mount('/assets', StaticFiles(directory='page/assets'))

@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Do not echo submitted API keys in validation responses.
    details = [{k:v for k,v in item.items() if k in ('loc','msg','type')} for item in error.errors()]
    return JSONResponse(status_code=422, content={'detail':details})

@app.get('/')
def homepage():
    html = Path('page/index.html').read_text('utf-8')
    html = html.replace('</body>', '<script defer src="/assets/task-order.js?v=' + VERSION + '"></script></body>')
    html = html.replace('</body>', '<script defer src="/assets/local-provider.js"></script></body>')
    html = html.replace('/assets/local-provider.js', '/assets/local-provider.js?v=' + VERSION)
    html = html.replace('</body>', '<script defer src="/assets/schedule-view.js?v=' + VERSION + '"></script></body>')
    html = html.replace('</body>', '<script defer src="/assets/schedule-summary.js?v=' + VERSION + '"></script></body>')
    html = html.replace('</body>', '<script defer src="/assets/points-history.js?v=' + VERSION + '"></script></body>')
    html = html.replace('</body>', '<script defer src="/assets/history-import.js?v=' + VERSION + '"></script></body>')
    return HTMLResponse(html, headers={'Cache-Control':'no-store'})

@app.get('/local/api-key')
def api_key_page(source: str | None = None):
    html=Path('backend/api-key.html').read_text('utf-8')
    if source == 'settings':
        html=html.replace('href="/">← 返回控制台', 'href="/local/settings#serviceHeading">← 返回设置')
        html=html.replace('href="/" class="muted">暂不配置，返回控制台', 'href="/local/settings#serviceHeading" class="muted">暂不配置，返回设置')
    html=html.replace('/assets/key-setup.js','/assets/key-setup.js?v='+VERSION)
    return HTMLResponse(html,headers={'Cache-Control':'no-store'})

@app.get('/local/settings')
def preferences_page():
    html = Path('page/settings.html').read_text('utf-8')
    for name in ('local-settings.js', 'history-import.js', 'schedule-view.js', 'local-schedule.js', 'settings.css', 'ui-base.css', 'console.css'):
        html = html.replace('/assets/'+name, '/assets/'+name+'?v='+VERSION)
    return HTMLResponse(html, headers={'Cache-Control':'no-store'})


@app.get('/api/local/preferences')
def get_preferences():
    return dict(**read_preferences(), completion=read_options())


@app.post('/api/local/preferences')
def update_preferences(options: LocalPreferences):
    with app_state.lock:
        try:
            value = save_preferences(options.model_dump())
        except OSError as error:
            raise HTTPException(503, '设置未保存，请检查本机文件权限。') from error
        worker = app_state.worker
        policy = getattr(worker, 'window_policy', None)
        applied = bool(policy.apply()) if policy is not None and not app_state.history_import.running else False
        return dict(**value, window_applied=applied,
                    message='已保存，窗口偏好已应用。' if applied else '已保存，下次连接模拟器时应用。')


@app.post('/api/local/window/show')
def show_emulator_once():
    from window_policy import WindowPolicy
    policy = app_state.worker.window_policy if app_state.worker is not None else WindowPolicy()
    if not policy.show_once():
        raise HTTPException(409, '模拟器尚未启动，请先执行任务。')
    return dict(status='success')


@app.get('/api/settings')
def get_settings():
    ensure_worker()
    return dict(api_key='', model=MODEL, provider='deepseek', api_key_configured=key_configured())

@app.post('/api/settings')
def post_settings(config: ConfigModel):
    configure_key(config.api_key.get_secret_value())
    return dict(status='success')

@app.get('/api/local/provider')
def get_provider():
    return provider_metadata()

@app.get('/api/local/diagnostics')
def local_diagnostics():
    if task_running() or app_state.history_import.running:
        raise HTTPException(409,'请在任务或历史导入结束后检查运行环境。')
    from runtime_doctor import diagnose
    return diagnose(Path.cwd(),health=status(),provider=provider_metadata(),schedule=read_schedule(),
                    adb_command=getattr(app_state.worker,'_adb_command',None))

@app.get('/api/local/task-plan')
def get_task_plan():
    path = Path('config/local-task-plan.json')
    default = [name for name in ALIASES if name != '发表观点' or comment_policy()['enabled']]
    try:
        plan = TaskPlanModel(**json.loads(path.read_text('utf-8-sig')))
    except (OSError,ValueError,TypeError):
        plan = TaskPlanModel(tasklist=default)
    order = plan.task_order or list(OPTIONAL_TASKS)
    value = dict(plan.model_dump(),task_order=order,
                 tasklist=ordered_tasks(plan.tasklist,task_order=order))
    return dict(**value, available=list(ALIASES),
                experimental=[], experimental_live_verified=False)

@app.get('/api/local/comment-policy')
def get_comment_policy():
    return comment_policy()

@app.get('/api/local/points-history')
def get_points_history(month: str | None = None):
    try:
        app_state.points.record_run(current_report())  # Import the existing last report once by run ID.
        return app_state.points.month(month)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except (OSError, sqlite3.Error) as error:
        raise HTTPException(503, '积分记录暂不可用，请稍后重试。') from error

@app.get('/api/local/completion-options')
def get_completion_options():
    return read_options()


@app.get('/api/local/points-ledger')
def get_points_ledger(day: str):
    try:
        return app_state.points.ledger_day(day)
    except ValueError as error:
        raise HTTPException(422,str(error)) from error
    except (OSError,sqlite3.Error) as error:
        raise HTTPException(503,'积分流水暂不可用，请稍后重试。') from error


@app.get('/api/local/history-import')
def history_import_status():
    return app_state.history_import.snapshot()


@app.post('/api/local/history-import/skip')
def skip_history_import():
    try:
        return dict(status='success', **app_state.history_import.skip())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    except OSError as error:
        raise HTTPException(503, '选择未能保存，请稍后重试。') from error


@app.post('/api/local/history-import')
def start_history_import(options: HistoryImportOptions = HistoryImportOptions()):
    with app_state.lock:
        if task_running():
            raise HTTPException(409, '学习任务执行中，请结束后再导入历史积分。')
        if not app_state.completion.cancel():
            raise HTTPException(409, '正在关闭窗口，请重新打开控制台后再导入。')
        try:
            return dict(status='success', **app_state.history_import.start(include_details=options.include_details))
        except OSError as error:
            raise HTTPException(503, '导入状态未能保存，请检查本机磁盘后重试。') from error


@app.post('/api/local/history-import/cancel')
def cancel_history_import():
    with app_state.lock:
        try:
            cancelled = app_state.history_import.cancel()
        except OSError as error:
            raise HTTPException(503, '取消状态未能保存，请稍后查看导入结果。') from error
        if not cancelled:
            raise HTTPException(409, '当前导入已结束或正在保存，请稍后查看结果。')
        return dict(status='success', **app_state.history_import.snapshot())

@app.post('/api/local/completion-options')
def update_completion_options(options: CompletionOptions):
    with app_state.lock:
        if app_state.completion.state['state'] == 'closing':
            raise HTTPException(409, '已经开始关闭，请稍后重新打开控制台。')
        try:
            result = save_options(options.auto_close, close_emulator=options.close_emulator,
                                  close_console=options.close_console)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except OSError as error:
            raise HTTPException(503, '自动关闭设置未保存，请稍后重试。') from error
        if not result['auto_close']:
            app_state.completion.cancel()
        elif app_state.completion.state['state'] == 'pending':
            app_state.completion.arm(current_report())
        return dict(status='success', **result)

@app.post('/api/local/completion/cancel')
def cancel_completion():
    with app_state.lock:
        if not app_state.completion.cancel():
            raise HTTPException(409, '已经开始关闭，请稍后重新打开控制台。')
        return dict(status='success', message='本次保持打开，下次仍按已保存的设置执行。')

@app.get('/api/local/schedule')
def get_schedule():
    return read_schedule()

@app.post('/api/local/execution-mode')
def update_execution_mode(options: ExecutionMode):
    try:
        result=set_execution_mode(options.mode)
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        raise HTTPException(503,'执行方式未能更新，请稍后重试。') from error
    return dict(result,status='success',message=(
        '已切换为手动执行，定时启动已关闭。正在运行的任务不受影响。' if options.mode=='manual'
        else '已启用定时自动执行；修改时间后请保存。'))

def start_saved_plan(*, unattended=False, scheduled=False):
    if task_running():
        return dict(status='failed',message='已有任务正在执行，不重复启动。')
    return start_selected(TaskModel(tasklist=get_task_plan()['tasklist'],unattended=unattended), scheduled=scheduled)

@app.post('/api/local/schedule/start')
def start_scheduled(tasks: TaskModel):
    if not tasks.unattended:
        raise HTTPException(422, '定时启动须使用无人值守模式。')
    return start_selected(tasks, scheduled=True)

@app.post('/api/local/schedule/run-now')
def run_schedule_now():
    return start_saved_plan()

@app.post('/api/local/schedule')
def update_schedule(options: ScheduleOptions):
    try:
        result=save_schedule(options)
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        raise HTTPException(503,'定时计划未能保存，请稍后重试或查看后台日志。') from error
    result['status']='success'
    if result['trigger_immediately']:
        started=start_saved_plan(unattended=True,scheduled=True)
        result['immediate_status']=started['status']
        result['message']='定时已保存；当前在设置时段内，已立即执行每日任务清单。' if started['status']=='success' else '定时已保存；'+started.get('message','立即执行未启动。')
    else:
        result['message']='定时已保存；今日已启动过，下一次按明天的计划执行。' if result.get('today_started') else '定时计划已保存。'
    return result

@app.post('/api/local/task-plan')
def save_task_plan(plan: TaskPlanModel):
    with app_state.lock:
        if task_running():
            raise HTTPException(409,'请等当前任务结束后再保存定时清单。')
        if not plan.tasklist:
            raise HTTPException(400,'至少选择一项任务。')
        if '发表观点' in plan.tasklist and not comment_policy()['enabled']:
            raise HTTPException(409,'请先确认按正文生成的规则和每天发表范围，再加入定时清单。')
        # Old callers and “save basic tasks” must not reset the optional order.
        order = getattr(plan,'task_order',None) or get_task_plan()['task_order']
        value = dict(tasklist=ordered_tasks(plan.tasklist,task_order=order),task_order=order,
                     fast_answer=False,debug=False)
        path = Path('config/local-task-plan.json')
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8-sig')
        temporary.replace(path)
        return dict(status='success',**value)

@app.post('/api/local/provider')
def post_provider(config: ConfigModel):
    key = config.api_key.get_secret_value().strip()
    if not key:
        raise HTTPException(400, '请输入 DeepSeek 官网 API Key。')
    configure_key(key)
    return dict(status='success', message='已在本机加密保存。', **provider_metadata())

@app.post('/api/local/provider/test')
def validate_provider():
    if task_running():
        raise HTTPException(409, '请先中止任务，再测试接口。')
    return test_connection()

@app.get('/api/get_device')
def get_devices():
    return dict(devices=ensure_worker().get_device())

@app.post('/api/connect_device')
def connect_device(device: DeviceModel):
    with app_state.lock:
        if task_running() or app_state.history_import.running:
            raise HTTPException(409, '请先中止当前任务。')
        try:
            connected = ensure_worker().connect_device(device)
        except TaskIncomplete as exc:
            raise HTTPException(503, str(exc)) from exc
        return dict(status='success' if connected else 'failed')

def execute_tasks(tasks, daily_acceptance=False, verification_first=False, replan=False):
    previous_run = current_report().get('run_id')
    try:
        worker = app_state.worker
        worker.last_task_error = None
        if replan:
            try:
                plan = rearm_for_run(migrate_legacy=True)
                if plan and plan['enabled']:
                    worker.send_log('今日已手动启动，今日定时不再重复；已安排下次自动执行。')
            except (OSError, ValueError, subprocess.SubprocessError):
                worker.send_log('今日定时已标记跳过；下次时间暂未更新，请稍后查看执行安排。')
        if daily_acceptance:
            worker.task(tasks.tasklist, tasks.fast_answer, tasks.debug, unattended=False, daily_acceptance=True)
        elif verification_first:
            worker.task(tasks.tasklist, tasks.fast_answer, tasks.debug, unattended=tasks.unattended,verification_first=True)
        else:
            worker.task(tasks.tasklist, tasks.fast_answer, tasks.debug, unattended=tasks.unattended)
    except Exception as error:
        app_state.worker.last_task_error = type(error).__name__
        app_state.worker.send_log('任务准备失败，请检查模拟器和登录状态')
        traceback.print_exc()
    finally:
        with app_state.lock:
            report = current_report()
            try:
                saved = app_state.points.record_run(report)
                if saved and report.get('run_id') != previous_run and not app_state.worker.last_task_error:
                    app_state.completion.arm(report)
            except (OSError, sqlite3.Error):
                app_state.completion.state = dict(state='failed', message='积分记录未保存，已保留窗口，请稍后重试。')
                app_state.worker.send_log('积分记录未保存，取消本次自动关闭，保留控制台。')
            app_state.child_process = None
            app_state.current_tasks = []

@app.post('/api/start')
def start(tasks: TaskModel):
    return start_selected(tasks)

@app.post('/api/local/verify-daily-flow')
def verify_daily_flow(tasks: TaskModel):
    # Explicit one-round manual acceptance; never saved to the daily task plan.
    if tasks.tasklist != ['每日答题'] or tasks.unattended:
        raise HTTPException(422, '单组验收仅支持手动运行每日答题')
    return start_selected(TaskModel(tasklist=['每日答题']), daily_acceptance=True)

@app.post('/api/local/verify-full-flow')
def verify_full_flow(tasks: TaskModel):
    if set(tasks.tasklist)!=set(ALIASES):
        raise HTTPException(422, '完整流程验收需要保留全部八项任务')
    return start_selected(tasks,verification_first=True)

def start_selected(tasks: TaskModel, daily_acceptance=False, verification_first=False, scheduled=False):
    with app_state.lock:
        if scheduled and daily_started_today():
            return dict(status='skipped', message='今日已启动过，跳过本次定时执行；未完成任务可手动继续。')
        if app_state.history_import.running:
            return dict(status='failed', message='正在导入历史积分，请等导入结束后再执行学习任务。')
        worker = ensure_worker()
        if task_running():
            return dict(status='failed', message='任务已开始')
        if not app_state.completion.cancel():
            return dict(status='failed', message='正在关闭本轮窗口，请稍后重新打开控制台。')
        if not tasks.tasklist:
            return dict(status='failed', message='请先选择任务')
        if '发表观点' in tasks.tasklist and not comment_policy()['enabled']:
            return dict(status='failed', message='发表观点尚待确认按正文生成的规则和每日使用范围，未启动发表。')
        if any(name in tasks.tasklist for name in ('每日答题','趣味答题','发表观点')) and not key_configured():
            return dict(status='failed', message='答题或观点草稿任务需要先配置 DeepSeek 密钥。')
        app_state.completion.state = dict(state='idle')
        tasks.tasklist = list(dict.fromkeys(tasks.tasklist))
        worker.stop_flag = False
        worker.pause_flag = False
        worker._manual_pause_requested = False
        worker._manual_hold = False
        worker._verification_wait_active = False
        worker.last_task_error = None
        app_state.current_tasks = tasks.tasklist
        args = (tasks, True) if daily_acceptance else (tasks,False,True) if verification_first else (tasks,)
        thread = threading.Thread(target=execute_tasks, args=args, daemon=True,
                                  kwargs={'replan':not (scheduled or daily_acceptance or verification_first)})
        claim = {} if daily_acceptance or verification_first else claim_daily_run(scheduled=scheduled)
        if claim is None:
            return dict(status='skipped', message='今日已启动过，跳过本次定时执行。')
        app_state.child_process = thread
        try:
            thread.start()
        except Exception:
            app_state.child_process = None
            app_state.current_tasks = []
            release_daily_claim(claim)
            raise
        return dict(status='success')

@app.post('/api/stop')
def stop():
    with app_state.lock:
        if not task_running():
            return dict(status='failed', message='任务未开始')
        app_state.worker.request_stop()
        return dict(status='success')

@app.post('/api/continue')
def resume():
    with app_state.lock:
        if task_running():
            worker=app_state.worker
            if not worker.pause_flag and not getattr(worker,'_manual_pause_requested',False):
                return dict(status='failed',message='任务正在执行，无需重复继续。')
            waiting=worker.request_continue()
            return dict(status='success',waiting_for_owner=waiting,
                message='请在模拟器完成验证；识别到正常页面后会继续。' if waiting else '已继续原任务。')
        report=current_report()
        tasks=continuation_tasks(report)
        if not tasks:
            return dict(status='failed',message='没有待继续的任务，请先选择任务并执行。')
        return start_selected(TaskModel(tasklist=tasks))

def current_report():
    report=getattr(app_state.worker,'run_report',None)
    value=report.snapshot() if report else read_report()
    return value if isinstance(value,dict) else {}

def continuation_tasks(report):
    if report.get('status') not in ('stopped','partial','failed','needs_human','paused','running'):
        return []
    tasks=report.get('tasks',{})
    if not isinstance(tasks,dict):
        return []
    selected=report.get('selected_tasks',[])
    selected=selected if isinstance(selected,list) else []
    # Explicit run order survives reloaded/sorted JSON and later plan edits.
    names=[name for name in selected if isinstance(name,str) and name in tasks and name in ALIASES]
    names.extend(name for name in tasks if name in ALIASES and name not in names)
    return ordered_tasks(names)

@app.post('/api/pause')
def pause_tasks():
    with app_state.lock:
        if not task_running():
            return dict(status='failed',message='当前没有正在执行的任务。')
        app_state.worker.request_pause()
        return dict(status='success',message='已请求暂停，当前操作结束后暂停。')

@app.get('/api/status')
def status():
    report = current_report()
    running = task_running()
    paused = bool(app_state.worker and app_state.worker.pause_flag)
    requested = bool(getattr(app_state.worker,'_manual_pause_requested',False))
    return dict(status='running', pid=os.getpid(), backend='deepseek-local',
                local_version=VERSION, run_summary=report,
                task_running=running, connected=bool(app_state.worker and app_state.worker.connection_alive()),
                current_tasks=app_state.current_tasks,
                last_error=getattr(app_state.worker, 'last_task_error', None),
                paused=paused, pause_requested=requested,
                manual_paused=paused and bool(getattr(app_state.worker,'_manual_hold',False)),
                can_continue=(paused or requested) if running else bool(continuation_tasks(report)),
                provider='deepseek', model=MODEL, api_key_configured=key_configured(),
                completion_action=app_state.completion.snapshot(),
                history_import=app_state.history_import.snapshot())

@app.websocket('/api/ws')
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    structured=websocket.query_params.get('v')=='2'
    cursor = -1
    if structured:
        if websocket.query_params.get('session')==app_state.log_session:
            try:cursor=max(-1,int(websocket.query_params.get('after','-1')))
            except ValueError:pass
        await websocket.send_json(dict(kind='hello',session=app_state.log_session))
    try:
        while not app_state.shutting_down:
            for sequence, message in list(app_state.history_message):
                if sequence > cursor:
                    if structured:
                        await websocket.send_json(dict(session=app_state.log_session,sequence=sequence,message=message))
                    else:await websocket.send_text(message)
                    cursor = sequence
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=.1)
                if message['type'] == 'websocket.disconnect':
                    return
            except asyncio.TimeoutError:
                pass
    except (WebSocketDisconnect, RuntimeError):
        return

def request_server_shutdown(server):
    app_state.shutting_down = True
    server.should_exit = True

if __name__ == '__main__':
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8000, timeout_graceful_shutdown=8))
    app_state.shutdown = lambda: request_server_shutdown(server)
    server.run()
