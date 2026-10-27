"""One-shot automatic startup guard; no GUI, emulator, model or daily claim."""
from datetime import datetime, timedelta
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from schedule_support import CHINA, _native, daily_started_today, now_china


def automatic_due(now=None):
    """Use the real daily trigger, never next-run time or a new random draw."""
    now = (now or now_china()).astimezone(CHINA)
    if daily_started_today(now):
        return False, '今天已经启动过，未重复执行'
    native = _native()
    if not native.get('enabled'):
        return False, '当前为手动执行，未自动启动'
    boundary = datetime.fromisoformat(native['start_boundary'])
    boundary = boundary.replace(tzinfo=CHINA) if boundary.tzinfo is None else boundary.astimezone(CHINA)
    delay = native['random_delay_seconds']
    if type(delay) is not int or not 0 <= delay < 86400:
        raise ValueError('Invalid native random delay')
    if boundary.date() > now.date():
        return False, '尚未到下一次计划时间'
    # A daily boundary from an earlier date still describes today's clock time.
    due = now.replace(hour=boundary.hour, minute=boundary.minute, second=boundary.second, microsecond=0)
    due += timedelta(seconds=delay)
    if now < due:
        return False, '尚未到今天的计划时间'
    return True, '已错过计划时间，联网后自动补跑'


def internet_ready():
    """Public HTTPS HEAD only; an HTTP error still proves network reachability."""
    try:
        with urlopen(Request('https://www.xuexi.cn/', method='HEAD'), timeout=3):
            return True
    except HTTPError:
        return True
    except (URLError, TimeoutError, OSError):
        return False


def wait_for_automatic_start(log, *, timeout=600, interval=5):
    """No schedule rearming or day claim until the original daily flow starts."""
    try:
        ready, reason = automatic_due()
        log(reason)
        if not ready:
            return False
        deadline = time.monotonic()+timeout
        waiting = False
        while True:
            if daily_started_today():
                log('等待期间今天已由其他入口启动，未重复执行')
                return False
            if internet_ready():
                ready, reason = automatic_due()
                log(('网络已就绪，' if ready else '等待结束，')+reason)
                return ready
            if not waiting:
                log('等待联网；不会提前打开控制台或模拟器')
                waiting = True
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                log('本次联网等待结束，保留计划；网络恢复事件会再次检查')
                return False
            time.sleep(min(interval, remaining))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        log('自动启动条件尚未确认，保留原计划：'+type(error).__name__)
        return False
