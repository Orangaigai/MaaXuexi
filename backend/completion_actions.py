"""Arm resource cleanup only for a newly finished, durably recorded run."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import os
import subprocess
import threading
import time

OPTIONS_PATH = Path('config/completion-options.json')
COUNTDOWN_SECONDS = 30


def read_options():
    try:
        value = json.loads(OPTIONS_PATH.read_text('utf-8-sig'))
        return dict(auto_close=value['auto_close']) if type(value.get('auto_close')) is bool else dict(auto_close=True)
    except (OSError, ValueError, TypeError, AttributeError):
        return dict(auto_close=True)


def save_options(auto_close):
    if type(auto_close) is not bool:
        raise ValueError('请选择是否自动关闭。')
    OPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = OPTIONS_PATH.with_suffix('.tmp')
    temp.write_text(json.dumps(dict(auto_close=auto_close)), 'utf-8')
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
            if not read_options()['auto_close'] or not completed_run(report):
                return
            self.deadline = time.monotonic() + COUNTDOWN_SECONDS
            closes_at = datetime.now(timezone.utc) + timedelta(seconds=COUNTDOWN_SECONDS)
            self.state = dict(state='pending', run_id=report['run_id'], closes_at=closes_at.isoformat())

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
                    self.process = subprocess.Popen([
                        str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'),
                        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                        '-RunId', self.state['run_id'], '-BackendPid', str(os.getpid())],
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
                    shutdown()
                else:
                    self.state.update(state='failed', message='自动关闭未完成，记录已保存，可手动关闭。')
