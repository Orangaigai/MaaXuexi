"""Read the actual Windows task; do not present a stale asset as live status."""
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


def schedule_display(value):
    start = datetime.fromisoformat(value['start_boundary'])
    china = timezone(timedelta(hours=8))
    start = start.replace(tzinfo=china) if start.tzinfo is None else start.astimezone(china)
    seconds = value['random_delay_seconds']
    if type(seconds) is not int or not 0 <= seconds < 24*60*60:
        raise ValueError('Invalid random delay')
    end = start + timedelta(seconds=seconds)
    return dict(value, mode='random_window' if seconds else 'fixed',
                window_start=start.strftime('%H:%M'), window_end=end.strftime('%H:%M'),
                time=start.strftime('%H:%M'), timezone='Asia/Shanghai',
                requires_windows_login=True)


def read_schedule():
    root = Path(__file__).resolve().parents[1]
    powershell = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
            'Bypass', '-File', str(root/'scripts/Schedule-MaaXuexi.ps1')],
            capture_output=True, text=True, encoding='utf-8-sig', errors='replace',
            timeout=15, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return schedule_display(json.loads(result.stdout))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return dict(available=False, enabled=None, timezone='Asia/Shanghai',
                    message='Windows 定时状态暂未读到，请稍后刷新；未将旧配置当作当前计划。')
