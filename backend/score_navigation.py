"""Navigate the score list and read only visible, confirmed score rows."""
import time
import re
from datetime import datetime
from task_support import ALIASES, TaskIncomplete, TaskCancelled, parse_score_rows, visible_task_rows
from daily_flow import daily_scene, profile_page


class ScoreNavigation:
    def read_learning_score(self, targets):
        for attempt in range(3):
            if not self._enter_learning_score():
                raise TaskCancelled()
            if self._wait_score_data():
                self._dismiss_recent_media()
                scores = self._read_score_pages(targets)
                if all(name in scores for name in targets):
                    return scores
            if attempt < 2:
                self.send_log(f'积分页数据未完整加载，等待后重新打开核分（重试 {attempt+1}/2）')
                self._sleep(3)
        raise TaskIncomplete('积分页三次仍未读到所选项目实际分数，停止本次核分；请检查网络或页面')

    def _wait_score_data(self, timeout=30):
        deadline = time.monotonic() + timeout
        last_log = -10
        started = time.monotonic()
        while time.monotonic() < deadline:
            self._check_active()
            records = self._screen_records()
            self._guard_verification(records)
            if parse_score_rows(records):
                return True
            if any('网络开小差' in row['text'] for row in records):
                self.send_log('积分页报告网络开小差，尚无实际积分数据')
                return False
            elapsed = time.monotonic() - started
            if elapsed - last_log >= 10:
                self.send_log('正在等待积分页实际分数加载；标题出现不代表数据已就绪')
                last_log = elapsed
            self._sleep(1)
        return False

    def _read_score_pages(self, targets):
        captured_date = datetime.now().date()
        scores = {}
        inventory = {}
        for page in range(5):
            self._check_active()
            result = self.tasker.post_task("本机积分OCR", {"本机积分OCR": {
                "recognition": "OCR", "roi": [10, 100, 700, 1150],
                "action": "DoNothing", "timeout": 2000
            }}).wait().get()
            if result.status.succeeded and result.nodes:
                records = [dict(text=x.text, box=list(x.box)) for x in result.nodes[0].recognition.all_results]
                self._guard_verification(records)
                inventory.update({name:row["rules"] for name,row in visible_task_rows(records).items()})
                current = parse_score_rows(records)
                for name, score in current.items():
                    previous = scores.get(name)
                    if previous is None or (score["maximum"] == previous["maximum"] and score["value"] >= previous["value"]):
                        scores[name] = score
            else:
                self._guard_verification()
            if all(name in scores for name in targets):
                break
            if page < 4:
                self.tasker.controller.post_swipe(350, 1120, 350, 570, 600).wait()
                self._sleep(1)
        if datetime.now().date() != captured_date:
            raise TaskIncomplete("The calendar day changed during score verification")
        if getattr(self,"run_report",None):
            self.run_report.update(visible_task_rules=inventory)
        return scores

    def _scroll_score_to_start(self, records):
        for step in range(7):
            self._check_active()
            self._guard_verification(records)
            if daily_scene(records) != 'score' or not self._app_in_foreground():
                raise TaskIncomplete('积分列表回到起点时页面已改变，未继续滑动')
            texts={row['text'].strip() for row in records}
            if '登录' in texts:
                return
            below_start=(bool(texts & (set(ALIASES)-{'登录'} | {'订阅','发表观点'})) or
                         any(re.search(r'强国城上新|点点通|九点九尝鲜',text) for text in texts))
            if not below_start:
                # A title with no task/store content can still be loading.
                # Leave its existing data wait in charge; do not blindly swipe.
                return
            if step==6:
                raise TaskIncomplete('积分列表6次上移仍未确认登录卡片，已保留现场')
            if step==0:
                self.send_log('积分导航：列表已向下滚动，先回到登录卡片再读取积分')
            if not self.tasker.controller.post_swipe(360,420,360,1150,450).wait().succeeded:
                raise TaskIncomplete('积分列表回到起点的滑动未成功')
            self._sleep(.5)
            records=self._screen_records()

    def _enter_learning_score(self):
        records=self._screen_records()
        self._guard_verification(records)
        scene=daily_scene(records)
        if scene=='score' and self._app_in_foreground():
            if not getattr(self,'_score_refresh_required',False):
                self._scroll_score_to_start(records)
                self.send_log('积分导航：复用当前积分中心，从任务列表起点读取实际分数')
                return True
            self.send_log('积分导航：首次核分重新进入积分页，避免读取上次停留的旧分数')
            back=self.tasker.post_task('返回').wait().get()
            if back.status.failed:
                raise TaskIncomplete('首次核分未能离开旧积分页，未使用旧分数跳过任务')
            self._sleep(.5)
            records=self._screen_records()
            self._guard_verification(records)
            scene=daily_scene(records)
        if scene=='center' and self._app_in_foreground():
            self.tasker.post_task('返回').wait()
            self._sleep(.3)
            records=self._screen_records()
            self._guard_verification(records)
        if profile_page(records) and self._app_in_foreground():
            self.send_log('积分导航：从当前个人页直接进入学习积分')
            target=self.tasker.post_task('本机个人页学习积分',{'本机个人页学习积分':{
                'recognition':'OCR','expected':['^学习积分$'],'roi':[20,250,680,450],
                'action':'Click','timeout':5000,
            }}).wait().get()
            if target.status.failed:
                raise TaskIncomplete('当前个人页学习积分入口未确认')
        else:
            self.send_log("积分导航：确认首页，必要时重新打开应用")
            self._reset_home()
            self.tasker.controller.post_click(520, 72).wait()
        rule_result = self.tasker.post_task("积分规则", {"积分规则": {
            "recognition": "OCR", "expected": ["积分规则"],
            "roi": [0, 200, 720, 950], "action": "DoNothing", "timeout": 30000
        }}).wait().get()
        if rule_result.status.failed:
            self._guard_verification()
            self.send_log("积分页未加载，停止任务，请检查网络和登录状态")
            raise TaskIncomplete("Score page did not load")
        self._sleep(0.5)
        self._score_refresh_required=False
        return True
