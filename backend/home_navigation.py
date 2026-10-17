"""Observe launch completion before input; preserve verification screens."""
import json
import re
import subprocess
import time
from pathlib import Path
from PIL import Image
from task_support import TaskIncomplete, now


def home_ready(records):
    channels = set()
    for row in records:
        box = row.get('box', [])
        if len(box) != 4 or not 85 <= box[1] <= 190:
            continue
        text = re.sub(r'\s+', '', row.get('text', ''))
        if text in ('推荐', '综合', '要闻', '思想', '发现', '本地', '乡村阅读'):
            channels.add(text)
    return bool(channels & {'推荐', '综合'}) and len(channels) >= 3


def login_required(records):
    texts = [re.sub(r'\s+', '', row.get('text', '')) for row in records]
    return any(re.fullmatch(r'手机号登录|账号密码登录|验证码登录|请输入手机号|请输入手机号码|登录/注册|请先登录', text)
               for text in texts)


def splash_skip(records):
    candidates = []
    for row in records:
        box = row.get('box', [])
        text = re.sub(r'\s+', '', row.get('text', ''))
        if len(box) == 4 and box[0] >= 480 and 30 <= box[1] <= 240 and re.fullmatch(r'(?:\d+[sS秒]?)?跳过(?:广告)?(?:\d+[sS秒]?)?', text):
            candidates.append(box)
    return candidates[0] if len(candidates) == 1 else None


def notification_deny(records):
    """Only the observed Android POST_NOTIFICATIONS prompt, never other permissions."""
    texts = [re.sub(r'\s+', '', row.get('text', '')) for row in records]
    if not any('要允许' in text and '学习强国' in text and '发送通知' in text for text in texts):
        return None
    buttons = [row['box'] for row, text in zip(records, texts)
               if text == '不允许' and len(row.get('box', [])) == 4
               and 200 <= row['box'][0] <= 520 and 550 <= row['box'][1] <= 1100]
    return buttons[0] if len(buttons) == 1 else None


def recent_media_close(records):
    """Recognize the observed draggable 420px recent-media panel from two anchors."""
    markers = [row['box'] for row in records if row.get('text') == '最近学习'
               and len(row.get('box', [])) == 4 and 15 <= row['box'][0] <= 40
               and 120 <= row['box'][1] <= 1130]
    if len(markers) != 1:
        return None
    marker = markers[0]
    timers = [row for row in records if re.fullmatch(r'\d{1,2}:\d{2}/\d{1,2}:\d{2}', row.get('text', ''))
              and len(row.get('box', [])) == 4 and 70 <= row['box'][0] <= 200
              and 50 <= row['box'][1] - marker[1] <= 90]
    return (408, marker[1] + 65) if len(timers) == 1 else None


class HomeNavigation:
    def _dismiss_recent_media(self):
        records = self._screen_records()
        self._guard_verification(records)
        point = recent_media_close(records)
        if not point or not self._app_in_foreground():
            return
        self._check_active()
        if not self.tasker.controller.post_click(*point).wait().succeeded:
            raise TaskIncomplete('积分页浮动播放器关闭失败')
        self._sleep(0.5)
        refreshed = self._screen_records()
        self._guard_verification(refreshed)
        if recent_media_close(refreshed):
            raise TaskIncomplete('积分页浮动播放器关闭未确认，未继续翻页')
        self.send_log('已关闭明确识别的最近学习浮动播放器，避免遮挡积分或拖走滑动')

    def _deny_launch_notification(self, records):
        box = notification_deny(records)
        if not box:
            return False
        command = getattr(self, '_adb_command', None)
        if not command:
            return False
        try:
            output = subprocess.run([*command, 'shell', 'dumpsys', 'activity', 'activities'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', check=True,
                timeout=8, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        except (OSError, subprocess.SubprocessError):
            raise TaskIncomplete('系统弹窗来源无法确认，未点击权限按钮') from None
        system_dialog = any(re.search(r'(?:topResumedActivity|mResumedActivity)=', line) and
            re.search(r'com\.(?:google\.)?android\.permissioncontroller/', line) for line in output.splitlines())
        if not system_dialog:
            return False
        self._check_active()
        self._guard_verification(records)
        x, y, w, h = box
        if not self.tasker.controller.post_click(x+w//2, y+h//2).wait().succeeded:
            raise TaskIncomplete('无法拒绝学习强国系统通知申请')
        self.send_log('已识别 Android 系统通知申请，选择不允许，继续恢复首页')
        return True

    def _launch_records(self):
        self._check_active()
        records = self._screen_records()
        image = self.tasker.controller.cached_image
        self._guard_verification(records)
        if image is not None and image.shape[:2] != (1280, 720):
            # MuMu's desktop/cold-boot frame can be landscape. It must not be
            # clicked or accepted as home; app launch/orientation may still settle.
            return []
        if login_required(records):
            self._wait_for_owner('学习强国需要本人登录；请在模拟器内完成登录')
            return []
        return records

    def _wait_home_ready(self, timeout=120, notification_dismissed=False):
        started = time.monotonic()
        deadline = started + timeout
        stable = 0
        skipped = False
        last_log = -15
        samples = []
        while time.monotonic() < deadline:
            self._check_active()
            records = self._launch_records()
            elapsed = time.monotonic() - started
            ready = home_ready(records)
            samples.append(dict(seconds=round(elapsed, 2), home=ready,
                                texts=[row['text'] for row in records]))
            samples = samples[-12:]
            stable = stable + 1 if ready else 0
            if stable >= 2:
                self.send_log(f'学习强国首页已连续确认，等待 {elapsed:.1f} 秒')
                self._home_launch_metrics = dict(wait_seconds=round(elapsed, 2), samples=samples, home_confirmed=True)
                report = getattr(self, 'run_report', None)
                if report:
                    report.update(phase='学习强国首页已确认，继续导航')
                return
            if elapsed - last_log >= 15:
                self.send_log(f'正在等待学习强国开屏和首页加载（已等待 {int(elapsed)} 秒，上限 {timeout} 秒）')
                report = getattr(self, 'run_report', None)
                if report:
                    report.update(phase=f'等待学习强国首页加载：{int(elapsed)} 秒')
                last_log = elapsed
            if not notification_dismissed and self._deny_launch_notification(records):
                notification_dismissed = True
                self._sleep(0.5)
                continue
            skip = splash_skip(records) if not ready else None
            if skip and not skipped:
                x, y, w, h = skip
                if not self.tasker.controller.post_click(x+w//2, y+h//2).wait().succeeded:
                    raise TaskIncomplete('开屏跳过按钮点击失败')
                self.send_log('已点击明确识别到的开屏跳过按钮，继续等待首页')
                skipped = True
            self._sleep(1)
        self._save_launch_failure(samples)
        raise TaskIncomplete(f'等待学习强国首页超过 {timeout} 秒；启动画面/弹窗/网络未恢复，现场已保留')

    def _save_launch_failure(self, samples):
        self._home_launch_metrics = dict(home_confirmed=False, samples=samples)
        try:
            folder = Path('logs/launch') / time.strftime('%Y%m%d-%H%M%S')
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'observation.json').write_text(json.dumps(dict(checked_at=now(), samples=samples), ensure_ascii=False, indent=2), encoding='utf-8')
            image = self.tasker.controller.cached_image
            if image is not None:
                Image.fromarray(image[:, :, ::-1]).save(folder / 'screen.png')
            self.send_log('首页加载失败现场已保存：' + str(folder))
        except (OSError, ValueError):
            self.send_log('首页加载失败现场未能写入，请查看模拟器和日志')

    def _go_home(self):
        self._check_active()
        self._guard_verification()
        records = self._launch_records()
        notification_dismissed = self._deny_launch_notification(records)
        if notification_dismissed:
            self._sleep(0.5)
            records = self._launch_records()
        foreground = self._app_in_foreground()
        if foreground and home_ready(records):
            self.send_log('当前已在首页，直接确认，不重开应用')
            return self._wait_home_ready(timeout=30, notification_dismissed=notification_dismissed)
        # A system dialog or another app may cover an existing detail activity.
        # Starting alone would resume that activity, so reset the app in both cases.
        # Login and access-verification screens were guarded before any mutation.
        self.tasker.controller.post_stop_app('cn.xuexi.android').wait()
        self.tasker.controller.post_start_app('cn.xuexi.android').wait()
        self.send_log('已打开学习强国，按实际画面等待开屏结束及首页稳定')
        return self._wait_home_ready(notification_dismissed=notification_dismissed)
