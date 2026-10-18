"""Task score parsing and durable status, independent of device control."""
import copy
import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

VERSION = "local-20261018.26"
REPORT_PATH = Path("config/last-run.json")
# Login is a prerequisite. Then usual no-challenge tasks, occasional-challenge tasks
# by daily cap (10/2/1), and daily quiz last (frequent challenge on this emulator).
# These are observed scheduling groups, not guaranteed platform verification rules.
ALIASES = {"登录": "登录", "选读文章": "我要选读文章", "视听学习": "我要视听学习", "本地频道": "本地频道", "趣味答题": "趣味答题", "订阅": "订阅", "发表观点": "发表观点", "每日答题": "每日答题"}
BOUNDARIES = ("登录", "我要选读文章", "我要视听学习", "每日答题", "趣味答题", "订阅", "强国运动", "专项答题", "每周答题", "发表观点", "本地频道")


def ordered_tasks(tasks, verification_first=False):
    selected = list(dict.fromkeys(tasks))
    order = ('登录','趣味答题','每日答题','订阅','发表观点','选读文章','视听学习','本地频道') if verification_first else ALIASES
    priority = {name:index for index,name in enumerate(order)}
    return sorted(selected, key=lambda name:priority.get(name,len(priority)))


class TaskCancelled(Exception):
    pass


class TaskIncomplete(RuntimeError):
    pass


class TaskUnavailable(TaskIncomplete):
    pass


class TaskSkipped(TaskIncomplete):
    """A recognized task mode deliberately excluded by the owner's settings."""
    pass


class HumanRequired(TaskIncomplete):
    """End an unattended run while preserving the screen for its owner."""
    pass


def visible_task_rows(records):
    """Find visible row bounds and its own action; never choose another row's button."""
    clean = []
    for item in records:
        box = item.get("box", [])
        text = re.sub(r"\s+", "", item.get("text", ""))
        if text and len(box) == 4:
            clean.append(dict(text=text, box=list(box), y=box[1] + box[3]/2))
    anchors = sorted((r for r in clean if r["box"][0] < 350 and r["text"] in BOUNDARIES), key=lambda r:r["y"])
    rows = {}
    for index, anchor in enumerate(anchors):
        name = next((key for key,value in ALIASES.items() if value == anchor["text"]), anchor["text"])
        end = anchors[index+1]["y"] - 15 if index+1 < len(anchors) else 1240
        row = [r for r in clean if anchor["y"] - 8 <= r["y"] < end]
        actions = [r for r in row if r["box"][0] >= 480 and r["text"] in ("去看看", "去看", "去学习", "去答题", "去订阅", "去参与")]
        rows[name] = dict(anchor=anchor, records=row, action=actions[0] if len(actions)==1 else None,
                          rules="".join(r["text"] for r in sorted(row,key=lambda r:r["y"]) if r["box"][0]<480))
    return rows


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_score_rows(records):
    """Only associate a score with its own visible task row; never another row."""
    clean = []
    for item in records:
        text = re.sub(r"\s+", "", item.get("text", "")).replace("／", "/")
        box = item.get("box", [])
        if text and len(box) == 4:
            clean.append(dict(text=text, box=box, y=box[1] + box[3] / 2))
    anchors = sorted((r for r in clean if r["box"][0] < 350 and
                      any(r["text"] == x for x in BOUNDARIES)), key=lambda r:r["y"])
    result = {}
    for index, anchor in enumerate(anchors):
        name = next((name for name, alias in ALIASES.items() if anchor["text"] == alias), None)
        if name is None:
            continue
        end_y = anchors[index+1]["y"] if index+1 < len(anchors) else 1265
        row = [r for r in clean if anchor["y"] - 8 <= r["y"] < end_y - 15]
        # OCR may split the numerator and denominator into adjacent boxes.
        right = sorted((r for r in row if r["box"][0] >= 480), key=lambda r:(r["y"], r["box"][0]))
        groups = []
        for item in right:
            group = next((g for g in groups if abs(g[0]["y"] - item["y"]) <= 18), None)
            if group is None:
                groups.append([item])
            else:
                group.append(item)
        candidates = []
        for group in groups:
            text = "".join(x["text"] for x in sorted(group, key=lambda r:r["box"][0]))
            match = re.fullmatch(r"(\d{1,3})分?/(\d{1,3})分?", text)
            if match:
                value, maximum = map(int, match.groups())
                if 0 <= value <= maximum <= 100 and maximum > 0:
                    candidates.append(dict(value=value, maximum=maximum, evidence="score_fraction"))
        if len(candidates) == 1:
            result[name] = candidates[0]
            continue
        # A partly obscured fraction may still have an explicit completed badge + row limit.
        caps = [re.fullmatch(r"每日上限(\d{1,3})分", r["text"]) for r in row]
        caps = {int(m.group(1)) for m in caps if m and 0 < int(m.group(1)) <= 100}
        completed = any(r["text"] == "已完成" and r["box"][0] >= 480 for r in row)
        numerators = [re.fullmatch(r"(\d{1,3})分", r["text"]) for r in right]
        numerators = {int(m.group(1)) for m in numerators if m}
        if not candidates and len(caps) == 1 and len(numerators) == 1:
            maximum = next(iter(caps))
            value = next(iter(numerators))
            if 0 <= value <= maximum:
                result[name] = dict(value=value, maximum=maximum, evidence="score_value_and_row_limit")
                continue
        if not candidates and completed and len(caps) == 1:
            maximum = caps.pop()
            result[name] = dict(value=maximum, maximum=maximum, evidence="completed_badge_and_row_limit")
    return result


def is_complete(score):
    return bool(score and score["value"] == score["maximum"] and score["maximum"] > 0)


def fun_first_round_credited(score, rules):
    """Eight is a credited first-round loss, only under observed platform rules."""
    clean = re.sub(r'\s+','',rules or '')
    return bool(score and score['maximum'] == 10 and score['value'] == 8
                and '首局' in clean and '未获胜积8分' in clean)


def parse_video_progress(text):
    clean = re.sub(r"\s+", "", text).replace("：", ":").replace("／", "/")
    match = re.search(r"(?<!\d)(\d{1,3}):(\d{2})/(\d{1,3}):(\d{2})(?!\d)", clean)
    if not match:
        return None
    minute, second, total_minute, total_second = map(int, match.groups())
    position, total = minute*60+second, total_minute*60+total_second
    if second >= 60 or total_second >= 60 or not 0 <= position <= total <= 36000 or total == 0:
        return None
    return dict(position=position,total=total)


def read_report():
    try:
        value = json.loads(REPORT_PATH.read_text("utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


class RunReport:
    def __init__(self, tasks):
        self.lock = threading.RLock()
        self.data = dict(version=VERSION, run_id=uuid.uuid4().hex[:12], started_at=now(),
                         finished_at=None, status="running", pause_reason=None,
                         tasks={name:dict(state="pending", before=None, after=None) for name in tasks})
        self.save()

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def save(self):
        with self.lock:
            try:
                REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
                temp = REPORT_PATH.with_suffix(".tmp")
                temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
                temp.replace(REPORT_PATH)
            except OSError:
                # A status file failure must not be mistaken for a task failure or expose secrets.
                pass

    def update(self, **kwargs):
        with self.lock:
            self.data.update(kwargs)
            self.save()

    def task(self, name, **kwargs):
        with self.lock:
            self.data["tasks"][name].update(kwargs)
            self.save()

    def finish(self, status):
        self.update(status=status, finished_at=now(), pause_reason=None, phase=None)
