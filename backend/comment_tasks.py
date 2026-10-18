"""Contextual comments: one unattended attempt; owner-started retries are journaled."""
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from daily_flow import verification_visible
from four_player import race_network_error
from home_navigation import home_ready
from comment_drafts import ContextCommentDrafts, valid_comment_text, validate_draft, valid_article
from comment_reading import article_page, append_page
from task_support import HumanRequired, TaskIncomplete, TaskUnavailable, is_complete

COMMENT_MODE = 'article_context_v1'
COMMENT_SCOPE = '科普中国/生活百科'
POLICY_PATH = Path('config/comment-policy.json')
JOURNAL_PATH = Path('config/comments.sqlite3')


def comment_policy():
    try:
        value = json.loads(POLICY_PATH.read_text('utf-8-sig'))
    except (OSError, ValueError):
        value = {}
    enabled = (isinstance(value, dict) and value.get('enabled') is True
               and value.get('mode') == COMMENT_MODE and value.get('scope') == COMMENT_SCOPE)
    return dict(enabled=enabled, mode=COMMENT_MODE, scope=COMMENT_SCOPE, daily_limit=1,
                min_chars=8, max_chars=28, model_review=True,
                unattended_attempt_limit=1, owner_started_retry=True)


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
                db.execute('CREATE TABLE IF NOT EXISTS comment_attempts '
                           '(attempt_id TEXT PRIMARY KEY, day TEXT NOT NULL, article TEXT NOT NULL, '
                           'text TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL)')
                db.execute('CREATE INDEX IF NOT EXISTS comment_attempts_day ON comment_attempts(day)')
                # Keep the original table and every original row. Backfill once
                # per day; all later attempts are append-only separate records.
                db.execute('INSERT OR IGNORE INTO comment_attempts '
                           "SELECT 'legacy:'||day, day, article, text, state, created_at FROM comments "
                           'WHERE NOT EXISTS (SELECT 1 FROM comment_attempts a WHERE a.day=comments.day)')
                yield db
        finally:
            db.close()

    def today(self):
        with self._connect() as db:
            row = db.execute('SELECT * FROM comment_attempts WHERE day=? ORDER BY rowid DESC LIMIT 1',
                             (datetime.now().date().isoformat(),)).fetchone()
            return dict(row) if row else None

    def reserve(self, article, text, *, owner_retry=False, attempt_id=None):
        if not valid_comment_text(text):
            raise TaskIncomplete('观点内容未满足短评规则，未占用发布额度')
        with self._connect() as db:
            day = datetime.now().date().isoformat()
            previous = db.execute('SELECT state FROM comment_attempts WHERE day=?', (day,)).fetchall()
            if previous and (not owner_retry or not attempt_id or any(r['state']=='credited' for r in previous)):
                return False
            attempt_id = attempt_id or uuid.uuid4().hex
            stamp = datetime.now().astimezone().isoformat(timespec='seconds')
            result = db.execute('INSERT OR IGNORE INTO comment_attempts VALUES(?,?,?,?,?,?)',
                (attempt_id, day, article, text, 'attempted', stamp))
            if result.rowcount == 1:
                db.execute('INSERT OR IGNORE INTO comments VALUES(?,?,?,?,?)',
                           (day,article,text,'attempted',stamp))
                self._reserved_id = attempt_id
            return result.rowcount == 1

    def credited(self):
        with self._connect() as db:
            attempt_id = getattr(self, '_reserved_id', None)
            if attempt_id is None:
                rows = db.execute('SELECT attempt_id FROM comment_attempts WHERE day=?',
                                  (datetime.now().date().isoformat(),)).fetchall()
                if len(rows) != 1:
                    raise TaskIncomplete('未找到唯一的本轮发表记录，未修改历史记录')
                attempt_id = rows[0]['attempt_id']
            db.execute('UPDATE comment_attempts SET state=? WHERE attempt_id=?', ('credited',attempt_id))
            db.execute('UPDATE comments SET state=? WHERE day=? AND ?='
                       '(SELECT attempt_id FROM comment_attempts WHERE day=? ORDER BY rowid LIMIT 1)',
                       ('credited',datetime.now().date().isoformat(),attempt_id,datetime.now().date().isoformat()))

    def used_articles(self):
        with self._connect() as db:
            return [r['article'] for r in db.execute('SELECT DISTINCT article FROM comment_attempts')]


def article_title_key(text):
    return re.sub(r'\W+', '', text)


def comment_card_used(signature, titles):
    card = article_title_key(signature)
    keys = [article_title_key(title) for title in titles]
    # A list card can omit the title's ending. An exact twelve-character prefix
    # is sufficient to skip it; full title checks before writing remain strict.
    return any(len(key) >= 4 and (key in card or (len(key)>12 and key[:12] in card)) for key in keys)


def comment_feed(records, *, allow_collapsed=False):
    if verification_visible(records):
        return False
    top = {r['text'].strip() for r in records if r['box'][1] < 250}
    if {'科普中国', '生活百科', '科学求真', '三农科普'} <= top:
        return True
    channels = {r['text'].strip() for r in records if 100 <= r['box'][1] < 180}
    return (allow_collapsed and '科普中国' in channels
            and len(channels & {'行走中国', '乡村振兴', '科普中国', '国际', '经济'}) >= 3)


def comment_text_key(text):
    """OCR may change exclamation width; retain every word and punctuation mark."""
    return re.sub(r'\s+', '', text).replace('!', '！')


def comment_text_matches(rows, expected_text):
    """Compare one line or two adjacent wrapped lines, never unrelated OCR rows."""
    if not 1 <= len(rows) <= 2:
        return False
    rows = sorted(rows, key=lambda r:r['box'][1])
    if len(rows) == 2:
        first, second = (row['box'] for row in rows)
        if abs(first[0]-second[0]) > 35 or not -8 <= second[1]-first[1]-first[3] <= 35:
            return False
    return ''.join(comment_text_key(row['text']) for row in rows) == comment_text_key(expected_text)


def comment_draft_ready(records, expected_text):
    """Both the whole input text and its own publish button must be recognized."""
    if verification_visible(records) or not valid_comment_text(expected_text):
        return None
    text = [r for r in records if 25 <= r['box'][0] < 580 and 1100 <= r['box'][1] < 1190]
    buttons = [r for r in records if r['text'].strip() == '发布'
               and 600 <= r['box'][0] < 700 and 1200 <= r['box'][1] < 1265]
    if comment_text_matches(text, expected_text) and len(buttons) == 1:
        return buttons[0]
    return None


def comment_article(records, *, require_body=True):
    if verification_visible(records) or comment_feed(records):
        return None
    sources = [r for r in records if 250 <= r['box'][1] < 400 and r['box'][0] < 200
               and re.match(r'^来源[：:]', r['text'].strip())]
    if len(sources) != 1:
        return None
    titles = sorted((r for r in records if 150 <= r['box'][1] < min(300, sources[0]['box'][1]-15)
                     and r['box'][0] < 100), key=lambda r:r['box'][1])
    if not 1 <= len(titles) <= 3:
        return None
    if any(abs(left['box'][0]-right['box'][0]) > 30
           or not -8 <= right['box'][1]-left['box'][1]-left['box'][3] <= 40
           for left, right in zip(titles, titles[1:])):
        return None
    body = ''.join(r['text'] for r in sorted(records, key=lambda r:r['box'][1])
                   if 250 <= r['box'][1] < 1100 and r not in titles)
    article = dict(title=''.join(r['text'] for r in titles), body=body, scope=COMMENT_SCOPE)
    if not require_body:
        return article if 4 <= len(article['title']) <= 150 else None
    return article if valid_article(article) else None


def comment_posted(records, expected_text):
    """Own visible comment is a continuation page, not proof of score credit."""
    if not expected_text or verification_visible(records):
        return False
    texts = sorted((r for r in records if 250 <= r['box'][1] < 1120),key=lambda r:r['box'][1])
    for index in range(len(texts)):
        for count in (1,2):
            block = texts[index:index+count]
            if not comment_text_matches(block, expected_text):
                continue
            y = block[-1]['box'][1]
            delete = [r for r in records if r['text'].strip() == '删除' and y < r['box'][1] < y+130]
            reply = [r for r in records if '回复' in r['text'] and y < r['box'][1] < y+130]
            if len(delete) == 1 and reply:
                return True
    return False


class CommentTasks:
    def _comment_native_life_page(self, records):
        headers=[r for r in records if r['text'].strip()=='科普中国' and 35<=r['box'][1]<110]
        tabs={r['text'].strip() for r in records if 110<=r['box'][1]<180}
        image=self.tasker.controller.cached_image
        if len(headers)!=1 or not {'生活百科','科学求真','三农科普'}<=tabs or image is None or image.shape[:2]!=(1280,720):
            return False
        labels=[r['box'] for r in records if r['text'].strip()=='生活百科' and 110<=r['box'][1]<180]
        if len(labels)!=1:return False
        x,y,w,h=labels[0];pixels=image[y:y+h,x:x+w,:3].astype('int16')
        red=(pixels[:,:,2]>160)&(pixels[:,:,2]>pixels[:,:,1]+50)&(pixels[:,:,2]>pixels[:,:,0]+50)
        return int(red.sum())>=12

    def _comment_life_feed(self, records):
        """Vertical scrolling can hide the verified Life subchannel header."""
        confirmed = getattr(self, '_comment_life_selected', False)
        if not comment_feed(records, allow_collapsed=confirmed):
            return False
        expanded = comment_feed(records)
        text, top, bottom = ('生活百科', 180, 250) if expanded else ('科普中国', 100, 180)
        labels = [r for r in records if r['text'].strip() == text and top <= r['box'][1] < bottom]
        image = self.tasker.controller.cached_image
        if len(labels) != 1 or image is None or image.shape[:2] != (1280, 720):
            return False
        x, y, width, height = labels[0]['box']
        pixels = image[y:y+height, x:x+width, :3].astype('int16')
        red = ((pixels[:, :, 2] > 160) & (pixels[:, :, 2] > pixels[:, :, 1]+50)
               & (pixels[:, :, 2] > pixels[:, :, 0]+50))
        if int(red.sum()) < 12:
            return False
        self._comment_life_selected = True
        return True

    def _comment_ocr(self, name, roi):
        self._check_active()
        self._guard_verification(resume_when=self._comment_resume_ready)
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
        self._guard_verification(resume_when=self._comment_resume_ready)
        if not self._app_in_foreground():
            raise TaskIncomplete('发表观点时应用不在前台')
        x, y, width, height = row['box']
        if not self.tasker.controller.post_click(round(x+width/2), round(y+height/2)).wait().succeeded:
            raise TaskIncomplete('发表观点触控未成功')
        self._sleep(.5)

    def _comment_retry_network(self, records):
        """Retry the failed page once, never the comment submission or a puzzle."""
        if not race_network_error(records):
            return False
        self._owner_resume_stable = 0
        now = time.monotonic()
        last = getattr(self, '_comment_network_retry_at', None)
        if last is not None and now-last < 4:
            return True
        if getattr(self, '_comment_network_retries', 0):
            raise HumanRequired('观点页面重试1次仍报告网络错误，保留现场，不重复发布')
        buttons = [row for row in records if row['text'].strip() == '重试' and row['box'][1] >= 500]
        if len(buttons) != 1 or not self._app_in_foreground():
            raise HumanRequired('观点网络错误页的重试按钮未确认，保留现场，不猜测点击')
        self._check_active()
        self._comment_network_retries = 1
        self._comment_network_retry_at = now
        x, y, width, height = buttons[0]['box']
        if not self.tasker.controller.post_click(round(x+width/2), round(y+height/2)).wait().succeeded:
            raise HumanRequired('观点网络错误页重试触控未成功，保留现场')
        report = getattr(self, 'run_report', None)
        if report is not None:
            report.task('发表观点', network_retries=1)
        self.send_log('观点：已点击当前网络错误页的重试（1/1）；若恢复访问验证仍需本人完成')
        return True

    def _comment_resume_ready(self):
        records = self._screen_records()
        if self._comment_retry_network(records):
            return False
        return self._owner_resume_ready(records=records)

    def _comment_wait(self, predicate, timeout=20, required=True):
        deadline = time.monotonic()+timeout
        paused = getattr(self, '_paused_seconds', 0)
        while time.monotonic() < deadline:
            records = self._screen_records()
            if self._comment_retry_network(records):
                self._sleep(.5)
                continue
            self._guard_verification(records, resume_when=self._comment_resume_ready)
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
            if not home_ready(records) and not self._comment_life_feed(records):
                raise TaskIncomplete('尚未确认首页频道栏，不点击频道菜单')
            self._comment_click(dict(box=[615, 120, 38, 44]))
        return self._comment_wait(lambda rs: any(r['text'].strip() == '全部频道' for r in rs))

    def _comment_open_article(self):
        self._comment_life_selected = False
        used_titles = CommentJournal().used_articles() + getattr(self, '_comment_rejected_titles', [])
        self._reset_home()
        records = self._comment_channel_menu(self._screen_records())
        self._comment_click(self._comment_label(records, '科普中国', 180, 1100))
        records = self._comment_wait(comment_feed)
        self._comment_click(self._comment_label(records, '生活百科', 180, 250))
        self._comment_wait(self._comment_life_feed)
        self._sleep(1)
        for page in range(3):
            records = self._screen_records()
            self._guard_verification(records, resume_when=self._comment_resume_ready)
            if not self._comment_life_feed(records):
                raise TaskIncomplete('未确认科普中国生活百科列表，未选择其他文章')
            boxes, kinds = self.detect()
            for box, kind in zip(boxes, kinds):
                if kind not in ('article', 'article_image') or not 250 <= box[1] < 1080:
                    continue
                title = self._article_card_text(box)
                if len(title) < 6 or any(s in title for s in ('直播', '专题', '订阅', '图集')):
                    continue
                if comment_card_used(title, used_titles):
                    self.send_log('观点：跳过已有发表尝试或本轮审核未通过的文章')
                    continue
                self._comment_click(dict(box=[int(box[0])+20, int(box[1])+30, min(350, int(box[2])-20), 50]))
                records = self._comment_wait(lambda rs: comment_article(rs, require_body=False) is not None, timeout=45)
                return comment_article(records, require_body=False)
            if page < 2:
                self.tasker.controller.post_swipe(350, 1090, 350, 550, 450).wait()
                self._sleep(.7)
        raise TaskUnavailable('生活百科未找到可确认正文的文章，本日不发表')

    def _comment_article_bar(self):
        self._check_active()
        if not self._app_in_foreground():
            raise TaskIncomplete('阅读全文时应用已离开前台，未继续翻动')
        rows = self._comment_ocr('观点阅读全文页核对', [20, 1200, 455, 75])
        if not any('发表观点' in row['text'] for row in rows):
            raise TaskIncomplete('未确认原文章输入栏，未继续翻动或输入')

    def _comment_article_top(self, title, pages):
        for _ in range(pages+3):
            records = self._screen_records()
            self._guard_verification(records, resume_when=self._comment_resume_ready)
            if verification_visible(records):
                records = self._screen_records()
            current = comment_article(records, require_body=False)
            if current and article_title_key(current['title']) == article_title_key(title):
                return
            self._comment_article_bar()
            if not self.tasker.controller.post_swipe(350, 350, 350, 1100, 600).wait().succeeded:
                raise TaskIncomplete('阅读全文后返回篇首触控失败，未输入短评')
            self._sleep(.6)
        raise TaskIncomplete('阅读全文后未重新确认同一篇标题，未输入短评')

    def _comment_read_full_article(self, article):
        lines, prior = [], []
        for page in range(12):
            records = self._screen_records()
            self._guard_verification(records, resume_when=self._comment_resume_ready)
            if verification_visible(records):
                records = self._screen_records()
            if page == 0:
                current_article = comment_article(records, require_body=False)
                if not current_article or article_title_key(current_article['title']) != article_title_key(article['title']):
                    raise TaskIncomplete('全文阅读开始前文章标题已变化，未继续翻动')
            self._comment_article_bar()
            current, end = article_page(records, first=page == 0)
            lines = append_page(lines, current, prior)
            prior = current
            body = '\n'.join(lines)
            self.send_log(f'观点阅读全文：第{page+1}屏，已收集{len(body)}字'+('，已确认文末' if end else ''))
            report = getattr(self, 'run_report', None)
            if report is not None:
                report.task('发表观点', article_reading=dict(title=article['title'], pages=page+1,
                            characters=len(body), end_observed=end))
            if len(re.sub(r'\s+', '', body)) > 4000:
                raise TaskUnavailable('全文超过本模块长度范围，未截断后发布')
            if end:
                full = dict(article, body=body)
                if not valid_article(full):
                    raise TaskUnavailable('全文不足或不适合授权短评范围，未发布')
                self._comment_article_top(article['title'], page)
                return full
            if page < 11:
                if not self.tasker.controller.post_swipe(350, 1100, 350, 500, 600).wait().succeeded:
                    raise TaskIncomplete('阅读全文翻页触控失败，未发布')
                self._sleep(.6)
        raise TaskUnavailable('十二屏内未确认文章结尾，未按部分正文发布')

    def _prepare_comment_draft(self):
        self._comment_rejected_titles = []
        for candidate in range(3):
            article = self._comment_open_article()
            try:
                article = self._comment_read_full_article(article)
            except TaskUnavailable as error:
                self._comment_rejected_titles.append(article['title'])
                self.send_log('观点：'+str(error)+'；换下一篇候选')
                continue
            self.send_log(f'观点候选（{candidate+1}/3）：按正文生成8～28字口语短评，再独立复核')
            drafts = ContextCommentDrafts(self.ai_resolver)
            self._comment_draft_engine = drafts
            draft = drafts.generate(article)
            self._check_active()
            if draft is not None:
                break
            if drafts.last_error not in ('draft_failed_local_rules', 'review_rejected'):
                raise TaskIncomplete('观点生成服务未正常返回：'+str(drafts.last_error)+'；未输入或发布')
            self._comment_rejected_titles.append(article['title'])
            self.send_log('观点：该文章短评未通过规则或复核，未输入、未发布；跳过本篇')
        else:
            raise TaskUnavailable('三篇候选均未通过观点规则或复核，本轮跳过，未发布')
        records = self._screen_records()
        current_article = comment_article(records, require_body=False)
        report = getattr(self, 'run_report', None)
        if report is not None:
            report.task('发表观点', article_identity=dict(expected=article['title'],
                        observed=current_article['title'] if current_article else None))
        if current_article is None or article_title_key(current_article['title']) != article_title_key(article['title']):
            diagnostic = getattr(self, 'diagnostics', None)
            if diagnostic is not None:
                diagnostic.capture('comment-identity', records, self.tasker.controller.cached_image)
            raise TaskIncomplete('生成短评后文章页面发生变化，未在其他文章输入')
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
        if not self.tasker.controller.post_input_text(draft['comment']).wait().succeeded:
            raise TaskIncomplete('观点短评未输入成功')
        # Input completion and actual redraw are separate; wait for the real text.
        self._comment_wait(lambda _: self._comment_publish_target(draft['comment']) is not None, timeout=15)
        self.send_log('观点草稿已核对：'+draft['comment']+'；文章：'+article['title'])
        self._last_comment_draft = dict(article=article, draft=draft)
        return self._last_comment_draft

    def _comment_publish_target(self, expected_text):
        records = self._comment_ocr('观点文字核对', [25, 1105, 670, 90])
        records += self._comment_ocr('观点发布按钮', [600, 1200, 100, 70])
        return comment_draft_ready(records, expected_text)

    def _comment_restore_home(self):
        self._guard_verification(resume_when=self._comment_resume_ready)
        records = self._screen_records()
        if home_ready(records):
            self._wait_home_ready(timeout=20)
            return
        if not self._comment_native_life_page(records):
            entry = self._comment_ocr('观点返回文章核对', [20, 1200, 455, 75])
            if not any('发表观点' in r['text'] for r in entry):
                raise TaskIncomplete('观点提交后未回到普通文章页，保留现场，不重复发表')
        # Article links and channel links can point back to each other in the
        # WebView history. Reuse the existing verification-aware home recovery.
        self.send_log('观点提交后已确认普通内容页，恢复首页并核分，不沿文章返回栈反复操作')
        self._go_home()

    def publish_comment(self, before=None):
        before = before or self.read_learning_score(['发表观点'])['发表观点']
        if is_complete(before):
            return before
        if before['maximum'] != 1:
            raise TaskIncomplete('发表观点计分规则已变化，需要核实')
        if not comment_policy()['enabled']:
            raise TaskUnavailable('按正文生成规则及每日发表范围尚未获本人确认，未发布')
        journal = CommentJournal()
        previous = journal.today()
        report = getattr(self, 'run_report', None)
        run_id = report.snapshot().get('run_id') if report is not None else None
        owner_retry = bool(previous and not getattr(self, 'unattended', True) and isinstance(run_id,str) and run_id)
        if previous and (not owner_retry or previous['state']=='credited'):
            raise TaskIncomplete('今天已有发表尝试记录，未重复发送；请查看积分与评论结果')
        if owner_retry:
            self.send_log('发表观点：本人手动发起补验，保留先前记录；本轮只提交一次，满分即停止')
        self._comment_network_retries = 0
        self._comment_network_retry_at = None
        self.send_log('发表观点：科普生活正文短评、规则检查及模型复核；无人值守每天一次尝试')
        prepared = self._prepare_comment_draft()
        self._check_active()
        article, draft = prepared['article'], prepared['draft']
        valid = validate_draft(dict(skip=False, **draft), article)
        button = self._comment_publish_target(draft['comment'])
        if valid is None or button is None or not comment_policy()['enabled']:
            raise TaskIncomplete('发布前短评或授权核对失败，未点击发布')
        if not journal.reserve(article['title'], draft['comment'], owner_retry=owner_retry, attempt_id=run_id):
            raise TaskIncomplete('今天已有发表尝试，未重复发送')
        # The durable reservation deliberately remains even if the click times out.
        self._comment_click(button)
        self.send_log('观点：已点击发布，等待编辑框响应与平台验证；是否加分以核分为准')
        self._comment_wait(lambda _: self._comment_publish_target(draft['comment']) is None)
        self.send_log('观点：编辑框已关闭，自动恢复首页并核分')
        self._comment_restore_home()
        self._score_refresh_required = True
        after = self.read_learning_score(['发表观点'])['发表观点']
        if after['maximum'] != before['maximum'] or after['value'] < before['value']:
            raise TaskIncomplete('观点核分规则发生变化，保留发表记录，不重复发送')
        if is_complete(after):
            journal.credited()
        self.send_log(f"发表观点核分：{before['value']}/{before['maximum']} → {after['value']}/{after['maximum']}；未满也不追加发表")
        return after
