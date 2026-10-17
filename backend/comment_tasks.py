"""One owner-approved, fixed comment per day. No generative model is involved."""
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from daily_flow import verification_visible
from home_navigation import home_ready
from task_support import TaskIncomplete, TaskUnavailable, is_complete

COMMENT_TEXT = '认真学习，增长知识。'
COMMENT_SCOPE = '科普中国/生活百科'
POLICY_PATH = Path('config/comment-policy.json')
JOURNAL_PATH = Path('config/comments.sqlite3')


def comment_policy():
    try:
        value = json.loads(POLICY_PATH.read_text('utf-8-sig'))
    except (OSError, ValueError):
        value = {}
    enabled = (isinstance(value, dict) and value.get('enabled') is True
               and value.get('text') == COMMENT_TEXT and value.get('scope') == COMMENT_SCOPE)
    return dict(enabled=enabled, text=COMMENT_TEXT, scope=COMMENT_SCOPE, daily_limit=1)


class CommentJournal:
    """Reserve before the sending action, including ambiguous/crashed attempts."""
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else JOURNAL_PATH

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS comments '
                           '(day TEXT PRIMARY KEY, article TEXT NOT NULL, text TEXT NOT NULL, '
                           'state TEXT NOT NULL, created_at TEXT NOT NULL)')
                yield db
        finally:
            db.close()

    def today(self):
        with self._connect() as db:
            row = db.execute('SELECT * FROM comments WHERE day=?',
                             (datetime.now().date().isoformat(),)).fetchone()
            return dict(row) if row else None

    def reserve(self, article):
        with self._connect() as db:
            result = db.execute('INSERT OR IGNORE INTO comments VALUES(?,?,?,?,?)',
                (datetime.now().date().isoformat(), article, COMMENT_TEXT, 'attempted',
                 datetime.now().astimezone().isoformat(timespec='seconds')))
            return result.rowcount == 1

    def credited(self):
        with self._connect() as db:
            db.execute('UPDATE comments SET state=? WHERE day=?',
                       ('credited', datetime.now().date().isoformat()))


def comment_feed(records):
    if verification_visible(records):
        return False
    top = {r['text'].strip() for r in records if r['box'][1] < 250}
    return {'科普中国', '生活百科', '科学求真', '三农科普'} <= top


def comment_draft_ready(records):
    """Both the whole input text and its own publish button must be recognized."""
    if verification_visible(records):
        return None
    text = [r for r in records if 25 <= r['box'][0] < 580 and 1100 <= r['box'][1] < 1190]
    buttons = [r for r in records if r['text'].strip() == '发布'
               and 600 <= r['box'][0] < 700 and 1200 <= r['box'][1] < 1265]
    if len(text) == 1 and re.sub(r'\s+', '', text[0]['text']) == COMMENT_TEXT and len(buttons) == 1:
        return buttons[0]
    return None


class CommentTasks:
    def _comment_ocr(self, name, roi):
        self._check_active()
        self._guard_verification()
        result = self.tasker.post_task(name, {name: {
            'recognition': 'OCR', 'roi': roi, 'action': 'DoNothing',
            'timeout': 1200, 'pre_delay': 0, 'post_delay': 0,
        }}).wait().get()
        if result is None or result.status.failed or not result.nodes:
            return []
        return [dict(text=r.text, box=list(r.box))
                for r in getattr(result.nodes[0].recognition, 'all_results', [])]

    def _comment_click(self, row):
        self._check_active()
        self._guard_verification()
        if not self._app_in_foreground():
            raise TaskIncomplete('发表观点时应用不在前台')
        x, y, width, height = row['box']
        if not self.tasker.controller.post_click(round(x+width/2), round(y+height/2)).wait().succeeded:
            raise TaskIncomplete('发表观点触控未成功')
        self._sleep(.5)

    def _comment_wait(self, predicate, timeout=20, required=True):
        deadline = time.monotonic()+timeout
        paused = getattr(self, '_paused_seconds', 0)
        while time.monotonic() < deadline:
            records = self._screen_records()
            self._guard_verification(records)
            if verification_visible(records):
                records = self._screen_records()
            total = getattr(self, '_paused_seconds', 0)
            deadline += max(0, total-paused)
            paused = total
            if predicate(records):
                return records
            self._sleep(.5)
        if required:
            raise TaskIncomplete('发表观点页面尚未确认，未猜测点击或重复发布')
        return None

    def _comment_label(self, records, text, top=0, bottom=1280):
        candidates = [r for r in records if r['text'].strip() == text and top <= r['box'][1] < bottom]
        if len(candidates) != 1:
            raise TaskIncomplete('发表观点入口不唯一或未找到：'+text)
        return candidates[0]

    def _comment_channel_menu(self, records):
        # Native, observed 720x1280 homepage icon; never act on an article/modal.
        image = self.tasker.controller.cached_image
        if image is None or image.shape[:2] != (1280, 720):
            raise TaskIncomplete('发表观点暂只支持已验收的720×1280界面')
        if not any(r['text'].strip() == '全部频道' for r in records):
            if not home_ready(records) and not comment_feed(records):
                raise TaskIncomplete('尚未确认首页频道栏，不点击频道菜单')
            self._comment_click(dict(box=[615, 120, 38, 44]))
        return self._comment_wait(lambda rs: any(r['text'].strip() == '全部频道' for r in rs))

    def _comment_open_article(self):
        self._reset_home()
        records = self._comment_channel_menu(self._screen_records())
        self._comment_click(self._comment_label(records, '科普中国', 180, 1100))
        records = self._comment_wait(comment_feed)
        self._comment_click(self._comment_label(records, '生活百科', 180, 250))
        self._comment_wait(comment_feed)
        self._sleep(1)
        for page in range(3):
            records = self._screen_records()
            self._guard_verification(records)
            if not comment_feed(records):
                raise TaskIncomplete('未确认科普中国生活百科列表，未选择其他文章')
            # Confirm the selected subchannel by its red native label.
            label = self._comment_label(records, '生活百科', 180, 250)
            x, y, width, height = label['box']
            pixels = self.tasker.controller.cached_image[y:y+height, x:x+width, :3].astype('int16')
            if int(((pixels[:, :, 2] > 160) & (pixels[:, :, 2] > pixels[:, :, 1]+50)
                    & (pixels[:, :, 2] > pixels[:, :, 0]+50)).sum()) < 12:
                raise TaskIncomplete('生活百科尚未选中，停止观点任务')
            boxes, kinds = self.detect()
            for box, kind in zip(boxes, kinds):
                if kind not in ('article', 'article_image') or not 250 <= box[1] < 1080:
                    continue
                title = self._article_card_text(box)
                if len(title) < 6 or any(s in title for s in ('直播', '专题', '订阅', '图集')):
                    continue
                self._comment_click(dict(box=[int(box[0])+20, int(box[1])+30, min(350, int(box[2])-20), 50]))
                def loaded(rs):
                    body = ''.join(r['text'] for r in rs if 250 <= r['box'][1] < 1080)
                    return '来源' in body and len(body) >= 60
                self._comment_wait(loaded, timeout=45)
                return title
            if page < 2:
                self.tasker.controller.post_swipe(350, 1090, 350, 550, 450).wait()
                self._sleep(.7)
        raise TaskUnavailable('生活百科未找到可确认正文的文章，本日不发表')

    def _prepare_comment_draft(self):
        article = self._comment_open_article()
        entry = self._comment_ocr('观点输入栏', [20, 1200, 455, 75])
        entry = [r for r in entry if r['text'].strip() in ('欢迎发表观点', '欢迎发表你的观点', '发表观点')]
        if len(entry) != 1:
            raise TaskIncomplete('当前文章未找到观点输入栏')
        self._comment_click(entry[0])
        empty = lambda _: any(r['text'].strip() == '好观点将会被优先展示'
                              for r in self._comment_ocr('观点空输入框', [25, 1105, 670, 90]))
        if self._comment_wait(empty, timeout=3, required=False) is None:
            # A newly loaded article can ignore its first tap. Only retry this
            # recognized navigation control once; never retry the Publish action.
            current = self._comment_ocr('观点输入栏重核', [20, 1200, 455, 75])
            targets = [r for r in current if r['text'].strip() == '欢迎发表观点']
            if len(targets) != 1 or not self._app_in_foreground():
                raise TaskIncomplete('观点输入栏已改变，未补按')
            x, y, width, height = targets[0]['box']
            x, y = round(x+width/2), round(y+height/2)
            if not self.tasker.controller.post_swipe(x, y, x, y, 120).wait().succeeded:
                raise TaskIncomplete('观点输入栏短按未成功')
            self._comment_wait(empty, timeout=8)
        self._comment_click(dict(box=[30, 1110, 650, 85]))
        if not self.tasker.controller.post_input_text(COMMENT_TEXT).wait().succeeded:
            raise TaskIncomplete('固定观点短句未输入成功')
        # Input completion and actual redraw are separate; wait for the real text.
        self._comment_wait(lambda _: self._comment_publish_target() is not None, timeout=15)
        self.send_log('观点草稿已核对：'+COMMENT_TEXT+'；范围：'+COMMENT_SCOPE)
        return article

    def _comment_publish_target(self):
        records = self._comment_ocr('观点文字核对', [25, 1105, 670, 90])
        records += self._comment_ocr('观点发布按钮', [600, 1200, 100, 70])
        return comment_draft_ready(records)

    def _comment_restore_home(self):
        self._guard_verification()
        # Only return from a recognized article, then restore the Recommended tab.
        entry = self._comment_ocr('观点返回文章核对', [20, 1200, 455, 75])
        if not any('发表观点' in r['text'] for r in entry):
            raise TaskIncomplete('观点提交后未回到普通文章页，保留现场，不重复发表')
        self.tasker.post_task('返回').wait()
        records = self._comment_wait(comment_feed)
        records = self._comment_channel_menu(records)
        self._comment_click(self._comment_label(records, '推荐', 180, 1100))
        self._wait_home_ready(timeout=20)

    def publish_comment(self, before=None):
        before = before or self.read_learning_score(['发表观点'])['发表观点']
        if is_complete(before):
            return before
        if before['maximum'] != 1:
            raise TaskIncomplete('发表观点计分规则已变化，需要核实')
        if not comment_policy()['enabled']:
            raise TaskUnavailable('固定短句及每日发表范围尚未获本人确认，未发布')
        journal = CommentJournal()
        if journal.today():
            raise TaskIncomplete('今天已有发表尝试记录，未重复发送；请查看积分与评论结果')
        self.send_log('发表观点：仅用固定短句，每天最多一条，不调用模型生成')
        article = self._prepare_comment_draft()
        self._check_active()
        button = self._comment_publish_target()
        if button is None or not comment_policy()['enabled']:
            raise TaskIncomplete('发布前短句或授权核对失败，未点击发布')
        if not journal.reserve(article):
            raise TaskIncomplete('今天已有发表尝试，未重复发送')
        # The durable reservation deliberately remains even if the click times out.
        self._comment_click(button)
        self._comment_wait(lambda _: self._comment_publish_target() is None)
        self._comment_restore_home()
        self._score_refresh_required = True
        after = self.read_learning_score(['发表观点'])['发表观点']
        if after['maximum'] != before['maximum'] or after['value'] < before['value']:
            raise TaskIncomplete('观点核分规则发生变化，保留发表记录，不重复发送')
        if is_complete(after):
            journal.credited()
        self.send_log(f"发表观点核分：{before['value']}/{before['maximum']} → {after['value']}/{after['maximum']}；未满也不追加发表")
        return after
