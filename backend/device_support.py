"""Bounded MuMu startup and live ADB checks. No Android UI input here."""
import json
import subprocess
import time
from pathlib import Path
from task_support import TaskIncomplete


def command(args, timeout=8):
    return subprocess.run([str(x) for x in args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def online(adb_command):
    if not adb_command:
        return False
    try:
        result = command([*adb_command, "get-state"], timeout=3)
        return result.returncode == 0 and result.stdout.strip() == "device"
    except (OSError, subprocess.SubprocessError):
        return False


def prepare_emulator(progress, check_active, timeout=600, *, window_policy=None):
    config_path = Path("config/emulator.json")
    try:
        config = json.loads(config_path.read_text("utf-8-sig"))
        cli, adb = Path(config["mumu_cli"]), Path(config["adb_path"])
        index = str(int(config["vm_index"]))
        if not cli.is_file() or not adb.is_file():
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise TaskIncomplete("模拟器配置缺失，请检查 config/emulator.json") from None
    deadline = time.monotonic() + timeout
    launched = False
    applied_window = None
    last_progress = -15
    started = time.monotonic()
    progress("正在检查模拟器并连接设备")
    while time.monotonic() < deadline:
        check_active()
        try:
            query = command([cli, "info", "-v", index])
            info = json.loads(query.stdout)
            info = info.get(index, info)
            if query.returncode != 0 or not isinstance(info, dict):
                raise ValueError()
        except (ValueError, OSError, subprocess.SubprocessError):
            elapsed = time.monotonic() - started
            if elapsed - last_progress >= 15:
                progress("MuMu 启动状态暂时不可读，继续等待本机接口恢复")
                last_progress = elapsed
            time.sleep(0.5)
            continue
        # Apply as soon as a new native window exists, including during a cold boot.
        identity = (info.get('main_wnd'), info.get('pid'))
        if identity[0] and identity != applied_window:
            if window_policy is None:
                from window_policy import WindowPolicy
                window_policy = WindowPolicy()
            if window_policy.apply(info):
                applied_window = identity
        if not info.get("is_process_started") and not launched:
            progress("正在启动模拟器，等待 Android 就绪")
            launched = True
            try:
                if command([cli, "control", "-v", index, "launch"]).returncode != 0:
                    raise TaskIncomplete("MuMu 启动失败，请查看模拟器提示")
            except subprocess.TimeoutExpired:
                progress("MuMu启动命令尚未返回，继续核对实际Android/ADB启动状态，不重复启动")
        elapsed = time.monotonic() - started
        if elapsed - last_progress >= 15:
            progress(f"等待模拟器实际启动完成（{int(elapsed)} 秒，上限 {timeout} 秒）")
            last_progress = elapsed
        # ADB + sys.boot_completed are authoritative even if the CLI flag lags.
        if info.get("adb_port"):
            host = info.get("adb_host_ip") or "127.0.0.1"
            if host not in ("127.0.0.1", "localhost"):
                raise TaskIncomplete("模拟器没有提供本机连接地址")
            address = f"{host}:{int(info['adb_port'])}"
            base = (str(adb), "-s", address)
            # adb connect can exit zero even when the connection failed.
            try:
                command([adb, "connect", address])
                boot = (command([*base, "shell", "getprop", "sys.boot_completed"])
                        if online(base) else None)
            except (OSError, subprocess.SubprocessError):
                # Android may advertise its port before ADB accepts commands.
                # Stay inside the existing bounded boot wait instead of aborting.
                boot = None
            if boot is not None:
                if boot.returncode == 0 and boot.stdout.strip() == "1":
                    check_active()
                    # ADB/MuMu capture is independent of desktop focus. Do not show_window.
                    if window_policy is None:
                        from window_policy import WindowPolicy
                        window_policy = WindowPolicy()
                    if identity != applied_window and not window_policy.apply(info):
                        if not online(base):
                            raise TaskIncomplete("应用窗口偏好时设备连接已中断")
                        progress("窗口偏好暂未应用，但Android/ADB仍在线；继续连接现有设备")
                    check_active()
                    return str(adb), address
        time.sleep(0.5)
    raise TaskIncomplete("等待模拟器连接超时，请检查 MuMu 启动提示后重新执行")
