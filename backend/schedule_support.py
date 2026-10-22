"""Persist editable plans and give Windows an exact, once-picked daily time."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictStr, model_validator

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT/'config/local-schedule.json'
CHINA = timezone(timedelta(hours=8))
_LOCK = threading.RLock()


class ScheduleOptions(BaseModel):
    mode: Literal['fixed', 'random_window'] = 'random_window'
    time: StrictStr = '09:00'
    window_start: StrictStr = '08:00'
    window_end: StrictStr = '09:00'
    model_config = ConfigDict(extra='forbid')

    @model_validator(mode='after')
    def valid_times(self):
        for value in (self.time, self.window_start, self.window_end):
            if re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value) is None:
                raise ValueError('时间请使用 00:00～23:59 格式')
        if self.mode == 'random_window' and self.window_end <= self.window_start:
            raise ValueError('结束时间须晚于开始时间；请设置同一天内的时间段')
        return self


def now_china():
    return datetime.now(CHINA)


def at_clock(day, value):
    hour, minute = map(int, value.split(':'))
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def plan_next(options, now=None, *, tomorrow=False):
    """Saving inside a window runs now; its next automatic run is tomorrow."""
    now = (now or now_china()).astimezone(CHINA)
    start = at_clock(now, options.window_start if options.mode=='random_window' else options.time)
    end = at_clock(now, options.window_end) if options.mode=='random_window' else start+timedelta(minutes=1)
    immediate = not tomorrow and start <= now < end
    if tomorrow or now >= start:
        start += timedelta(days=1)
    if options.mode == 'random_window':
        seconds = int((at_clock(start, options.window_end)-start).total_seconds())
        start += timedelta(seconds=secrets.randbelow(seconds))
    return start, immediate


def _native(*args):
    ps = Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(ps),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(ROOT/'scripts/Schedule-MaaXuexi.ps1'),*args],capture_output=True,
        text=True,encoding='utf-8-sig',errors='replace',timeout=20,check=True,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return json.loads(result.stdout)


def _load_options():
    if not CONFIG_PATH.exists():
        return None
    value = json.loads(CONFIG_PATH.read_text('utf-8-sig'))
    return ScheduleOptions(**value['options'])


def _daily_state_path():
    return CONFIG_PATH.with_name('daily-run-state.json')


def daily_started_today(now=None):
    """A started attempt consumes automation for its Beijing day, not manual retries."""
    path = _daily_state_path()
    if not path.exists():
        return False
    value = json.loads(path.read_text('utf-8-sig'))
    return value.get('day') == (now or now_china()).astimezone(CHINA).date().isoformat()


def claim_daily_run(*, scheduled=False):
    with _schedule_lock():
        if daily_started_today():
            return None if scheduled else {}
        stamp = now_china().astimezone(CHINA)
        value = dict(day=stamp.date().isoformat(), started_at=stamp.isoformat(timespec='seconds'),
                     source='scheduled' if scheduled else 'manual', token=uuid.uuid4().hex)
        path = _daily_state_path()
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value), 'utf-8')
        temporary.replace(path)
        return value


def release_daily_claim(value):
    """Roll back only a request whose worker thread could not start."""
    if not value:
        return
    with _schedule_lock():
        path = _daily_state_path()
        if path.exists() and json.loads(path.read_text('utf-8-sig')) == value:
            path.unlink()


@contextmanager
def _schedule_lock():
    # Both the server and hidden daily launcher can replan a task. Windows
    # byte-range locks release automatically if either process exits.
    with _LOCK:
        if os.name!='nt':
            yield
            return
        import msvcrt
        CONFIG_PATH.parent.mkdir(parents=True,exist_ok=True)
        with CONFIG_PATH.with_suffix('.lock').open('a+b') as stream:
            if stream.tell()==0:
                stream.write(b'0');stream.flush()
            deadline=time.monotonic()+10
            while True:
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                    break
                except OSError:
                    if time.monotonic()>=deadline:
                        raise TimeoutError('定时计划正在更新，请稍后再试') from None
                    time.sleep(.1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


def schedule_display(value, options=None, now=None):
    now=(now or now_china()).astimezone(CHINA)
    start=datetime.fromisoformat(value['start_boundary'])
    start=start.replace(tzinfo=CHINA) if start.tzinfo is None else start.astimezone(CHINA)
    seconds=value['random_delay_seconds']
    if type(seconds) is not int or not 0<=seconds<86400:
        raise ValueError('Invalid random delay')
    end=start+timedelta(seconds=seconds)
    # Compatibility for the previous native RandomDelay plan before migration.
    fields=options.model_dump() if options else dict(mode='random_window' if seconds else 'fixed',
        time=start.strftime('%H:%M'),window_start=start.strftime('%H:%M'),window_end=end.strftime('%H:%M'))
    next_run=value.get('next_base_run') if value.get('enabled') else None
    next_at=datetime.fromisoformat(next_run) if next_run else None
    if next_at is not None:
        next_at=next_at.replace(tzinfo=CHINA) if next_at.tzinfo is None else next_at.astimezone(CHINA)
    return dict(value,**fields,execution_mode='automatic' if value['enabled'] else 'manual',
        today_started=daily_started_today(now),
        timezone='Asia/Shanghai',requires_windows_login=True,
        server_now=now.isoformat(timespec='seconds'),next_run_at=next_at.isoformat(timespec='seconds') if next_at else None,
        seconds_until_next_run=max(0,math.ceil((next_at-now).total_seconds())) if next_at else None,
        defaults=ScheduleOptions().model_dump())


def read_schedule():
    try:
        with _schedule_lock():
            return schedule_display(_native(),_load_options())
    except (OSError,subprocess.SubprocessError,ValueError,KeyError,TypeError):
        return dict(available=False,enabled=None,timezone='Asia/Shanghai',
            defaults=ScheduleOptions().model_dump(),message='定时状态暂未读到，请稍后刷新；未将旧配置当作当前计划。')


def _apply_plan(options, next_at):
    temporary=CONFIG_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(options=options.model_dump(),
        planned_at=next_at.isoformat(timespec='seconds'),saved_at=now_china().isoformat(timespec='seconds')),
        ensure_ascii=False,indent=2),'utf-8')
    try:
        native=_native('-Apply','-StartAt',next_at.isoformat(timespec='seconds'))
        applied=datetime.fromisoformat(native['start_boundary'])
        if applied!=next_at or native['random_delay_seconds']!=0 or not native['enabled']:
            raise ValueError('Windows 未确认新的定时时刻')
        temporary.replace(CONFIG_PATH)
    finally:
        temporary.unlink(missing_ok=True)
    return schedule_display(native,options)


def save_schedule(options):
    with _schedule_lock():
        next_at,immediate=plan_next(options,tomorrow=daily_started_today())
        return dict(_apply_plan(options,next_at),trigger_immediately=immediate)


def rearm_for_run():
    """Rearm before learning, so tomorrow stays scheduled if learning fails."""
    with _schedule_lock():
        options=_load_options()
        native=_native()
        if not native['enabled']:
            return schedule_display(native,options)
        if options is None:
            return None
        next_at,_=plan_next(options,tomorrow=True)
        return _apply_plan(options,next_at)


def set_execution_mode(mode):
    """Use the native task as the source of truth, including queued launches."""
    if mode not in ('manual','automatic'):
        raise ValueError('Unknown execution mode')
    with _schedule_lock():
        options=_load_options()
        native=_native()
        if mode=='manual':
            if native['enabled']:
                native=_native('-Disable')
            if native['enabled']:
                raise ValueError('Windows 未确认关闭定时启动')
            return schedule_display(native,options)
        if native['enabled']:
            return schedule_display(native,options)
        if options is None:
            options=ScheduleOptions()
        next_at,_=plan_next(options,tomorrow=daily_started_today())
        # Enabling schedules a future run; only an explicit Save can run now.
        return _apply_plan(options,next_at)
