"""Read visible platform history; confirm dates and scores across two complete passes."""
from collections import defaultdict
from datetime import date, datetime, timedelta
import re
import time
import unicodedata

from points_history import BEIJING
from task_support import ALIASES, TaskCancelled, TaskIncomplete


def history_page(records):
    return any(row['text'].strip() == '积分明细' and 35 <= row['box'][1] < 110 for row in records)


def parse_day_summary(records):
    """Only the selected day's full date and 当日积分; never use 年度积分."""
    day = points = None
    for row in records:
        x, y, w, h = row['box']
        text = unicodedata.normalize('NFKC', row['text']).replace(' ', '')
        if 570 <= y < 725:
            match = re.search(r'(20\d{2})[.年/-](\d{2})[.月/-](\d{2})', text)
            if match:
                try:
                    day = date(*map(int, match.groups()))
                except ValueError:
                    pass
            match = re.fullmatch(r'当日积分[:：]?(\d{1,3})', text)
            if match:
                points = int(match.group(1))
    return day, points


def _chart_labels(records, today):
    dates, scores = [], []
    for row in records:
        x, y, w, h = row['box']
        text = unicodedata.normalize('NFKC', row['text']).replace(' ', '')
        if 110 <= y < 180:
            if text == '今日':
                dates.append((x + w / 2, today))
            for match in re.finditer(r'(0[1-9]|1[0-2])[.．](0[1-9]|[12]\d|3[01])', text):
                month, number = map(int, match.groups())
                try:
                    day = date(today.year, month, number)
                    if day > today:
                        day = day.replace(year=today.year - 1)
                except ValueError:
                    continue
                if 0 <= (today - day).days < 90:
                    center = x + w * (match.start() + match.end()) / (2 * len(text))
                    dates.append((center, day))
        if 180 <= y < 565:
            match = re.fullmatch(r'(\d{1,3})分', text)
            if match:
                scores.append((x + w / 2, int(match.group(1))))
    return dates, scores


def parse_history_chart(records, today):
    """Split merged axis labels, then associate score labels by column, not list order."""
    dates, scores = _chart_labels(records, today)
    candidates = defaultdict(set)
    for center, points in scores:
        nearest = sorted((abs(center - axis), day) for axis, day in dates)
        if nearest and nearest[0][0] <= 34 and (len(nearest) == 1 or nearest[1][0] - nearest[0][0] > 12):
            candidates[nearest[0][1].isoformat()].add(points)
    return {day: next(iter(values)) for day, values in candidates.items() if len(values) == 1}


def confirmed_history(first, second):
    return {day: points for day, points in first.items() if second.get(day) == points}


class HistoryReaderMixin:
    """Own controller for the import; no daily run report, model requests or learning tasks."""
    def _check_active(self):
        if self.history_cancel.is_set():
            raise TaskCancelled()
        if time.monotonic() > self.history_deadline:
            raise TaskIncomplete('读取历史积分超过等待时间，已停止；原有记录保留。')
        if datetime.now(BEIJING).date() != self.history_date:
            raise TaskIncomplete('读取期间日期已变化，请重新导入。')

    def _wait_for_owner(self, reason='请本人完成当前登录或访问验证', resume_when=None):
        from daily_flow import verification_visible
        from home_navigation import login_required
        if getattr(self, 'window_policy', None) is not None:
            self.window_policy.attention()
        self.history_progress('waiting', reason)
        deadline = min(self.history_deadline, time.monotonic() + 120)
        while time.monotonic() < deadline:
            self._check_active()
            rows = self._screen_records()
            if any('网络开小差' in row['text'] for row in rows):
                raise TaskIncomplete('平台登录或验证页面报告网络错误；请处理后重新导入。')
            if not login_required(rows) and not verification_visible(rows) and any(
                    re.search(r'学习积分|积分中心|积分明细|学习报表|^我的$', row['text']) for row in rows):
                if getattr(self, 'window_policy', None) is not None:
                    self.window_policy.resume()
                self.history_progress('reading', '已恢复，继续读取积分记录')
                return
            self._sleep(1)
        raise TaskIncomplete('仍需本人完成登录或访问验证，已停止本次导入；原有记录保留。')

    def _guard_verification(self, records=None, resume_when=None):
        from daily_flow import verification_visible
        rows = self._screen_records() if records is None else records
        if verification_visible(rows):
            self._wait_for_owner('请在 MuMu 本人完成当前访问验证，完成后自动继续导入')
            return True
        return False

    def _history_back(self):
        from device_support import command
        self._check_active()
        if command([*self._adb_command, 'shell', 'input', 'keyevent', '4']).returncode:
            raise TaskIncomplete('未能返回积分中心，已停止导入。')
        self._sleep(.5)

    def _history_records(self):
        rows = self._screen_records()
        if self._guard_verification(rows):
            rows = self._screen_records()
        if any('网络开小差' in row['text'] for row in rows):
            raise TaskIncomplete('平台积分明细暂时无法联网，请稍后重新导入。')
        return rows

    def _open_history(self):
        self.history_progress('preparing', '正在连接模拟器')
        self.prepare_device()
        rows = self._history_records()
        if history_page(rows):
            self._history_back()
        elif any(row['text'].strip() == '积分明细' and 100 < row['box'][1] < 260
                 and row['box'][0] > 400 for row in rows):
            # An already-open menu consumes the score-list swipes below it.
            self._history_back()
        self.history_progress('reading', '正在读取今日积分')
        current = self.read_learning_score(list(ALIASES))
        observed = datetime.now(BEIJING).isoformat()
        self._enter_learning_score()
        rows = self._history_records()
        if not any(row['text'].strip() == '积分中心' and row['box'][1] < 110 for row in rows):
            raise TaskIncomplete('未确认积分中心，未点击历史入口。')
        self.tasker.controller.post_click(673, 70).wait()
        entry = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self._sleep(.3)
            rows = self._history_records()
            entry = next((row for row in rows if row['text'].strip() == '积分明细'
                          and 100 < row['box'][1] < 260 and row['box'][0] > 400), None)
            if entry:
                break
        if entry is None:
            raise TaskIncomplete('没有识别到积分明细菜单，未继续操作其它入口。')
        x, y, w, h = entry['box']
        self.tasker.controller.post_click(x + w // 2, y + h // 2).wait()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            self._sleep(.6)
            rows = self._history_records()
            selected, points = parse_day_summary(rows)
            if history_page(rows) and selected == self.history_date and points is not None and parse_history_chart(rows, selected):
                return current, observed, points
        raise TaskIncomplete('积分明细没有加载出实际日期和分数，未导入空白数据。')

    def _history_pass(self, forward, all_days):
        result, conflicting, previous = {}, set(), None
        for _ in range(12):
            self._check_active()
            rows = self._history_records()
            if not history_page(rows):
                raise TaskIncomplete('已离开积分明细页面，未继续滑动。')
            visible = parse_history_chart(rows, self.history_date)
            if not visible:
                raise TaskIncomplete('历史图表的日期和分数未完整识别，已停止，未填造积分。')
            for day, points in visible.items():
                if day in result and result[day] != points:
                    conflicting.add(day)
                result[day] = points
            all_days.update(day.isoformat() for _, day in _chart_labels(rows, self.history_date)[0])
            self.history_progress('reading' if forward else 'checking',
                '正在读取历史积分' if forward else '正在核对日期与分数', read_days=len(all_days))
            key = tuple(sorted(visible.items()))
            if key == previous:
                break
            previous = key
            start, end = (650, 100) if forward else (100, 650)
            if not self.tasker.controller.post_swipe(start, 300, end, 300, 650).wait().succeeded:
                raise TaskIncomplete('历史图表滑动未成功，未继续导入。')
            self._sleep(.65)
        else:
            raise TaskIncomplete('历史图表没有到达可确认的边界，已停止本次读取。')
        return {day: points for day, points in result.items() if day not in conflicting}

    def collect(self, progress, cancel, *, include_details=False):
        self.history_progress, self.history_cancel = progress, cancel
        self.history_date = datetime.now(BEIJING).date()
        self.history_deadline = time.monotonic() + (1200 if include_details else 240)
        self.run_report = None
        self.stop_flag = False
        try:
            current, observed, platform_today = self._open_history()
            all_days = set()
            first = self._history_pass(True, all_days)
            second = self._history_pass(False, all_days)
            if first.keys()!=second.keys():
                first = self._history_pass(True, all_days)
            verified = confirmed_history(first, second)
            if verified.get(self.history_date.isoformat()) != platform_today:
                raise TaskIncomplete('图表今日总分与当日积分不一致，未保存本次历史读取。')
            self._check_active()
            rows = [dict(day=day, points=points) for day, points in sorted(verified.items())
                    if day < self.history_date.isoformat()]
            if not rows and len(all_days) > 1:
                raise TaskIncomplete('没有往日记录通过两轮核对，原有积分记录保留。')
            details = self._read_history_ledgers(verified) if include_details else {}
            if history_page(self._history_records()):
                self._history_back()
            return dict(rows=rows, current_scores=current, current_observed_at=observed,
                read_days=len(all_days), unconfirmed_days=len(all_days - verified.keys()),
                date_from=min(verified), date_to=max(verified), source='screen_ocr_verified',**details)
        finally:
            if getattr(self, 'window_policy', None) is not None:
                self.window_policy.resume()
            self.tasker.post_stop()
            self.ai_resolver.session.close()


def collect_platform_history(queue, progress, cancel, ledger_cache=None, *, include_details=False):
    from utils import MaaWorker
    from provider_support import MODEL
    from ledger_reading import LedgerReading
    class HistoryWorker(LedgerReading, HistoryReaderMixin, MaaWorker):
        pass
    worker = HistoryWorker(queue, api_key='', model=MODEL)
    worker.ledger_cache = ledger_cache or {}
    return worker.collect(progress, cancel, include_details=include_details)
