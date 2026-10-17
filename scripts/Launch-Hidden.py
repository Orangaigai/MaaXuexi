"""GUI entry point: keep startup and scheduled monitoring out of the terminal."""
import argparse
import ctypes
from datetime import datetime
import os
from pathlib import Path
import subprocess


def show_failure(log_path):
    ctypes.windll.user32.MessageBoxW(
        None, '启动未成功，请查看日志：\n' + str(log_path), 'MaaXuexi 启动提示', 0x10)


def run_launcher(root, *, daily=False, check_only=False, no_browser=False, notify=True):
    root = Path(root).resolve()
    log_root = root / 'logs'
    log_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    log_path = log_root / f'launcher-{stamp}-{os.getpid()}.log'
    script = root / 'scripts' / ('Run-Daily.ps1' if daily or check_only else 'Start-MaaXuexi.ps1')
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = [str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
               'Bypass', '-File', str(script)]
    if check_only:
        command.append('-CheckOnly')
    if no_browser:
        command.append('-NoBrowser')
    with log_path.open('xb') as log:
        log.write(f'MaaXuexi hidden launcher: {script.name}\r\n'.encode('utf-8'))
        log.flush()
        try:
            result = subprocess.run(command, cwd=root, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            code = result.returncode
        except OSError as error:
            log.write(('Launcher failed: ' + str(error) + '\r\n').encode('utf-8'))
            code = 1
        log.write(f'\r\nLauncher exit code: {code}\r\n'.encode('utf-8'))
    # Daily codes 2 and 3 already have task reports/notifications; no modal loop.
    if notify and code != 0 and not (daily and code in (2, 3)):
        show_failure(log_path)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--daily', action='store_true')
    mode.add_argument('--check', action='store_true', dest='check_only')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    return run_launcher(Path(__file__).resolve().parents[1], **vars(args))


if __name__ == '__main__':
    raise SystemExit(main())
