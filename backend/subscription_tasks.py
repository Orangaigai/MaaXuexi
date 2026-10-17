"""Subscribe one account at a time; verify server points and clean only owned follows."""
import re
import time
import json
from pathlib import Path
from PIL import Image
from daily_flow import verification_visible, daily_scene, profile_page
from home_navigation import home_ready
from task_support import TaskIncomplete, HumanRequired, is_complete
from subscription_store import SubscriptionStore

CATEGORIES = ('待订阅', '上新', '今年上线', '主要央媒', '行业媒体', '高校', '党刊', '地方媒体', '社会机构')
MAX_ATTEMPTS = 16
PAGES_PER_CATEGORY = 5


def subscription_scene(records):
    if verification_visible(records):
        return None
    texts = {re.sub(r'\s+', '', row['text']) for row in records}
    if '我的订阅' in texts and '添加' in texts:
        return 'mine'
    if {'强国号', '地方平台'} <= texts:
        return 'catalog'
    return None


def owned_list_button(records, name, state='已订阅'):
    """Match an exact journalled name to its own right-hand list button."""
    if not name or subscription_scene(records) != 'mine':
        return None
    names = [r for r in records if re.sub(r'\s+', '',r['text']) == name
             and 90 <= r['box'][0] < 590 and 175 <= r['box'][1] < 1175]
    if len(names) != 1:
        return None
    y = names[0]['box'][1]
    buttons = [r for r in records if r['text'].strip() == state
               and r['box'][0] >= 600 and y-8 <= r['box'][1] <= y+65]
    return buttons[0] if len(buttons) == 1 else None


def cancellation_action(records):
    if verification_visible(records) or not any(re.search('取消.*订阅',r['text']) for r in records):
        return None
    confirm=[r for r in records if r['text'].strip() in ('确定','确认') and r['box'][1]>=400]
    if len(confirm)==1:
        return confirm[0]
    actions=[r for r in records if r['text'].strip()=='取消订阅' and r['box'][1]>=700]
    return actions[0] if len(actions)==1 else None


def subscription_accounts(records):
    """Pair a header with its own button; exclude article titles and noisy logos."""
    result = []
    for button in records:
        box = button['box']
        state = re.sub(r'\s+', '', button['text'])
        if state not in ('订阅', '已订阅') or box[0] < 600 or not 120 < box[1] < 1190:
            continue
        y = box[1] + box[3]/2
        names = [row for row in records if 208 <= row['box'][0] < 580
                 and abs(row['box'][1] + row['box'][3]/2 - y) <= 18
                 and row['box'][3] >= 16 and row['text'].strip() not in ('订阅', '已订阅')]
        if len(names) != 1:
            continue
        name = re.sub(r'\s+', '', names[0]['text'])
        if len(name) < 2 or '…' in name or '...' in name:
            continue
        result.append(dict(name=name, kind='强国号', state=state, button=button))
    return sorted(result, key=lambda row:row['button']['box'][1])


def category_selected(records, image, category):
    """The native left rail marks only its active category with a red vertical bar."""
    rows=[r for r in records if r['box'][0]<140 and r['text'].strip()==category]
    if len(rows)!=1 or image is None or image.shape[:2]!=(1280,720):
        return False
    _,y,_,height=rows[0]['box']
    pixels=image[max(120,y-15):min(1200,y+height+15),:10,:3].astype('int16')
    if not pixels.size:
        return False
    blue,green,red=pixels[:,:,0],pixels[:,:,1],pixels[:,:,2]
    return int(((red>180)&(red>green+60)&(red>blue+60)).sum())>=20


class SubscriptionTasks:
    def _sub_evidence(self, tag, records):
        report = getattr(self,'run_report',None)
        if report is None:
            return
        try:
            folder = Path('logs') / ('subscriptions-' + report.snapshot()['run_id'])
            folder.mkdir(parents=True,exist_ok=True)
            (folder/(tag+'.json')).write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            Image.fromarray(self.tasker.controller.cached_image[:,:,::-1]).save(folder/(tag+'.png'))
        except (OSError,ValueError,TypeError,AttributeError):
            pass  # Diagnostic output cannot change the subscription result.

    def _sub_records(self):
        self._check_active()
        records = self._screen_records()
        if verification_visible(records):
            self._sub_verification_seen = True
            self._sub_evidence('verification',records)
        self._guard_verification(records)
        return self._screen_records() if verification_visible(records) else records

    def _sub_wait(self, predicate, timeout=12):
        deadline = time.monotonic() + timeout
        paused_seconds = getattr(self,'_paused_seconds',0)
        while time.monotonic() < deadline:
            records = self._sub_records()
            total_paused = getattr(self,'_paused_seconds',0)
            deadline += max(0,total_paused-paused_seconds)
            paused_seconds = total_paused
            if predicate(records):
                return records
            self._sleep(.4)
        self._sub_evidence('unrecognized',records)
        raise TaskIncomplete('订阅页面未确认，已保留现场和订阅记录')

    def _sub_click(self, row):
        self._check_active()
        self._guard_verification()
        if not self._app_in_foreground():
            raise TaskIncomplete('订阅操作时应用不在前台')
        x,y,w,h = row['box']
        if not self.tasker.controller.post_click(round(x+w/2), round(y+h/2)).wait().succeeded:
            raise TaskIncomplete('订阅页面触控未成功')
        self._sleep(.6)

    def _sub_label(self, records, label, left=0, right=720, top=35, bottom=1240):
        found = [r for r in records if r['text'].strip() == label
                 and left <= r['box'][0] < right and top <= r['box'][1] < bottom]
        if len(found) != 1:
            raise TaskIncomplete('订阅页面未找到唯一的“'+label+'”')
        self._sub_click(found[0])

    def _sub_back(self):
        self._check_active()
        before = self._sub_records()
        scene = subscription_scene(before) or daily_scene(before)
        if self.tasker.post_task('返回').wait().get().status.failed:
            raise TaskIncomplete('订阅页面返回未成功')
        self._sub_wait(lambda rows:(subscription_scene(rows) or daily_scene(rows)) != scene)

    def _sub_mine(self):
        """Use the recognized back stack, avoiding an app restart for every point check."""
        for _ in range(6):
            records = self._sub_records()
            scene = subscription_scene(records)
            if scene == 'mine':
                if cancellation_action(records) is not None:
                    # A menu opened outside this cleanup has no recorded owner.
                    self._sub_label(records,'关闭',top=1000)
                    return self._sub_wait(lambda rows:subscription_scene(rows)=='mine'
                                          and cancellation_action(rows) is None)
                return records
            if scene == 'catalog' or daily_scene(records) == 'score':
                self._sub_back()
            elif profile_page(records):
                self._sub_label(records, '订阅', top=180, bottom=290)
                return self._sub_wait(lambda rows:subscription_scene(rows) == 'mine')
            elif home_ready(records):
                self._sub_open_profile(records)
            else:
                raise TaskIncomplete('订阅返回路径不明确，未重开应用或盲目点击')
        raise TaskIncomplete('未能到达我的订阅，已保留页面')

    def _sub_open_profile(self, records):
        if not home_ready(records) or not self._app_in_foreground():
            raise TaskIncomplete('订阅个人页入口只允许在已确认首页操作')
        self._check_active()
        self._guard_verification(records)
        task = '订阅个人页入口'
        result = self.tasker.post_task(task,{task:{
            'recognition':'OCR','expected':['^我的$'],'roi':[642,35,78,80],
            'action':'Click','timeout':5000,'pre_delay':0,'post_delay':0,
        }}).wait().get()
        if result.status.failed:
            self._sub_evidence('profile-entry-unrecognized',self._sub_records())
            raise TaskIncomplete('首页右上角我的入口未识别，已保留画面')
        return self._sub_wait(profile_page)

    def _sub_catalog(self):
        records = self._sub_records()
        if subscription_scene(records) == 'catalog':
            return records
        # The live score-card button stayed inert; My/Add is the observed working route.
        records = self._sub_mine()
        self._sub_label(records, '添加', left=560, top=35, bottom=100)
        return self._sub_wait(lambda rows:subscription_scene(rows) == 'catalog')

    def _sub_score(self):
        records = self._sub_records()
        if subscription_scene(records):
            self._sub_mine()
            self._sub_back()  # My subscriptions -> profile -> fresh score center.
        self._score_refresh_required = True
        return self.read_learning_score(['订阅'])['订阅']

    def _sub_candidate(self, store):
        self._sub_catalog()
        start = getattr(self, '_sub_category_cursor', 0)
        observed = set()
        for offset in range(len(CATEGORIES)):
            category_index = (start + offset) % len(CATEGORIES)
            category = CATEGORIES[category_index]
            records = self._sub_records()
            if subscription_scene(records) != 'catalog':
                raise TaskIncomplete('选择订阅分类前页面已改变')
            self._sub_select_category(records,category)
            seen_pages = set()
            for _ in range(PAGES_PER_CATEGORY):
                records = self._sub_records()
                if subscription_scene(records) != 'catalog':
                    raise TaskIncomplete('扫描订阅候选时页面已改变')
                accounts = subscription_accounts(records)
                observed.update((a['kind'],a['name']) for a in accounts)
                signature = tuple((a['name'],a['state']) for a in accounts)
                for account in accounts:
                    if account['state'] == '订阅' and store.eligible(account['name'], account['kind']):
                        account['category_index'] = category_index
                        return account
                if not accounts or signature in seen_pages:
                    break
                seen_pages.add(signature)
                # Move only the right account list, never the left category rail.
                if not self.tasker.controller.post_swipe(450,1120,450,420,450).wait().succeeded:
                    raise TaskIncomplete('订阅候选列表滑动失败')
                self._sleep(.4)
        self._sub_scan_observed = len(observed)
        return None

    def _sub_select_category(self, records, category):
        if subscription_scene(records)!='catalog' or not self._app_in_foreground():
            raise TaskIncomplete('选择订阅分类前未确认目录页')
        self._check_active();self._guard_verification(records)
        task='订阅分类入口'
        result=self.tasker.post_task(task,{task:{
            'recognition':'OCR','expected':['^'+re.escape(category)+'$'],
            'roi':[0,120,140,1060],'action':'Click','timeout':8000,
            'pre_delay':0,'post_delay':0,
        }}).wait().get()
        if result.status.failed:
            self._sub_evidence('category-entry-unrecognized',self._sub_records())
            raise TaskIncomplete('订阅分类未加载或无法识别：'+category)
        self._sleep(.8)
        return self._sub_wait(lambda rows:subscription_scene(rows)=='catalog' and
            category_selected(rows,self.tasker.controller.cached_image,category))

    def _sub_follow(self, account, store, before):
        records = self._sub_records()
        candidates = [a for a in subscription_accounts(records)
                      if a['name'] == account['name'] and a['state'] == '订阅']
        if subscription_scene(records) != 'catalog' or len(candidates) != 1:
            raise TaskIncomplete('订阅账号位置或状态已改变，未发送订阅')
        store.begin(account['name'], account['kind'])
        self._sub_verification_seen = False
        self.send_log('订阅：'+account['name']+'；随后单独核分')
        self._sub_click(candidates[0]['button'])
        self._sub_records()  # Detect an actual owner verification before leaving.
        after = self._sub_score()
        self.run_report.task('订阅',after=after)
        self._sub_evidence('score-after-follow',self._sub_records())
        if (getattr(self, '_sub_verification_seen', False) and after['value'] == before['value']
                and not self._sub_follow_saved(account['name'])):
            raise HumanRequired('验证页已离开，但本次关注未确认保存；停止新增订阅，待本人处理')
        if after['maximum'] != before['maximum'] or after['value'] < before['value']:
            raise TaskIncomplete('订阅积分规则或分数发生异常变化，停止新增订阅')
        self.send_log(f"订阅核分：{before['value']}/{before['maximum']} → {after['value']}/{after['maximum']}")
        return after

    def _sub_follow_saved(self, name):
        self._sub_mine()
        self._sleep(2)
        records = self._sub_records()
        if subscription_scene(records) != 'mine':
            raise TaskIncomplete('验证后未确认我的订阅页面，保留待取消记录')
        matches = [r for r in records if 175 <= r['box'][1] < 1175
                   and re.sub(r'\s+','',r['text']) == name]
        if len(matches) == 1:
            self.send_log('验证后已确认关注保存；本次未加分，继续其它候选')
            return True
        if any(r['text'].strip() == '暂无更多内容' for r in records):
            return False
        raise TaskIncomplete('验证后非空列表未能唯一定位新增关注，保留记录和页面')

    def _sub_find_owned(self, name):
        seen_pages = set()
        for _ in range(20):
            records = self._sub_records()
            if subscription_scene(records) != 'mine':
                raise TaskIncomplete('清理新增订阅时页面已改变，未继续操作')
            if any(r['text'].strip() == '暂无更多内容' for r in records):
                self._sleep(1)
                if any(r['text'].strip() == '暂无更多内容' for r in self._sub_records()):
                    return None
            names = [r for r in records if 90 <= r['box'][0] < 590
                     and 175 <= r['box'][1] < 1175 and re.sub(r'\s+','',r['text']) == name]
            if len(names) > 1:
                raise TaskIncomplete('我的订阅出现同名账号，未猜测取消对象')
            if names:
                button = self._sub_list_button(records,name)
                if button is None:
                    raise TaskIncomplete('新增关注的列表按钮未能唯一对应，保留待取消记录')
                return button if button['text'].strip()=='已订阅' else None
            signature = tuple((r['text'],tuple(r['box'])) for r in records if r['box'][1] >= 175)
            if signature in seen_pages:
                return None
            seen_pages.add(signature)
            if not self.tasker.controller.post_swipe(360,1100,360,400,450).wait().succeeded:
                raise TaskIncomplete('我的订阅列表滑动失败')
            self._sleep(.6)
        raise TaskIncomplete('新增关注未在可扫描范围找到，保留待取消记录')

    def _sub_list_button(self, records, name):
        button = owned_list_button(records,name) or owned_list_button(records,name,'订阅')
        if button is not None:
            return button
        if subscription_scene(records) != 'mine':
            return None
        names = [r for r in records if re.sub(r'\s+','',r['text']) == name
                 and 90 <= r['box'][0] < 590 and 175 <= r['box'][1] < 1175]
        if len(names) != 1:
            return None
        self._check_active();self._guard_verification(records)
        if not self._app_in_foreground():
            raise TaskIncomplete('识别订阅状态时应用不在前台')
        task='订阅列表状态'
        detail=self.tasker.post_task(task,{task:{
            'recognition':'OCR','expected':['^(?:已)?订阅$'],
            'roi':[615,names[0]['box'][1]+12,72,60],
            'action':'DoNothing','timeout':2000,'pre_delay':0,'post_delay':0,
        }}).wait().get()
        if detail.status.failed or not detail.nodes:
            return None
        local=[dict(text=r.text,box=list(r.box)) for r in getattr(detail.nodes[0].recognition,'all_results',[])]
        isolated=names+local+[r for r in records if r['text'].strip() in ('我的订阅','添加')]
        return owned_list_button(isolated,name) or owned_list_button(isolated,name,'订阅')

    def _sub_row_cancelled(self, records, name):
        if subscription_scene(records) != 'mine' or cancellation_action(records) is not None:
            return False
        names=[r for r in records if 90 <= r['box'][0] < 590 and 175 <= r['box'][1] < 1175
               and re.sub(r'\s+','',r['text'])==name]
        if not names:
            return True
        button=self._sub_list_button(records,name)
        return button is not None and button['text'].strip()=='订阅'

    def _sub_cleanup(self, store):
        """Look up owned attempts by full name in My subscriptions, never cancel all."""
        pending = store.pending()
        if not pending:
            return
        self.send_log(f'正在我的订阅核对并清理 {len(pending)} 项本机新增关注')
        self._sub_mine()
        # Wait for the freshly opened list, not the previous cached empty frame.
        self._sleep(2)
        cancelled = []
        while pending:
            records = self._sub_records()
            visible = [(owned_list_button(records,a['name']),a) for a in pending]
            visible = [(button,a) for button,a in visible if button is not None]
            if visible:
                found,attempt = min(visible,key=lambda pair:pair[0]['box'][1])
            else:
                attempt = pending[0]
                found = self._sub_find_owned(attempt['name'])
            pending.remove(attempt)
            if found is None:
                self._sub_evidence('cleanup-absent-'+attempt['id'][:12],self._sub_records())
                store.cleaned(attempt['id'])
                self.send_log('已核实我的订阅中没有：'+attempt['name'])
                continue
            # Stay on My subscriptions; do not enter the publisher's content page.
            self._sub_click(found)
            dialog = self._sub_wait(lambda rows:cancellation_action(rows) is not None)
            self._sub_click(cancellation_action(dialog))
            result = self._sub_wait(lambda rows:self._sub_row_cancelled(rows,attempt['name']))
            self._sub_evidence('cancelled-'+attempt['id'][:12],result)
            cancelled.append(attempt)
            self.send_log('已在列表取消：'+attempt['name']+'；继续处理下一项')
        if not cancelled:
            return
        # One reload for the whole batch, rather than entering/exiting per account.
        self._sub_back()
        self._sub_mine()
        self._sleep(2)
        self._sub_confirm_removed({a['name'] for a in cancelled})
        for attempt in cancelled:
            count = store.cleaned(attempt['id'],completed=True)
            self.send_log(f"已取消本机新增订阅：{attempt['name']}；累计完整操作 {count} 次")

    def _sub_confirm_removed(self, names):
        seen_pages = set()
        for _ in range(20):
            records = self._sub_records()
            if subscription_scene(records) != 'mine':
                raise TaskIncomplete('统一核对取消结果时未确认我的订阅')
            present={re.sub(r'\s+','',r['text']) for r in records
                     if 90 <= r['box'][0] < 590 and 175 <= r['box'][1] < 1175} & names
            if any(not self._sub_row_cancelled(records,name) for name in present):
                raise TaskIncomplete('统一刷新后仍有待取消账号，保留记录，未增加完成次数')
            if any(r['text'].strip() == '暂无更多内容' for r in records):
                return
            signature = tuple((r['text'],tuple(r['box'])) for r in records if r['box'][1] >= 175)
            if signature in seen_pages:
                return
            seen_pages.add(signature)
            if not self.tasker.controller.post_swipe(360,1100,360,400,450).wait().succeeded:
                raise TaskIncomplete('统一核对订阅列表滑动失败')
            self._sleep(.6)
        raise TaskIncomplete('统一核对超出可扫描范围，保留待取消记录')

    def subscribe(self):
        store = SubscriptionStore()
        self._sub_verification_seen = False
        progress = store.progress()
        self._sub_category_cursor = progress['category_cursor'] % len(CATEGORIES)
        self._sub_scan_observed = 0
        round_id = progress['current_round']
        score = self._sub_score()
        # Recover a previous interrupted cleanup even if today's points are already full.
        if store.pending():
            self._sub_cleanup(store)
        attempted = 0
        exhausted = False
        try:
            while not is_complete(score) and attempted < MAX_ATTEMPTS:
                self._check_active()
                account = self._sub_candidate(store)
                if account is None:
                    exhausted = True
                    self.send_log('本轮可扫描范围暂无未尝试候选；清理后保存下一轮进度，本次不循环重跑')
                    break
                before_value = score['value']
                score = self._sub_follow(account, store, score)
                category_index = account.get('category_index',self._sub_category_cursor)
                self._sub_category_cursor = ((category_index+1) % len(CATEGORIES)
                                             if score['value'] == before_value else category_index)
                store.save_category_cursor(self._sub_category_cursor)
                attempted += 1
                self.run_report.task('订阅', metrics=dict(attempted=attempted,round_id=round_id,**store.stats()))
            self._sub_cleanup(store)
            if exhausted and store.finish_round(self._sub_scan_observed):
                self.send_log(f'第{round_id}轮已结束，历史保留；下次开始第{round_id+1}轮')
            self.run_report.task('订阅', after=score,metrics=dict(attempted=attempted,round_id=round_id,**store.stats()))
            self.send_log(f"订阅积分已核实：{score['value']}/{score['maximum']}；本机新增关注已清理，取消后不再重复核分")
            return score
        except HumanRequired:
            # A validation that expired may leave a normal score page. Clean only
            # journalled follows there; never leave or interact with a live CAPTCHA.
            if store.pending() and not verification_visible(self._screen_records()):
                try:
                    self._sub_cleanup(store)
                except HumanRequired:
                    raise
                except (TaskIncomplete,OSError) as error:
                    self.send_log('订阅待取消记录保留：'+str(error)[:160])
            raise
        finally:
            self.run_report.task('订阅', subscription_history=store.stats())
