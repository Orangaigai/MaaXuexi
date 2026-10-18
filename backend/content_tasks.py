"""Article and video execution, including real playback progress checks."""
import re
from random import randint
import numpy as np
from task_support import HumanRequired, TaskIncomplete, parse_video_progress, is_complete


def is_audio_collection(text):
    return bool(re.search(r'\d+条音频', text) or '原创音乐专辑' in text or '今晚陪你听' in text)


class ContentTasks:
    def _content_page_ready(self, kind):
        if not self._app_in_foreground():
            return False
        self._guard_verification()
        expected, roi = {
            "feed": (["推荐", "综合"], [0,90,300,180]),
            "article": (["发表观点", "写评论", "评论"], [0,1080,720,200]),
            "video": (["全屏观看", "听视频", "观点"], [0,680,720,600]),
        }[kind]
        result = self.tasker.post_task("本机内容页检查", {"本机内容页检查": {
            "recognition":"OCR", "expected":expected, "roi":roi,
            "action":"DoNothing", "timeout":1000
        }}).wait().get()
        return result.status.succeeded

    def _recover_content_home(self):
        if self._app_in_foreground() and self._captcha_present():
            self._wait_for_owner()
        self.send_log("页面已离开预期内容，自动重新打开应用并恢复首页")
        self._reset_home()

    def _article_card_text(self, box):
        result = self.tasker.post_task("本机文章卡片文字", {"本机文章卡片文字": {
            "recognition":"OCR", "roi":list(box), "action":"DoNothing", "timeout":1000
        }}).wait().get()
        if result.status.failed or not result.nodes:
            return ""
        return re.sub(r"\s+", "", "".join(item.text for item in result.nodes[0].recognition.all_results))

    def read_article(self, before_score=None):
        """Read a bounded batch, then let the actual score decide whether to stop."""
        score=before_score
        self._article_seen=set()
        metrics=dict(batches=[],articles_opened=0)
        for batch in range(3):
            if is_complete(score):return score
            remaining=(score['maximum']-score['value']) if isinstance(score,dict) else 12
            # A combined score does not reveal whether count or minutes are missing.
            # Use it only to choose a modest batch; never treat it as a count proof.
            size=min(6,max(1,(remaining+1)//2)) if batch==0 else min(2,max(1,remaining))
            if batch:self._reset_home()
            self.send_log(f'文章：本批最多{size}篇，结束后核分，满分立即停止')
            self._read_article_batch(size)
            score=self.read_learning_score(['选读文章']).get('选读文章')
            metrics['articles_opened']=len(self._article_seen)
            metrics['batches'].append(dict(size=size,score=score))
            report=getattr(self,'run_report',None)
            if report and '选读文章' in report.snapshot()['tasks']:
                report.task('选读文章',metrics=metrics,after=score)
            if not score:raise TaskIncomplete('文章批次结束后未能读取实际积分')
            if is_complete(score):
                self.send_log('文章积分已满，停止阅读，不继续累计固定等待时间')
                return score
        self.send_log('文章三批已结束，保留实际积分，继续其他任务')
        return score

    def _read_article_batch(self, article_limit):
        self.send_log("开始任务：选读文章")
        read_count = 0
        completed = 0
        rejected_cards = set()
        recoveries = 0
        def recover():
            nonlocal recoveries, empty_rounds
            recoveries += 1
            if recoveries > 3:
                raise TaskIncomplete("文章页面连续恢复失败，已停止本项任务")
            self._recover_content_home()
            empty_rounds = 0
        self.send_log("确认首页并进入 推荐/综合")
        home = self.tasker.post_task("综合", {"综合": {
            "recognition": "OCR", "expected": ["推荐", "综合"],
            "roi": [0, 100, 160, 80], "action": "Click", "timeout": 5000
        }}).wait().get()
        if home.status.failed:
            self.send_log("未能确认学习强国首页，停止任务，避免在错误界面翻动")
            raise RuntimeError("Home page was not recognized")
        self._sleep(randint(4, 5))
        empty_rounds = 0
        while completed < article_limit:
            if self.stop_flag:
                return
            if not self._content_page_ready("feed"):
                recover()
                continue
            empty_rounds += 1
            if empty_rounds > 12:
                raise TaskIncomplete("已检查多页列表但未找到可读文章，停止本项任务")
            # 识别文章，获取点击文章的坐标范围
            boxes, box_class = self.detect()
            image = self.tasker.controller.post_screencap().wait().get()
            # 没有文章就滑动屏幕
            if len(boxes) == 0 or (
                "article" not in box_class and "article_image" not in box_class
            ):
                self.send_log("未识别到文章，正在滑动屏幕")
                self.tasker.controller.post_swipe(
                    350, 1050, 350, 350, 450,
                ).wait()
                self._sleep(1.5)
                continue
            boxes, box_class = zip(
                *[
                    (box, cls)
                    for box, cls in zip(boxes, box_class)
                    if cls in ["article", "article_image"]
                ]
            )
            self.send_log(f"识别到{len(boxes)}篇文章")
            article_list = []
            for box in boxes:
                img = image[box[1] : box[1] + box[3], box[0] : box[0] + box[2]]
                article_list.append(img)

            for i in range(len(box_class)):
                if self.stop_flag:
                    return
                if not self._has_unread_text(article_list[i]):
                    continue
                signature = self._article_card_text(boxes[i])
                if (len(signature) < 4 or signature in rejected_cards or signature in self._article_seen or
                        is_audio_collection(signature) or
                        any(text in signature for text in ("全国学习平台", "进入学习强国PC版", "待订阅", "立即订阅"))):
                    continue
                read_count += 1
                empty_rounds = 0
                self.send_log(f"正在阅读第{read_count}篇文章")
                self._sleep(0.5)
                self.tasker.controller.post_click(boxes[i][0] + min(200, boxes[i][2]//2),
                                                 boxes[i][1] + min(55, boxes[i][3]//2)).wait()
                self._sleep(3)
                if not self._content_page_ready("article"):
                    rejected_cards.add(signature)
                    self.send_log("未确认文章正文，不计阅读时间，本轮跳过该卡片")
                    if not self._content_page_ready("feed"):
                        recover()
                    break
                confirmed=True
                for _ in range(3):
                    if self.stop_flag:
                        return
                    if not self._content_page_ready("article"):
                        confirmed=False
                        recover()
                        break
                    self.tasker.controller.post_swipe(
                        350, 1000, 350, 400, 600,
                    ).wait()
                    t = randint(20, 22)
                    self._sleep(t)
                self._sleep(1)
                if self._content_page_ready("article"):
                    self.tasker.post_task("返回").wait()
                    if confirmed:
                        completed+=1
                        self._article_seen.add(signature)
                if completed >= article_limit:return
                self._sleep(randint(3, 5))
                break
            if not self._content_page_ready("feed"):
                recover()
                continue
            self.tasker.controller.post_swipe(
                350, 1050, 350, 350, 450,
            ).wait()
            self._sleep(1.5)
        self.send_log("选读文章任务完成")

    def _has_unread_text(self, img: np.ndarray) -> bool:
        """检测文章卡片左上角 50%x50% 区域是否存在未读文章的黑色标题文字"""
        h, w = img.shape[:2]
        roi = img[: h // 2, : w // 2]
        # rgb(45, 51, 56) -> bgr(56, 51, 45)，容差 +-15
        lower = np.array([41, 36, 30], dtype=np.uint8)
        upper = np.array([71, 66, 60], dtype=np.uint8)
        mask = np.all((roi >= lower) & (roi <= upper), axis=2)
        count = np.count_nonzero(mask)
        unread = bool(count > 80)
        self.send_log(
            f"[颜色检测] 黑色像素={count}, 阈值=80, 判定={'未读' if unread else '已读'}"
        )
        return unread

    def watch_video(self, duration=400, alternate_feed=False):
        self.send_log("开始任务：视听学习")
        watch_count = 0
        waiting_time = 0
        recoveries = 0
        television = self.tasker.post_task("电视台").wait().get()
        if television.status.failed:
            self.send_log("未能进入电视台，停止任务，避免在错误界面翻动")
            raise RuntimeError("Television tab was not recognized")
        if alternate_feed:
            self.send_log("切换视野频道，寻找其他视频内容；最终仍核实实际积分")
            selected=self.tasker.post_task("本机视频视野频道",{"本机视频视野频道":{
                "recognition":"OCR","expected":["^视野$"],"roi":[0,100,720,90],
                "action":"Click","timeout":2000}}).wait().get()
            if selected.status.failed:
                raise TaskIncomplete("未确认视野视频频道，停止本项，避免在错误页面翻动")
        self._sleep(randint(3, 5))
        video_box = []
        empty_rounds = 0
        while not video_box:
            if self.stop_flag:
                return
            if not self._app_in_foreground():
                recoveries += 1
                if recoveries > 3:
                    raise TaskIncomplete("视频页面连续恢复失败，已停止本项任务")
                self._recover_content_home()
                television = self.tasker.post_task("电视台").wait().get()
                if television.status.failed:
                    raise TaskIncomplete("无法恢复电视台页面")
                self._sleep(3)
            empty_rounds += 1
            if empty_rounds > 12:
                self.send_log("连续未找到可播放视频，停止任务，请检查电视台页面")
                raise RuntimeError("No playable videos after 12 attempts")
            # 识别视频，获取点击视频的坐标范围
            boxes, box_class = self.detect()
            image = self.tasker.controller.post_screencap().wait().get()
            # 没有视频就滑动屏幕
            if not any(kind in ("video", "video_big") for kind in box_class):
                self.send_log("未识别到视频，正在滑动屏幕")
                self.tasker.controller.post_swipe(
                    350, 1050, 350, 350, 450,
                ).wait()
                self._sleep(1.5)
                continue
            boxes, box_class = zip(
                *[(box, cls) for box, cls in zip(boxes, box_class) if cls in ("video", "video_big")]
            )
            self.send_log(f"识别到{len(boxes)}个视频")
            for box,kind in zip(boxes,box_class):
                img = image[box[1] : box[1] + box[3], box[0] : box[0] + box[2]]
                # Large cards place their title below the thumbnail; its upper
                # pixels cannot be used as the old small-card unread marker.
                if kind != "video_big" and not self._has_unread_text(img):
                    continue
                else:
                    video_box = box
                    break
            if video_box:
                break
            if empty_rounds >= 3:
                video_box=boxes[0]
                self.send_log("积分未满但当前视频均标记已看，打开已识别的视频补看；是否得分仍以积分页为准")
                break
            self.send_log("本页未找到未看视频，正在滑动屏幕")
            self.tasker.controller.post_swipe(
                350, 1050, 350, 350, 450,
            ).wait()
            self._sleep(1.5)
        self._sleep(0.5)
        self.tasker.controller.post_click(video_box[0] + 150, video_box[1] + 10).wait()
        self._sleep(3)
        while waiting_time < duration:
            if self.stop_flag:
                return
            if not self._content_page_ready("video"):
                raise TaskIncomplete("未确认视频播放页，已停止计时；请查看模拟器提示")
            watch_count += 1
            if watch_count > 30:
                raise TaskIncomplete("视频有效播放进度不足，已停止本项任务")
            self.send_log(f"正在播放第{watch_count}个视频")
            t = self._watch_video_segment(min(65, duration-waiting_time))
            if not self._content_page_ready("video"):
                raise TaskIncomplete("视频播放页面已丢失，已停止计时和滑动")
            waiting_time += t
            self.send_log(f"本片有效播放进度{t}秒，累计{waiting_time}秒")
            self.tasker.controller.post_swipe(
                350, 1050, 350, 350, 400,
            ).wait()
            self._sleep(2)
        self.tasker.post_task("返回").wait()
        self.send_log("视听学习任务完成")

    def _video_progress(self):
        for attempt in range(2):
            self._guard_verification()
            detail = self.tasker.post_task("本机视频播放进度", {"本机视频播放进度": {
                "recognition":"OCR", "roi":[470,1190,250,90], "action":"DoNothing", "timeout":1000
            }}).wait().get()
            if detail.status.succeeded and detail.nodes:
                items = sorted(detail.nodes[0].recognition.all_results,key=lambda item:item.box[0])
                progress = parse_video_progress("".join(item.text for item in items))
                if progress:
                    return progress
            self._sleep(1)
        raise TaskIncomplete("无法读取视频实际播放进度，已停止计时")

    def _watch_video_segment(self, limit):
        try:
            previous = self._video_progress()
        except HumanRequired:
            raise
        except TaskIncomplete:
            if not self._content_page_ready('video'):
                raise
            diagnostic = getattr(self, 'diagnostics', None)
            if diagnostic is not None:
                records = self._screen_records()
                self._guard_verification(records)
                diagnostic.capture('video-progress-missing', records, self.tasker.controller.cached_image)
            self.send_log('本片起始进度不可读，切换下一片；未确认时间不计入')
            return 0
        watched, stalled = 0, 0
        while watched < limit and previous["position"] < previous["total"]:
            self._check_active()
            if not self._content_page_ready("video"):
                raise TaskIncomplete("视频播放页面已丢失，已停止计时和滑动")
            wait = min(8, limit-watched, previous["total"]-previous["position"])
            self._sleep(wait)
            try:
                current = self._video_progress()
            except HumanRequired:
                raise
            except TaskIncomplete:
                if watched <= 0 or not self._content_page_ready("video"):
                    raise
                self.send_log(f"本片进度暂不可读，保留已确认的{watched}秒，切换下一片；未确认时间不计入")
                return watched
            if current["total"] != previous["total"] or current["position"] < previous["position"]:
                break
            delta = min(current["position"]-previous["position"],wait+2)
            if delta <= 0:
                stalled += 1
                if stalled >= 3:
                    self.send_log("视频进度连续停滞，切换下一片；空等不计时")
                    break
            else:
                watched += delta
                stalled = 0
            previous = current
        return watched
