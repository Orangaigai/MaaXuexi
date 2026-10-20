"""Control only the configured MuMu window. Never send desktop mouse/keyboard input."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import threading

from local_preferences import read_preferences

NO_ACTIVATE = 0x0010 | 0x0001 | 0x0002 | 0x0200 | 0x4000


class NativeWindows:
    def __init__(self):
        if os.name != 'nt':
            raise OSError('窗口显示设置仅适用于 Windows。')
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        self.api.IsWindow.argtypes = [wintypes.HWND]
        self.api.IsWindow.restype = wintypes.BOOL
        self.api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.api.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.api.GetAncestor.restype = wintypes.HWND
        self.api.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
        self.api.ShowWindowAsync.restype = wintypes.BOOL
        self.api.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.api.SetWindowPos.restype = wintypes.BOOL
        self.api.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.api.GetWindowLongW.restype = wintypes.LONG

    def valid(self, hwnd, pid):
        owner = wintypes.DWORD()
        return (bool(self.api.IsWindow(hwnd)) and self.api.GetAncestor(hwnd, 2) == hwnd
                and bool(self.api.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))) and owner.value == pid)

    def show(self, hwnd, state):
        if not self.api.ShowWindowAsync(hwnd, state):
            raise OSError('MuMu 窗口未响应显示请求。')

    def order(self, hwnd, after):
        if not self.api.SetWindowPos(hwnd, after, 0, 0, 0, 0, NO_ACTIVATE):
            raise OSError('MuMu 窗口层级设置失败。')

    def release(self, hwnd):
        if self.api.GetWindowLongW(hwnd, -20) & 8:
            self.order(hwnd, -2)


def emulator_info():
    config = json.loads(Path('config/emulator.json').read_text('utf-8-sig'))
    cli = Path(config['mumu_cli'])
    index = str(int(config['vm_index']))
    if not cli.is_file():
        raise OSError('未找到 MuMu 程序。')
    result = subprocess.run([str(cli), 'info', '-v', index], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=3,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise OSError('暂时无法读取 MuMu 窗口。')
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError('MuMu 窗口信息格式不正确。')
    return value.get(index, value)


class WindowPolicy:
    def __init__(self, native=None):
        self.native = native
        self.lock = threading.RLock()
        self.waiting = False
        self.error = None

    def _target(self, info=None):
        info = emulator_info() if info is None else info
        if not isinstance(info, dict) or not info.get('is_process_started'):
            return None
        hwnd = info.get('main_wnd')
        hwnd = int(hwnd, 16) if isinstance(hwnd, str) else int(hwnd or 0)
        pid = int(info.get('pid') or 0)
        if not hwnd or not pid:
            return None
        if self.native is None:
            self.native = NativeWindows()
        if not self.native.valid(hwnd, pid):
            raise OSError('MuMu 窗口归属已变化，请重新连接。')
        return hwnd

    def apply(self, info=None):
        with self.lock:
            try:
                hwnd = self._target(info)
                if hwnd is None:
                    return False
                # A settings save must not cover a challenge the user is handling.
                if self.waiting:
                    return True
                mode = read_preferences()['window_mode']
                if mode == 'background':
                    self.native.release(hwnd)
                    self.native.show(hwnd, 7)  # SW_SHOWMINNOACTIVE
                elif mode == 'bottom':
                    self.native.show(hwnd, 4)
                    self.native.order(hwnd, 1)
                elif mode == 'topmost':
                    self.native.show(hwnd, 4)
                    self.native.order(hwnd, -1)
                else:
                    self.native.release(hwnd)
                self.error = None
                return True
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
                self.error = str(error)
                return False

    def attention(self):
        with self.lock:
            if self.waiting:
                return False
            self.waiting = True
            if not read_preferences()['show_on_attention']:
                return False
            return self.show_once()

    def show_once(self):
        with self.lock:
            try:
                hwnd = self._target()
                if hwnd is None:
                    return False
                self.native.show(hwnd, 4)  # Restore without changing keyboard focus.
                self.native.order(hwnd, -1)
                # Release immediately: the user's next window click can cover MuMu.
                self.native.order(hwnd, -2)
                self.error = None
                return True
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
                self.error = str(error)
                return False

    def resume(self):
        with self.lock:
            if not self.waiting:
                return
            self.waiting = False
            self.apply()


if __name__ == '__main__':
    # Shared by the PowerShell launcher; never invokes MuMu's focus-stealing show_window.
    raise SystemExit(0 if WindowPolicy().apply() else 1)
