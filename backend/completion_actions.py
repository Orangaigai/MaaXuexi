"""Arm resource cleanup only for a newly finished, durably recorded run."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import os
import subprocess
import threading
import time

OPTIONS_PATH = Path('config/completion-options.json')
# record_run has committed before arm(); the next poll may close immediately.
COUNTDOWN_SECONDS = 0


def read_options():
    try:
        value = json.loads(OPTIONS_PATH.read_text('utf-8-sig'))
    except (OSError, ValueError, TypeError, AttributeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    legacy = value.get('auto_close', True)
    legacy = legacy if type(legacy) is bool else True
    flags = {name:value[name] if type(value.get(name)) is bool else legacy
             for name in ('close_emulator', 'close_console')}
    return dict(auto_close=any(flags.values()), **flags)


def save_options(auto_close=None, *, close_emulator=None, close_console=None):
    changes = dict(close_emulator=close_emulator, close_console=close_console)
    if auto_close is not None and (type(auto_close) is not bool or any(v is not None for v in changes.values())):
        raise ValueError('请单独保存关闭选项。')
    if auto_close is not None:
        changes = dict.fromkeys(changes, auto_close)
    if not any(v is not None for v in changes.values()) or any(v is not None and type(v) is not bool for v in changes.values()):
        raise ValueError('请选择是否关闭模拟器或控制台。')
    value = read_options()
    value.update({name:flag for name,flag in changes.items() if flag is not None})
    value['auto_close'] = value['close_emulator'] or value['close_console']
    OPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = OPTIONS_PATH.with_suffix('.tmp')
    temp.write_text(json.dumps(value), 'utf-8')
    temp.replace(OPTIONS_PATH)
    return read_options()


def completed_run(report):
    return (isinstance(report, dict) and report.get('status') == 'completed'
            and report.get('finished_at') and report.get('run_id') and report.get('tasks')
            and all(row.get('state') in ('verified', 'skipped_full', 'credited', 'skipped_mode')
                    for row in report['tasks'].values()) and not report.get('acceptance_test'))


class CompletionActions:
    def __init__(self):
        self.lock = threading.RLock()
        self.state = dict(state='idle')
        self.deadline = None
        self.process = None
        self.output = None

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def cancel(self):
        with self.lock:
            if self.state['state'] == 'closing':
                return False
            if self.state['state'] == 'pending':
                self.state = dict(state='cancelled', run_id=self.state['run_id'])
                self.deadline = None
            return True

    def arm(self, report):
        with self.lock:
            options = read_options()
            if not options['auto_close'] or not completed_run(report):
                return
            self.deadline = time.monotonic() + COUNTDOWN_SECONDS
            closes_at = datetime.now(timezone.utc) + timedelta(seconds=COUNTDOWN_SECONDS)
            self.state = dict(state='pending', run_id=report['run_id'], closes_at=closes_at.isoformat(),
                              close_emulator=options['close_emulator'], close_console=options['close_console'])

    def poll(self, report, *, running, shutdown):
        with self.lock:
            if self.state['state'] == 'pending':
                if running or report.get('run_id') != self.state['run_id'] or not completed_run(report):
                    self.cancel()
                    return
                if time.monotonic() < self.deadline:
                    return
                root = Path.cwd().resolve()
                script = root/'scripts/Finish-Session.ps1'
                try:
                    log = root/'logs'/('close-'+self.state['run_id']+'.log')
                    log.parent.mkdir(parents=True, exist_ok=True)
                    self.output = log.open('ab')
                    arguments = [
                        str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),
                        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                        '-RunId', self.state['run_id'], '-BackendPid', str(os.getpid())]
                    if not self.state['close_emulator']:
                        arguments.append('-KeepEmulator')
                    if not self.state['close_console']:
                        arguments.append('-KeepConsole')
                    self.process = subprocess.Popen(arguments,
                        stdout=self.output, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    self.deadline = time.monotonic() + 90
                    self.state.update(state='closing')
                except (OSError, KeyError):
                    if self.output:
                        self.output.close()
                    self.state.update(state='failed', message='自动关闭未完成，记录已保存，可手动关闭。')
            elif self.state['state'] == 'closing':
                code = self.process.poll()
                if code is None:
                    if time.monotonic() >= self.deadline:
                        self.process.kill()  # Only the helper created by this instance.
                        self.output.close()
                        self.state.update(state='failed', message='自动关闭超时，记录已保存，可手动关闭。')
                    return
                self.output.close()
                if code == 0:
                    self.state.update(state='closed')
                    if self.state['close_console']:
                        shutdown()
                else:
                    self.state.update(state='failed', message='自动关闭未完成，记录已保存，可手动关闭。')
