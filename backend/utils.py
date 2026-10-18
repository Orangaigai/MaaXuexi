import re
import json
import os
import subprocess
import time
import traceback
from queue import SimpleQueue
from random import randint

import numpy as np
import plyer
from PIL import Image, ImageDraw, ImageFont
from PIL import ImageFilter
from maa.controller import AdbController
from maa.custom_recognition import CustomRecognition
from maa.define import TaskDetail, MaaAdbInputMethodEnum
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit


from answer_client import AIResolver
from task_support import ALIASES, RunReport, TaskCancelled, TaskIncomplete, TaskUnavailable, TaskSkipped, HumanRequired, is_complete, fun_first_round_credited, read_report, ordered_tasks
from datetime import datetime
from pathlib import Path
from device_support import online, prepare_emulator
from fun_challenge import FunChallenge
from daily_hints import question_from_records, exact_hint_choice, exact_hint_parts
from daily_text import choice_page, complete_hint, request_text
from screen_reading import read_screen
from blank_hints import blank_context, hint_paragraph, exact_blank_hint
from diagnostics import RunDiagnostics, observed, record_time
from daily_flow import verification_visible, completion_box, daily_scene, finish_daily_quiz, resume_ready, profile_page, final_submission_evidence, acceptance_ready
from home_navigation import HomeNavigation
from content_tasks import ContentTasks, is_audio_collection
from score_navigation import ScoreNavigation
from subscription_tasks import SubscriptionTasks, subscription_scene
from subscription_store import SubscriptionStore
from local_channel import LocalChannelTasks
from comment_tasks import CommentTasks, comment_article, comment_posted


os.makedirs("logs/maa", exist_ok=True)
Tasker.set_log_dir("logs/maa")
Tasker.set_save_draw(False)
resource = Resource()
resource.set_cpu()
resource.post_bundle("./resource").wait()

DEBUG_MODE = False
OWNER_WAIT_SECONDS = 10 * 60


def _dilate(mask: np.ndarray, kh: int, kw: int) -> np.ndarray:
    """形态学膨胀：用 kh×kw 矩形核连接相邻像素"""
    ph, pw = kh // 2, kw // 2
    padded = np.pad(mask, ((ph, ph), (pw, pw)), mode="constant", constant_values=False)
    h, w = mask.shape
    result = np.zeros_like(mask)
    for di in range(kh):
        for dj in range(kw):
            result |= padded[di : di + h, dj : dj + w]
    return result


def strip_punct(s):
    return re.sub(r"\W", "", s)


def detect_click_option_boxes(image: np.ndarray) -> list[list[int]]:
    """Locate separate neutral-grey button borders, independent of text length."""
    if image is None or image.shape[:2] != (1280, 720):
        raise TaskIncomplete("点选填空题截图尺寸不受支持")
    crop = image[270:1100, 40:690, :3].astype(np.int16)
    lo, hi = crop.min(axis=2), crop.max(axis=2)
    mask = (lo >= 165) & (hi <= 238) & (hi-lo <= 35)
    # Join antialiased border gaps; adjacent buttons remain separate.
    mask = np.asarray(Image.fromarray(mask.astype(np.uint8)*255).filter(
        ImageFilter.MaxFilter(3))) > 0
    height, width = mask.shape
    boxes = []
    for sy, sx in zip(*np.nonzero(mask)):
        if not mask[sy, sx]:
            continue
        mask[sy, sx] = False
        stack = [(int(sy), int(sx))]
        xmin = xmax = int(sx)
        ymin = ymax = int(sy)
        while stack:
            y, x = stack.pop()
            xmin, xmax = min(xmin,x), max(xmax,x)
            ymin, ymax = min(ymin,y), max(ymax,y)
            for ny,nx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny,nx]:
                    mask[ny,nx] = False
                    stack.append((ny,nx))
        w,h = xmax-xmin+1,ymax-ymin+1
        if 35 <= w <= 330 and 45 <= h <= 130:
            boxes.append([xmin+40,ymin+270,w,h])
    return sorted(boxes,key=lambda box:(box[1],box[0]))


class RedTextOCR(CustomRecognition):
    """提示红字OCR自定义识别器。
    处理流程：
    1. 裁剪提示区域(0,483,720,517)
    2. 红色度过滤：R - max(B,G) > 50，非红色像素置白，只保留红色文字
    3. 估算文字行高，确定形态学膨胀核大小（水平方向膨胀连接同行字符）
    4. 形态学膨胀 → 行列扫描分块：将相连的红色像素聚合成文本块
    5. 逐块裁剪处理后图片，调用MAA内置OCR(扫描选项)识别文字
    6. 出血线合并：处理跨行文本（提示区域左右边缘的文本块合并为同一行）
    """

    # 提示区域在屏幕上的位置和大小
    ROI_X, ROI_Y, ROI_W, ROI_H = 0, 483, 720, 517
    # 出血线阈值：文本块右边缘>=682 且 下一块左边缘<=37 时合并（处理跨行文本）
    LEFT_BLEED, RIGHT_BLEED, BLEED_TH = 37, 682, 5

    def analyze(self, context, argv: CustomRecognition.AnalyzeArg):
        img = argv.image
        rx, ry, rw, rh = self.ROI_X, self.ROI_Y, self.ROI_W, self.ROI_H
        hint_img = img[ry : ry + rh, rx : rx + rw]

        # 1. 红色度过滤：R - max(B, G) > 50，非红色像素置白
        redness = hint_img[:, :, 2].astype(np.int16) - np.maximum(
            hint_img[:, :, 0], hint_img[:, :, 1]
        ).astype(np.int16)
        red_mask = redness > 50
        processed = np.full_like(hint_img, 255)
        processed[red_mask] = hint_img[red_mask]

        # 2. 估算文字行高，确定膨胀核大小
        rows_with_red = np.any(red_mask, axis=1)
        line_heights = []
        lh_start = None
        for i, has in enumerate(rows_with_red):
            if has and lh_start is None:
                lh_start = i
            elif not has and lh_start is not None:
                line_heights.append(i - lh_start)
                lh_start = None
        if lh_start is not None:
            line_heights.append(len(rows_with_red) - lh_start)
        if not line_heights:
            if DEBUG_MODE:
                print("[RedTextOCR] 无红色像素")
            return CustomRecognition.AnalyzeResult(
                box=[0, 0, 1, 1], detail={"texts": []}
            )
        text_h = int(np.median(line_heights))
        kh, kw = 1, max(5, text_h)  # 垂直不膨胀，水平按行高膨胀连接同行文字

        # 3. 形态学膨胀 + 行列扫描分块
        dilated = _dilate(red_mask, kh, kw)
        rows_with_text = np.any(dilated, axis=1)
        line_ranges = []
        start = None
        for i, has_text in enumerate(rows_with_text):
            if has_text and start is None:
                start = i
            elif not has_text and start is not None:
                if i - start > 5:
                    line_ranges.append((start, i))
                start = None
        if start is not None and len(rows_with_text) - start > 5:
            line_ranges.append((start, len(rows_with_text)))
        blocks = []
        for r_start, r_end in line_ranges:
            cols_with_text = np.any(dilated[r_start:r_end], axis=0)
            col_start = None
            for j, has_text in enumerate(cols_with_text):
                if has_text and col_start is None:
                    col_start = j
                elif not has_text and col_start is not None:
                    if j - col_start > 5:
                        blocks.append(
                            [
                                max(0, col_start - 5),
                                max(0, r_start - 5),
                                j - col_start + 10,
                                r_end - r_start + 10,
                            ]
                        )
                    col_start = None
            if col_start is not None and len(cols_with_text) - col_start > 5:
                blocks.append(
                    [
                        max(0, col_start - 5),
                        max(0, r_start - 5),
                        len(cols_with_text) - col_start + 10,
                        r_end - r_start + 10,
                    ]
                )
        if not blocks:
            if DEBUG_MODE:
                print("[RedTextOCR] 无文本块")
            return CustomRecognition.AnalyzeResult(
                box=[0, 0, 1, 1], detail={"texts": []}
            )

        # 4. 逐块裁剪处理后图片，调用OCR识别
        texts = []
        for idx, block in enumerate(blocks):
            bx, by, bw, bh = block
            crop = processed[max(0, by) : by + bh, max(0, bx) : bx + bw].copy()
            reco = context.run_recognition("扫描选项", crop)
            text = ""
            if reco and reco.best_result:
                text = reco.best_result.text.strip()
            if DEBUG_MODE:
                print(f'[RedTextOCR] block[{idx}] roi={block} => "{text}"')
            texts.append(text)

        # 5. 保存调试图片（处理后图片 + 绿框标注）
        if DEBUG_MODE:
            vis = Image.fromarray(processed[:, :, ::-1]).convert("RGB")
            draw = ImageDraw.Draw(vis)
            try:
                font = ImageFont.truetype("msyh.ttc", 14)
            except Exception:
                font = ImageFont.load_default()
            palette = [
                (200, 60, 60),
                (60, 140, 200),
                (60, 180, 75),
                (200, 150, 40),
                (140, 80, 180),
                (200, 100, 60),
                (40, 160, 160),
                (180, 60, 140),
            ]
            for idx, block in enumerate(blocks):
                bx, by, bw, bh = block
                color = palette[idx % len(palette)]
                draw.rectangle([bx, by, bx + bw, by + bh], outline=color, width=2)
                label = texts[idx] if idx < len(texts) else ""
                draw.text((bx, by - 16), label, fill=color, font=font)
            os.makedirs("debug", exist_ok=True)
            vis.save("debug/hint_processed.png")
            if DEBUG_MODE:
                print("[RedTextOCR] 已保存 debug/hint_processed.png")

        # 6. 出血线合并：当前块右边缘靠右出血线 且 下一块左边缘靠左出血线，则合并
        i = 0
        while i < len(texts) - 1:
            right_edge = blocks[i][0] + blocks[i][2]
            left_edge = blocks[i + 1][0]
            if (
                right_edge >= self.RIGHT_BLEED - self.BLEED_TH
                and left_edge <= self.LEFT_BLEED + self.BLEED_TH
            ):
                texts[i] += texts[i + 1]
                nb = blocks[i + 1]
                bx = min(blocks[i][0], nb[0])
                by = min(blocks[i][1], nb[1])
                blocks[i] = [
                    bx,
                    by,
                    max(blocks[i][0] + blocks[i][2], nb[0] + nb[2]) - bx,
                    max(blocks[i][1] + blocks[i][3], nb[1] + nb[3]) - by,
                ]
                del texts[i + 1]
                del blocks[i + 1]
            else:
                i += 1
        result = [t for t in texts if t]
        if DEBUG_MODE:
            print(f"[RedTextOCR] 最终结果: {result}")
        return CustomRecognition.AnalyzeResult(
            box=[0, 0, 1, 1], detail={"texts": result}
        )


resource.register_custom_recognition("RedTextOCR", RedTextOCR())


class MaaWorker(FunChallenge, HomeNavigation, ContentTasks, ScoreNavigation, SubscriptionTasks, LocalChannelTasks, CommentTasks):
    def __init__(
        self,
        queue: SimpleQueue,
        api_key,
        model: str,
    ):
        Tasker.set_log_dir("logs/maa")

        self.queue = queue
        self.tasker = Tasker()
        self.connected = False
        self.api_key = api_key
        self.ai_resolver = AIResolver(
            api_key=api_key,
            model=model,
            stop_requested=lambda: self.stop_flag,
        )
        self.stop_flag = False
        self.pause_flag = False
        self._manual_pause_requested = False
        self._manual_hold = False
        self._verification_wait_active = False
        self.unattended = False
        self.fast_answer = False
        self.last_task_error = None
        self.run_report = None
        self._deadline = None
        self.send_log("MAA初始化成功")

    def update_ai_models(
        self,
        model: str | None = None,
    ):
        self.ai_resolver = AIResolver(
            api_key=self.api_key,
            model=model or self.ai_resolver.model,
            stop_requested=lambda: self.stop_flag,
        )

    def send_log(self, msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())} {msg}"
        self.queue.put(line)
        try:
            Path("logs").mkdir(exist_ok=True)
            with Path(time.strftime("logs/events-%Y%m%d.log")).open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            pass
        time.sleep(0.05)

    def connection_alive(self):
        return bool(self.connected and online(getattr(self, "_adb_command", None)))

    def prepare_device(self):
        def progress(message):
            if self.run_report:
                self.run_report.update(phase=message)
            self.send_log(message)
        self.connected = False
        adb, address = prepare_emulator(progress, self._check_active)
        progress("正在连接自动操作控制器")
        devices = Toolkit.find_adb_devices()
        device = next((item for item in devices if item.address == address), None)
        if device is None:
            devices = Toolkit.find_adb_devices(specified_adb=adb)
            device = next((item for item in devices if item.address == address), None)
        if device is None or not self.connect_device(device, open_from_launcher=False):
            raise TaskIncomplete("自动操作控制器连接失败，请检查模拟器后重新执行")
        if not self.connection_alive():
            raise TaskIncomplete("模拟器连接在准备期间断开，请重新执行")
        progress("设备已就绪，正在启动应用并核对今日积分")

    def pause(self, reason="需要本人查看模拟器并处理提示", resume_when=None, *, manual=False):
        unattended = getattr(self, "unattended", False)
        if unattended and resume_when is None and not manual:
            self.pause_flag = False
            reason += "；本轮已结束，处理后请在网页重新执行"
            self.send_log("无人值守需要本人处理，本轮结束：" + reason)
            raise HumanRequired(reason)
        if resume_when is not None:
            reason += "；正常页面恢复后自动继续本轮，无需点网页执行或继续"
            if unattended:
                reason += "；最多等待10分钟，超时保留结果并通知"
        self.pause_flag = True
        self._manual_hold = manual
        self._verification_wait_active = resume_when is not None and not manual
        report = getattr(self, "run_report", None)
        if report:
            report.update(status="paused", pause_reason=reason, resume_automatically=resume_when is not None,
                          pause_kind='manual' if manual else 'verification')
        self.send_log("任务暂停：" + reason)
        try:
            if not manual:
                message = "请在 MuMu 本人处理验证或登录，完成后程序自动继续" if resume_when is not None else "任务暂停，请查看模拟器提示并在网页继续"
                plyer.notification.notify(title="MaaXuexi", message=message, app_name="MaaXuexi", timeout=60)
        except Exception as error:
            if report:
                report.update(notification_error=type(error).__name__)
        started = time.monotonic()
        next_probe = started
        wait_deadline = started + OWNER_WAIT_SECONDS if unattended and not manual else None
        last_tick = started
        try:
            while self.pause_flag and not self.stop_flag:
                time.sleep(0.1)
                if self.stop_flag:
                    break
                self._check_active()
                current = time.monotonic()
                if getattr(self, '_manual_hold', False):
                    if wait_deadline is not None:
                        wait_deadline += current-last_tick
                    last_tick = current
                    continue
                last_tick = current
                if wait_deadline is not None and current >= wait_deadline:
                    raise HumanRequired("等待本人处理已满10分钟，本轮结束；已完成积分保留，处理后重新执行")
                if resume_when is not None and current >= next_probe:
                    try:
                        if resume_when():
                            self.pause_flag = False
                    except HumanRequired:
                        raise
                    except (TaskIncomplete, OSError):
                        pass
                    next_probe = time.monotonic() + 1
        finally:
            paused_seconds = time.monotonic() - started
            record_time(self,'等待本人处理',paused_seconds)
            self._paused_seconds = getattr(self, "_paused_seconds", 0) + paused_seconds
            if getattr(self, "_deadline", None):
                self._deadline += paused_seconds
            self.pause_flag = False
            self._manual_hold = False
            self._verification_wait_active = False
        self._check_active()
        if report:
            report.update(status="running", pause_reason=None, resume_automatically=False, pause_kind=None)

    @staticmethod
    def get_device():
        adb_devices = []
        config = json.loads(Path("config/emulator.json").read_text("utf-8-sig"))
        devices = Toolkit.find_adb_devices()
        if not devices:
            devices = Toolkit.find_adb_devices(specified_adb=config["adb_path"])
        for device in devices:
            # 这两个字段的数字在JS里会整数溢出，转为字符串处理
            device.input_methods = str(device.input_methods)
            device.screencap_methods = str(device.screencap_methods)
            if device not in adb_devices:
                adb_devices.append(device)
        return adb_devices

    def connect_device(self, device, *, open_from_launcher=True):
        self.connected = False
        controller = AdbController(
            adb_path=device.adb_path,
            address=device.address,
            screencap_methods=int(device.screencap_methods),
            input_methods=int(MaaAdbInputMethodEnum.AdbShell),
            config=device.config,
        )
        status = controller.post_connection().wait().succeeded
        if not status:
            plyer.notification.notify(
                title="MaaXuexi",
                message="设备连接失败，请检查终端日志",
                app_name="MaaXuexi",
                timeout=30,
            )
            self.send_log("设备连接失败，请检查终端日志")
            return self.connected
        if self.tasker.bind(resource, controller):
            self.connected = True
            self._adb_command = (str(device.adb_path), "-s", str(device.address))
            self.send_log("设备连接成功")
            self.send_log("控制方式：保留 MuMu 专用截图配置，使用 ADB 兼容触控")
            if not open_from_launcher or not self._open_from_android_launcher():
                self.send_log("设备已连接，保留当前页面；开始任务后自动导航")
        else:
            plyer.notification.notify(
                title="MaaXuexi",
                message="设备连接失败，请检查终端日志",
                app_name="MaaXuexi",
                timeout=30,
            )
            self.send_log("设备连接失败，请检查终端日志")
        return self.connected

    def detect(self):
        result: TaskDetail = self.tasker.post_task("yolo_detect").wait().get()
        if result.status.failed:
            return [], []
        details = result.nodes[0].recognition.raw_detail["all"]
        boxes, labels = [], []
        for detail in details:
            boxes.append(detail["box"])
            labels.append(detail["label"])
        return list(boxes), list(labels)

    def task(self, tasks, fast_answer=False, debug=False, unattended=False, daily_acceptance=False, verification_first=False):
        global DEBUG_MODE
        DEBUG_MODE = debug
        tasks = ordered_tasks(tasks,verification_first=verification_first)
        self.last_task_error = None
        self.fast_answer = fast_answer
        self.unattended = unattended
        self._fun_network_retry_count = 0
        self._fun_network_retry_at = None
        self._fun_metrics = {}
        self._score_refresh_required = True
        self._deadline = time.monotonic() + 45 * 60
        self._run_date = datetime.now().date()
        previous = read_report() or {}
        self._previous_run_report = previous
        self.run_report = RunReport(tasks)
        self.diagnostics = RunDiagnostics(self.run_report,self.send_log)
        if isinstance(getattr(self,'ai_resolver',None),AIResolver):
            self.ai_resolver.timing_hook=lambda seconds:record_time(self,'模型请求',seconds)
        self.run_report.update(unattended=unattended)
        if verification_first:
            self.run_report.update(verification_first=True)
            self.send_log('本轮临时先执行验证相关任务，随后自动继续文章、视听与本地；每日默认顺序不变')
        if daily_acceptance:
            self.run_report.update(acceptance_test='daily_fresh_round', new_points_claimed=False)
        current = None
        self.send_log("已接到执行请求，开始准备设备")
        try:
            self._check_active()
            if any(name not in ALIASES for name in tasks):
                raise TaskIncomplete("Unsupported task")
            if daily_acceptance and (unattended or tasks != ['每日答题']):
                raise TaskIncomplete('单组验收仅支持手动运行每日答题')
            self.prepare_device()
            if '本地频道' in tasks:
                self._local_clear_location_prompt()
            if previous.get("status") in ("stopped","partial","failed") and any(name not in ALIASES for name in previous.get("tasks",{})):
                self.send_log("已停止的旧功能不再继续，返回首页核对保留任务")
                self._reset_home()
            else:
                self._finish_existing_quiz()
            before = self.read_learning_score(tasks)
            fun_rules = self.run_report.snapshot().get('visible_task_rules',{}).get('趣味答题','')
            for name in tasks:
                self.run_report.task(name, before=before.get(name))
            for current in tasks:
                self._check_active()
                if current == '趣味答题' and fun_first_round_credited(before.get(current),fun_rules):
                    self.run_report.task(current,state='credited',after=before[current],
                        reason='今日对战首局积分已领取8/10；按平台仅首局计分，不重复匹配')
                    self.send_log('趣味答题今日首局8分已领取，跳过，不重复比赛')
                    continue
                acceptance_round = daily_acceptance and current == '每日答题'
                cleanup_pending = current == '订阅' and bool(SubscriptionStore().pending())
                if is_complete(before.get(current)) and not acceptance_round and not cleanup_pending:
                    self.run_report.task(current, state="skipped_full", after=before[current])
                    self.send_log(current + "今日已满分，跳过")
                    continue
                if acceptance_round:
                    self.run_report.task(current, acceptance_round=True, new_points_claimed=False)
                    self.send_log('每日答题实测：只运行一组新题，检验自动完成和返回；不申报新增积分')
                self.run_report.task(current, state="running")
                self.run_report.update(phase="正在执行：" + current)
                try:
                    if current in ("登录","选读文章","视听学习"):
                        self._reset_home()
                    self.run_report.update(phase="正在执行：" + current)
                    if current == "选读文章":
                        article_score = self.read_article(before.get(current))
                    elif current == "视听学习":
                        score = before.get(current)
                        remaining = score["maximum"] - score["value"] if score else 12
                        self.watch_video(duration=max(80, min(400, remaining * 70)),alternate_feed=remaining<12)
                    elif current == "每日答题":
                        self.daily_answer()
                    elif current == "趣味答题":
                        self.funny_answer()
                    elif current == "订阅":
                        subscription_score = self.subscribe()
                    elif current == '本地频道':
                        local_score = self.learn_local_channel(before.get(current))
                    elif current == '发表观点':
                        comment_score = self.publish_comment(before.get(current))
                    elif current != "登录":
                        raise TaskUnavailable("No handler for selected task")
                    self._check_active()
                    if acceptance_round:
                        evidence = self.run_report.snapshot()['tasks'][current]
                        if not acceptance_ready(evidence):
                            raise TaskIncomplete('实测未确认五题、程序提交、结果页和自动返回，未将满分跳过记作通过')
                        self.run_report.task(current, acceptance_flow_verified=True)
                    self.send_log('订阅流程结束，积分已核实，新增关注已清理' if current == '订阅'
                                  else current + "流程结束，正在核实积分")
                    after = (article_score if current == '选读文章' and isinstance(article_score,dict)
                             else subscription_score if current == '订阅' else local_score if current == '本地频道'
                             else comment_score if current == '发表观点'
                             else self.read_learning_score([current]).get(current))
                    if current == "视听学习" and after and not is_complete(after):
                        remaining = after["maximum"] - after["value"]
                        self.send_log(f"视频积分还差{remaining}分，自动补充观看后再次核实")
                        self.run_report.update(phase="正在补充视频并复核积分")
                        self._reset_home()
                        self.watch_video(duration=max(80, min(200, remaining * 70)),alternate_feed=True)
                        after = self.read_learning_score([current]).get(current)
                    self.run_report.task(current, after=after)
                    if current == '趣味答题' and fun_first_round_credited(after,fun_rules):
                        self.run_report.task(current,state='credited',
                            reason='对战首局实际领取8/10，未获胜；按平台仅首局计分，不重复匹配')
                        self.send_log('趣味答题：已核实首局积分8/10，本日计分局完成，不重复匹配')
                        continue
                    if not is_complete(after):
                        state = "unverified" if after is None else "incomplete"
                        self.run_report.task(current, state=state)
                        self.send_log(current + "未确认满分，保留实际结果，继续其它所选任务")
                        continue
                    self.run_report.task(current, state="verified")
                    self.send_log(f"{current}积分已核实：{after['value']}/{after['maximum']}")
                except (TaskCancelled, HumanRequired):
                    raise
                except TaskSkipped as error:
                    self.run_report.task(current, state='skipped_mode', reason=str(error)[:240],
                                         after=before.get(current))
                    self.send_log(current+'：'+str(error))
                except Exception as error:
                    self._check_active()
                    if (current in ("选读文章", "视听学习")
                            and isinstance(error, (TaskIncomplete, RuntimeError, OSError))
                            and not isinstance(error, TaskUnavailable)):
                        try:
                            after = self.read_learning_score([current]).get(current)
                            self.run_report.task(current,after=after)
                            if is_complete(after):
                                self.run_report.task(current,state='verified')
                                self.send_log(current+'流程报错，但实际积分已满；记录结果并继续')
                                continue
                        except (TaskCancelled, HumanRequired):
                            raise
                        except Exception:
                            self._check_active()
                    state = "unavailable" if isinstance(error,TaskUnavailable) else "failed"
                    reason = str(error)[:240] if isinstance(error,TaskIncomplete) else "请查看后台日志与模拟器页面"
                    self.run_report.task(current,state=state,error=type(error).__name__,reason=reason)
                    self.send_log(current+"未完成，已记录原因，继续其它所选任务")
                    traceback.print_exc()
                finally:
                    if current == "趣味答题" and hasattr(self, "_fun_metrics"):
                        self.run_report.task(current, metrics=self._fun_metrics)
            states = [row["state"] for row in self.run_report.snapshot()["tasks"].values()]
            if all(state in ("verified","skipped_full","credited","skipped_mode") for state in states):
                self.run_report.finish("completed")
                if daily_acceptance:
                    self.send_log('每日答题单组实测完成：五题、自动完成和返回均已确认；既有积分仅复核')
                elif 'skipped_mode' in states:
                    self.send_log('本轮执行结束：对战模式已按设置跳过，其余所选任务积分已核实')
                else:
                    self.send_log("所有所选任务已核实完成；对战首局按实际领取积分记录" if 'credited' in states else "所有所选任务积分已核实完成")
            else:
                self.last_task_error = "TaskIncomplete"
                self.run_report.finish("partial")
                self.send_log("本轮结束：部分任务未满分或未能核实，请查看逐项结果")
        except HumanRequired as error:
            reason = str(error)[:240]
            self.last_task_error = "HumanRequired"
            self.pause_flag = False
            blocked = current or self.run_report.snapshot().get('blocked_task')
            self.run_report.update(reason=reason,requires_human=True,blocked_task=blocked)
            for name,row in self.run_report.snapshot()["tasks"].items():
                if row["state"] == "running":
                    self.run_report.task(name,state="needs_human",reason=reason)
                elif row['state'] == 'pending' and is_complete(row.get('before')):
                    self.run_report.task(name,state='skipped_full',after=row['before'],
                        reason='验证前已核实今日满分，无需继续执行')
                elif row["state"] == "pending":
                    self.run_report.task(name,state="deferred",reason="本轮需要本人处理，未继续此项")
            self.run_report.finish("needs_human")
            self.send_log("本轮已结束：等待本人处理；已完成项目保留，未继续操作验证页面")
        except TaskCancelled:
            for name, row in self.run_report.snapshot()["tasks"].items():
                if row['state'] == 'pending' and is_complete(row.get('before')):
                    self.run_report.task(name,state='skipped_full',after=row['before'],
                        reason='中止前已核实今日满分，无需继续执行')
                    continue
                if row["state"] in ("pending", "running"):
                    self.run_report.task(name, state="stopped")
            self.run_report.finish("stopped")
            self.send_log("任务已终止")
        except Exception as error:
            self.last_task_error = type(error).__name__
            reason = str(error)[:240] if isinstance(error, TaskIncomplete) else "任务准备失败，请检查模拟器连接与运行日志"
            self.run_report.update(error=type(error).__name__, reason=reason)
            for name, row in self.run_report.snapshot()["tasks"].items():
                if row["state"] in ("pending", "running"):
                    self.run_report.task(name, state="failed", reason=reason)
            self.run_report.finish("failed")
            self.send_log("任务未完成：" + reason)
            traceback.print_exc()
        finally:
            self._deadline = None
            self._run_date = None
            diagnostic=getattr(self,'diagnostics',None)
            if isinstance(diagnostic,RunDiagnostics):diagnostic.flush()
            self.run_report.update(resume_automatically=False)
            self._summarize_run()

    def _summarize_run(self):
        report = self.run_report.snapshot()
        completed = [name for name, row in report["tasks"].items() if row["state"] in ("verified", "skipped_full", "credited")]
        skipped = [name for name, row in report['tasks'].items() if row['state'] == 'skipped_mode']
        unfinished = [name for name in report["tasks"] if name not in completed and name not in skipped]
        self.run_report.update(completed_tasks=completed, skipped_tasks=skipped, unfinished_tasks=unfinished)
        if report["status"] not in ("partial", "failed", "needs_human"):
            return
        message = "待处理/未确认完成：" + "、".join(unfinished) + "。"
        if completed:
            message += "已核实：" + "、".join(completed) + "。"
        if report.get("reason"):
            message += report["reason"][:80] + "。"
        message += "详情保留在任务控制台，处理后重新执行。"
        self.run_report.update(attention_message=message, notification_attempted=True)
        self.send_log(message)
        try:
            plyer.notification.notify(title="MaaXuexi 待处理", message=message[:220], app_name="MaaXuexi", timeout=60)
        except Exception as error:
            self.run_report.update(notification_error=type(error).__name__)
            self.send_log("桌面通知未能显示，待处理清单已保留在控制台和最近运行记录中")

    def _check_active(self):
        if self.stop_flag:
            raise TaskCancelled()
        if getattr(self, '_manual_pause_requested', False) and not self.pause_flag:
            self._manual_pause_requested = False
            self.pause('已由你暂停，点击控制台“继续执行”恢复', manual=True)
        if getattr(self, "_run_date", None) and datetime.now().date() != self._run_date:
            raise TaskIncomplete("The calendar day changed during the task")
        if not getattr(self, "pause_flag", False) and getattr(self, "_deadline", None) and time.monotonic() > self._deadline:
            raise TaskIncomplete("Task time limit exceeded")

    def _sleep(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._check_active()
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        self._check_active()

    def request_stop(self):
        self.stop_flag = True
        self.pause_flag = False
        self._manual_pause_requested = False
        self._manual_hold = False
        self.tasker.post_stop()

    def request_pause(self):
        if self.pause_flag:
            self._manual_hold = True
        else:
            self._manual_pause_requested = True

    def request_continue(self):
        self._manual_pause_requested = False
        self._manual_hold = False
        waiting = getattr(self, '_verification_wait_active', False)
        # The owner observer must still confirm a normal page. A Continue click
        # never dismisses or marks a verification challenge as completed.
        if not waiting:
            self.pause_flag = False
        return waiting

    def _reset_home(self):
        return self._go_home()


    def _captcha_present(self, records=None):
        # Detection only. Never click, infer, or submit a verification challenge.
        return verification_visible(self._screen_records() if records is None else records)

    def _guard_verification(self, records=None, resume_when=None):
        for _ in range(3):
            self._check_active()
            if not self._captcha_present(records):
                return
            self._wait_for_owner("平台访问验证：请在模拟器内本人完成", resume_when=resume_when)
            records = None
        raise TaskIncomplete("平台验证仍未完成，停止当前操作")

    def _wait_for_owner(self, reason="平台访问验证：请在模拟器内本人完成", resume_when=None):
        self._owner_resume_stable = 0
        self.pause(reason, resume_when=resume_when or self._owner_resume_ready)

    def _owner_resume_ready(self, records=None):
        """Observe a known app page after owner action; never interact with CAPTCHA."""
        from four_player import detect_fun_mode, race_finished, race_match_expired, race_network_error
        records = self._screen_records() if records is None else records
        if self._captcha_present(records):
            self._owner_resume_stable = 0
            return False
        normal = daily_scene(records, pending=True) in (
            'completion', 'result', 'center', 'profile', 'score', 'home', 'network')
        question = any(row['text'].strip() in ('单选题', '多选题', '填空题', '点选填空题')
                       and 130 <= row['box'][1] < 440 for row in records)
        content = any(row['text'].strip() in ('全屏观看', '听视频', '发表观点', '写评论', '评论')
                      and 680 <= row['box'][1] < 1280 for row in records)
        comment = getattr(self, '_last_comment_draft', None)
        if comment and (comment_article(records) is not None or
                        comment_posted(records, comment.get('draft', {}).get('comment'))):
            content = True
        competition = (detect_fun_mode(records, True) in ('四人赛', '双人对战')
                       or race_finished(records) or race_match_expired(records)
                       or bool(race_network_error(records)))
        ready = bool(normal or question or content or competition or subscription_scene(records))
        self._owner_resume_stable = getattr(self, '_owner_resume_stable', 0) + 1 if ready else 0
        if self._owner_resume_stable < 2 or not self._app_in_foreground():
            return False
        self.send_log('本人处理页面已离开，已确认应用页面，自动继续原任务')
        return True

    def _completion_button(self, action="DoNothing", records=None):
        if records is None or action in ('Click','Press'):
            records=self._screen_records()
        box=completion_box(records,pending=getattr(self,'_daily_completion_expected',False))
        if box is None:
            return False
        if action in ('Click','Press'):
            self._check_active()
            if not self._app_in_foreground():
                return False
            x,y,w,h=box
            x,y=round(x+w/2),round(y+h/2)
            # AdbShell Click uses Android input tap (DOWN/UP at the same time).
            # A stationary 120 ms press is only the existing recovery attempt.
            job=(self.tasker.controller.post_swipe(x,y,x,y,120) if action=='Press'
                 else self.tasker.controller.post_click(x,y))
            if not job.wait().succeeded:
                raise TaskIncomplete('每日答题完成触控命令失败，已停止发送并保留现场')
            metrics=getattr(self,'_daily_finish_metrics',None)
            if metrics is not None:
                metrics.setdefault('finish_inputs',[]).append(dict(
                    action='stationary_press' if action=='Press' else 'tap',
                    point=[x,y],duration_ms=120 if action=='Press' else 0))
            return True
        return True

    def _daily_resume_ready(self):
        return resume_ready(self)

    def _finish_existing_quiz(self):
        # MaaFW 5.12.2 omits the ctypes signatures for post_shell on Windows.
        # Use the already connected ADB executable for this read-only query.
        command = getattr(self, "_adb_command", None)
        if not command:
            return
        try:
            output = subprocess.run(
                [*command, "shell", "dumpsys", "activity", "activities"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=8, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
        except (OSError, subprocess.SubprocessError):
            self.connected = False
            raise TaskIncomplete("模拟器设备连接已断开，请点击执行重新连接") from None
        if not isinstance(output, str) or not any(
            "cn.xuexi.android/" in line and re.search(r"(?:topResumedActivity|mResumedActivity)=", line)
            for line in output.splitlines()
        ):
            return
        if self._resume_fun_submission(getattr(self, '_previous_run_report', {})):
            return
        previous = getattr(self, '_previous_run_report', {})
        pending_daily = previous.get('tasks', {}).get('每日答题', {})
        recovering_daily = (pending_daily.get('completion_pending') is True or
                            (previous.get('status') == 'needs_human' and
                             (previous.get('blocked_task') == '每日答题' or
                              (previous.get('blocked_task') is None and list(previous.get('tasks',{})) == ['每日答题']))))
        for attempt in range(3):
            if not self._captcha_present():
                break
            if recovering_daily:
                self.pause('每日答题真实访问验证，请本人完成',resume_when=self._daily_resume_ready)
            else:
                self._wait_for_owner()
        else:
            raise TaskIncomplete("Existing verification is still pending")
        fifth = self.tasker.post_task("第五题", {"第五题": {
            "roi": [500, 150, 220, 280], "timeout": 700
        }}).wait().get()
        self._daily_completion_expected = fifth.status.succeeded or recovering_daily
        if (fifth.status.succeeded or recovering_daily) and self._completion_button():
            report = getattr(self, 'run_report', None)
            if report and '每日答题' in report.snapshot()['tasks']:
                report.task('每日答题', state='running', questions_confirmed=5, completion_pending=True)
                report.update(blocked_task='每日答题', phase='每日答题：提交已有第五题结果')
            self.send_log("发现已作答的第五题，自动提交现有这一轮")
            self._finish_quiz(True)
        elif recovering_daily and daily_scene(self._screen_records(),pending=True) in ('result','center','profile','score','home','network','loading'):
            self.send_log('恢复每日答题收尾：保留当前提交页面，按实际画面处理并核分')
            self._finish_quiz(True)

    def _app_in_foreground(self):
        command = getattr(self, "_adb_command", None)
        if not command:
            return False
        try:
            result = subprocess.run([*command, "shell", "dumpsys", "activity", "activities"],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=8, check=True, creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        except (OSError, subprocess.SubprocessError):
            self.connected = False
            raise TaskIncomplete("模拟器设备连接已断开，请点击执行重新连接") from None
        return any("cn.xuexi.android/" in line and re.search(r"(?:topResumedActivity|mResumedActivity)=", line)
                   for line in result.stdout.splitlines())


    @observed('每日答题：等待页面响应')
    def _check_quiz_transition(self, index):
        if index == 4:
            # The fifth question may be scrolled below its heading after submission.
            # Preserve the confirmed position before recognizing its Finish control.
            self._daily_completion_expected = True
            for attempt in range(3):
                self._check_active()
                wrong = self.tasker.post_task("下一题检测").wait().get()
                if wrong.status.succeeded:
                    return False
                if self._captcha_present():
                    return True
                if self._completion_button():
                    return True
                result = self.tasker.post_task("本机答题结束检测", {"本机答题结束检测": {
                    "recognition": "OCR", "expected": ["答对.*题", "获得.*积分", "本次答题", "答题完成", "完成答题", "再来一组", "再答一次", "积分规则"],
                    "roi": [0, 100, 720, 1100], "action": "DoNothing", "timeout": 1000
                }}).wait().get()
                if result.status.succeeded:
                    return True
                self._sleep(1)
            raise TaskIncomplete("Final quiz submission was not confirmed")
        next_name = ["第二题", "第三题", "第四题", "第五题"][index]
        for attempt in range(2):
            self._check_active()
            next_question = self.tasker.post_task(next_name, {next_name: {"timeout": 3500, "roi": [500, 150, 220, 280]}}).wait().get()
            if next_question.status.succeeded:
                return True
            wrong = self.tasker.post_task("下一题检测").wait().get()
            if wrong.status.succeeded:
                return False
            if self._captcha_present():
                self._wait_for_owner()
                continue
            break
        raise TaskIncomplete("Next quiz question was not confirmed")

    def _finish_quiz(self, proceed_next):
        return finish_daily_quiz(self)


    def _navigate_to_daily_answer(self):
        """导航到每日答题页面：执行一系列MAA任务点击进入答题界面。"""
        self.send_log("开始任务：每日答题")
        return self._click_daily_answer_button()

    def _screen_records(self):
        self._check_active()
        started = time.monotonic()
        self._last_screen_read_started = started
        records = read_screen(self)
        self._last_screen_read_seconds = time.monotonic()-started
        return records

    def _open_daily_quiz(self):
        # The score WebView can route a click to another task on this device.
        # Use the app's native profile entry and the unscrolled quiz-center card.
        started=time.monotonic()
        records=self._screen_records()
        self._guard_verification(records)
        entry_skip=0
        if self._app_in_foreground():
            scene=daily_scene(records)
            if scene=='score' or any(row['text'].strip() in ('四人赛','双人对战') and row['box'][1]<440 for row in records):
                self.send_log('每日答题：从当前积分/对战入口返回，复用答题中心或个人页')
                self.tasker.post_task('返回').wait()
                self._sleep(.3)
                records=self._screen_records()
                self._guard_verification(records)
            if daily_scene(records)=='center':
                entry_skip=2
                self.send_log('每日答题：当前已在答题中心，直接进入每日答题')
            elif profile_page(records):
                entry_skip=1
                self.send_log('每日答题：复用当前个人页，不重新打开应用')
        if not entry_skip:
            self._reset_home()
        entries = (
            ("本机个人页入口", "我的", [600,35,120,80], 5000),
            ("本机答题中心入口", "我要答题", [20,250,680,450], 8000),
            ("本机每日答题卡片", "每日答题", [20,200,250,500], 30000),
        )
        for name, label, roi, timeout in entries[entry_skip:]:
            self._check_active()
            if self._captcha_present():
                self._wait_for_owner()
            result = self.tasker.post_task(name, {name: {
                "recognition":"OCR", "expected":["^"+label+"$"],
                "roi":roi, "action":"Click", "timeout":timeout,
            }}).wait().get()
            if result.status.failed:
                raise TaskUnavailable("每日答题：未找到"+label+"入口")
            self._sleep(.3)
        for pending in range(12):
            self._sleep(.5)
            if self._captcha_present():
                self._wait_for_owner()
            records = self._screen_records()
            texts = [item["text"].strip() for item in records]
            if any(re.fullmatch(r"单选题|多选题|填空题|点选填空题|第一题|第1题|1[/／]5",text) for text in texts):
                self.send_log("每日答题：已确认题目页面")
                report=getattr(self,'run_report',None)
                if report and '每日答题' in report.snapshot().get('tasks',{}):
                    report.task('每日答题',entry_metrics=dict(reused_entries=entry_skip,elapsed_seconds=round(time.monotonic()-started,2)))
                return
            starts = [item for item in records if item["text"].strip() in ("开始答题", "开始作答", "再来一组", "再答一次")]
            if len(starts) == 1:
                x,y,w,h = starts[0]["box"]
                self.tasker.controller.post_click(round(x+w/2),round(y+h/2)).wait()
        raise TaskUnavailable("每日答题：未确认题目页面")

    def _click_daily_answer_button(self):
        self._open_daily_quiz()
        self.send_log("每日答题题面已就绪，开始识别题型")
        if self.stop_flag:
            return
        return True

    def daily_answer(self):
        """Answer one five-question round, submit it and verify the result outside."""
        if not self._navigate_to_daily_answer() or self.stop_flag:
            return
        first_q = self.tasker.post_task("第一题", {"第一题": {
            "timeout": 15000, "roi": [500, 150, 220, 280]
        }}).wait().get()
        if first_q.status.failed:
            raise TaskIncomplete("未确认每日答题第一题，已保留页面，不重复重开")
        self._daily_completion_expected = False
        incorrect = []
        for i in range(5):
            self._check_active()
            result = self.tasker.post_task("题型识别").wait().get()
            question_type = (result.nodes[0].recognition.best_result.text.strip()
                             if result.status.succeeded else "")
            self.send_log(f"第{i + 1}题 {question_type}")
            match question_type:
                case "单选题" | "多选题":
                    prepared = self._handle_choice(question_type)
                case "填空题":
                    prepared = self._handle_fill_blank()
                case "点选填空题":
                    prepared = self._handle_click_blank()
                case _:
                    raise TaskIncomplete(f"每日答题第{i + 1}题未识别到支持的题型")
            self._check_active()
            if not prepared:
                raise TaskIncomplete("Answer was not prepared or selected")
            self._sleep(1)
            submitted = self.tasker.post_task("确定").wait().get()
            if submitted.status.failed:
                raise TaskIncomplete("Answer submission button was not recognized")
            if i==4:
                report=getattr(self,'run_report',None)
                if report and '每日答题' in report.snapshot()['tasks']:
                    report.task('每日答题',final_answer_submission=final_submission_evidence(submitted))
            if not self._check_quiz_transition(i):
                incorrect.append(i+1)
                self.send_log(f"第{i + 1}题答错，继续本轮剩余题目，不退出重开")
                self.fast_answer = False
                self._guard_verification()
                advance = self.tasker.post_task("下一题检测", {"下一题检测": {
                    "action":"Click", "timeout":1500
                }}).wait().get()
                if advance.status.failed:
                    raise TaskIncomplete("答错后未确认下一题按钮，保留本轮页面")
                self._sleep(.3)
                if not self._check_quiz_transition(i):
                    raise TaskIncomplete("答错后未确认进入下一题或完成页")
            report = getattr(self, "run_report", None)
            if report and "每日答题" in report.snapshot()["tasks"]:
                report.task("每日答题", questions_confirmed=i+1,
                            incorrect_questions=list(incorrect), completion_pending=(i == 4))
            self.send_log(f"[进度核对] 第{i + 1}题已进入下一题" if i < 4
                          else "第5题已作答，正在识别完成按钮和本轮提交结果")
        self._finish_quiz(True)

    def _get_red_texts(self) -> list[str]:
        """调用 RedTextOCR 自定义识别器，从提示区域提取红色文字。
        OCR流程：红度过滤 → 形态学膨胀 → 分块裁剪 → 逐块OCR → 出血线合并。
        返回红字文本列表，每组红字为一个元素。"""
        result: TaskDetail = self.tasker.post_task("红字识别").wait().get()
        texts = []
        if result.status.succeeded:
            raw = result.nodes[0].recognition.raw_detail
            if raw:
                if isinstance(raw, dict):
                    best = raw.get("best", {})
                    if isinstance(best, dict):
                        detail = best.get("detail", {})
                        if isinstance(detail, dict):
                            texts = detail.get("texts", [])
                    if not texts:
                        for key in ["all", "filtered"]:
                            for item in raw.get(key, []):
                                if isinstance(item, dict):
                                    d = item.get("detail", {})
                                    if isinstance(d, dict) and "texts" in d:
                                        texts = d["texts"]
                                        break
                                if texts:
                                    break
        if DEBUG_MODE:
            print(f"[识别] 红色文字: {texts}")
        return texts

    def _scan_click_options(self) -> dict[str, list]:
        """Segment individual button borders, then read each button's own text."""
        self._check_active()
        image = self.tasker.controller.post_screencap().wait().get()
        boxes = detect_click_option_boxes(image)
        if len(boxes) > 12:
            raise TaskIncomplete("点选填空题选项边框不明确")
        options = {}
        for box in boxes:
            self._check_active()
            x,y,w,h = box
            roi = [x+6,y+6,w-12,h-12]
            result = self.tasker.post_task("扫描选项", {"扫描选项": {
                "roi":roi, "action":"DoNothing", "timeout":500,
            }}).wait().get()
            if result.status.failed or not result.nodes or not result.nodes[0].recognition.best_result:
                continue
            text = result.nodes[0].recognition.best_result.text.strip()
            if text:
                if text in options:
                    raise TaskIncomplete("点选填空题存在重复文字选项，无法唯一定位")
                options[text] = box
        self.send_log(f"点选填空：识别到 {len(options)} 个选项")
        return options

    def _fast_try_answer(
        self, question_type: str, options: dict, red_texts: list, blank_num: int = 0
    ):
        """极速答题核心：基于OCR红字和选项文字进行匹配，无需调用AI。
        - 单选题：判断题直接匹配正确/错误；普通题去标点后模糊匹配（精确/子串/长度≥2/3），多红字尝试排列组合
        - 多选题：选项数≤红字组数则全选；否则逐条红字匹配选项，全部匹配才返回
        - 填空题：红字拼接，字数=格子数则直接填入
        - 点选填空题：红字拼接作为答案
        返回选项字母列表/答案字符串，失败返回None交给AI。"""
        if not options and question_type not in ("填空题", "点选填空题"):
            return None
        if not red_texts:
            return None
        red_texts = [t for t in red_texts if t.strip()]
        if not red_texts:
            return None
        match question_type:
            case "多选题":
                option_letters = list(options.keys())
                if len(option_letters) <= len(red_texts):
                    if DEBUG_MODE:
                        print(
                            f"[极速] 多选题: 选项数{len(option_letters)} <= 红字组数{len(red_texts)}, 全选 {option_letters}"
                        )
                    return option_letters

                option_texts = {l: options[l][0] for l in options}
                if DEBUG_MODE:
                    print(
                        f"[极速] 多选题: 选项数{len(option_letters)} > 红字组数{len(red_texts)}, 红字={red_texts}, 选项={option_texts}"
                    )

                def match_red_to_option(red_text):
                    red_clean = strip_punct(red_text)
                    # 优先级：精确 > 子串 > 模糊
                    exact, substring, fuzzy = None, None, None
                    for letter, text in option_texts.items():
                        text_clean = strip_punct(text)
                        if not text_clean:
                            if text in red_text:
                                exact = exact or letter
                        elif text_clean == red_clean:
                            exact = letter
                        elif text_clean in red_clean or red_clean in text_clean:
                            substring = substring or letter
                        elif (
                            red_clean
                            and min(len(text_clean), len(red_clean))
                            / max(len(text_clean), len(red_clean))
                            >= 2 / 3
                        ):
                            fuzzy = fuzzy or letter
                    return exact or substring or fuzzy

                matched = []
                unmatched_reds = []
                for rt in red_texts:
                    result = match_red_to_option(rt)
                    if result is not None:
                        if DEBUG_MODE:
                            print(
                                f'[极速] 多选题红字匹配: "{rt}" -> {result} ({option_texts[result]})'
                            )
                        if result not in matched:
                            matched.append(result)
                    else:
                        unmatched_reds.append(rt)
                if unmatched_reds:
                    if DEBUG_MODE:
                        print(
                            f"[极速] 多选题: 有红字未匹配选项: {unmatched_reds}, 交给AI"
                        )
                    return None
                if matched:
                    if DEBUG_MODE:
                        print(f"[极速] 多选题: 所有红字均匹配选项, 选 {matched}")
                    return matched
                if DEBUG_MODE:
                    print("[极速] 多选题: 文字匹配失败, 交给AI")
                return None
            case "填空题":
                answer = "".join(red_texts)
                if blank_num > 0 and len(answer) == blank_num:
                    if DEBUG_MODE:
                        print(
                            f'[极速] 填空题: 答案="{answer}", 字数={len(answer)}, 格子={blank_num}'
                        )
                    return answer
                if DEBUG_MODE:
                    print(
                        f"[极速] 填空题: 字数{len(answer)} != 格子{blank_num}, 交给AI"
                    )
                return None
            case "点选填空题":
                answer = "".join(red_texts)
                if answer:
                    if DEBUG_MODE:
                        print(f'[极速] 点选填空题: 答案="{answer}"')
                    return answer
                return None
            case "单选题":
                option_texts = {i: options[i][0] for i in options}
                judge_words = {"正确", "错误", "对", "错", "√", "×"}
                is_judge = all(t in judge_words for t in option_texts.values())
                if is_judge:
                    combined = "".join(red_texts)
                    if DEBUG_MODE:
                        print(f'[极速] 判断题, 红字="{combined}"')
                    if combined == "正确":
                        for letter, text in option_texts.items():
                            if text == "正确":
                                if DEBUG_MODE:
                                    print(f"[极速] 判断题直接匹配: {letter} ({text})")
                                return [letter]
                    # 字多即是对.jpg
                    target_words = (
                        {"正确", "对", "√"}
                        if len(combined) > 10
                        else {"错误", "错", "×"}
                    )
                    for letter, text in option_texts.items():
                        if text in target_words:
                            if DEBUG_MODE:
                                print(f"[极速] 判断题推测: {letter} ({text})")
                            return [letter]
                    return None

                combined = "".join(red_texts)
                if DEBUG_MODE:
                    print(f'[极速] 选择题, 红字="{combined}", 选项={option_texts}')

                combined_clean = strip_punct(combined)
                # 优先级匹配：精确 > 子串 > 模糊长度比(≥2/3)
                exact, substring, fuzzy = [], [], []
                for letter, text in option_texts.items():
                    text_clean = strip_punct(text)
                    if not text_clean:
                        if text in combined:
                            exact.append(letter)
                    elif text_clean == combined_clean:
                        exact.append(letter)
                    elif text_clean in combined_clean or combined_clean in text_clean:
                        substring.append(letter)
                    elif (
                        min(len(text_clean), len(combined_clean))
                        / max(len(text_clean), len(combined_clean))
                        >= 2 / 3
                    ):
                        fuzzy.append(letter)
                for label, candidates, log_fn in [
                    (
                        "精确",
                        exact,
                        lambda x: f"[极速] 精确匹配: {x} ({option_texts[x]})",
                    ),
                    (
                        "子串",
                        substring,
                        lambda x: f"[极速] 子串匹配: {x} ({option_texts[x]})",
                    ),
                    (
                        "模糊",
                        fuzzy,
                        lambda x: f"[极速] 模糊匹配: {x} ({option_texts[x]})",
                    ),
                ]:
                    if candidates:
                        log_fn(candidates[0])
                        return [candidates[0]]
                if len(red_texts) > 1:
                    from itertools import permutations

                    for perm in permutations(red_texts):
                        perm_text = "".join(perm)
                        perm_clean = strip_punct(perm_text)
                        # 排列匹配也按优先级：精确 > 子串 > 模糊
                        exact_p, substring_p, fuzzy_p = [], [], []
                        for letter, text in option_texts.items():
                            text_clean = strip_punct(text)
                            if not text_clean:
                                if text in perm_text:
                                    exact_p.append(letter)
                            elif text_clean == perm_clean:
                                exact_p.append(letter)
                            elif text_clean in perm_clean or perm_clean in text_clean:
                                substring_p.append(letter)
                            elif (
                                min(len(text_clean), len(perm_clean))
                                / max(len(text_clean), len(perm_clean))
                                >= 2 / 3
                            ):
                                fuzzy_p.append(letter)
                        for label, candidates in [
                            ("排列精确", exact_p),
                            ("排列子串", substring_p),
                            ("排列模糊", fuzzy_p),
                        ]:
                            if candidates:
                                if DEBUG_MODE:
                                    print(
                                        f'[极速] {label}匹配: {candidates[0]} ({option_texts[candidates[0]]}), 排列="{perm_text}"'
                                    )
                                return [candidates[0]]
                self.send_log("[极速答题] 极速答题失败，原因：无法匹配，请求AI解答")
                return None
            case _:
                return None

    def _get_options(self) -> dict[str, tuple[str, list]]:
        """OCR扫描选择题选项文字。
        流程：模板匹配A-F字母位置 → 对每个字母右侧区域(到x=635)执行OCR → 去噪(銀園等)。
        返回 {字母: (文字, [x, y, w, h])} 映射。"""
        options = {}
        found = {}
        for letter in ["A", "B", "C", "D", "E", "F"]:
            find_result: TaskDetail = (
                self.tasker.post_task(f"选项{letter}").wait().get()
            )
            if find_result.status.succeeded:
                box = list(find_result.nodes[0].recognition.best_result.box)
                found[letter] = box
        if not found:
            if DEBUG_MODE:
                print("[识别] 选项: 未找到任何选项")
            return options
        if DEBUG_MODE:
            print(f"[识别] 找到选项: {list(found.keys())}")
        for letter, box in found.items():
            roi = [box[0] + box[2], box[1], 635 - box[0] - box[2], box[3]]
            # OCR区域：从字母右侧到x=635，避开字母图标本身
            ocr_result: TaskDetail = (
                self.tasker.post_task("扫描选项", {"扫描选项": {"roi": roi}})
                .wait()
                .get()
            )
            text = ""
            if ocr_result.status.succeeded:
                text = ocr_result.nodes[0].recognition.best_result.text.strip()
                for noise in ["銀園", "銀", "電", "機"]:
                    if text.endswith(noise):
                        text = text[: -len(noise)].strip()
            if DEBUG_MODE:
                print(f'[识别] {letter} => "{text}"')
            if text:
                options[letter] = (text, box)
        if DEBUG_MODE:
            print(f"[识别] 选项: {options}")
        return options

    @observed('每日答题：提取题目与提示')
    def _prepare(self, question_type: str) -> tuple:
        """准备答题数据，返回 (截图列表, 选项OCR结果, 红字列表, 填空格子数)。
        流程：
        1. [极速+选择题] OCR扫描选项文字 (_get_options)
        2. [填空题] 统计文本框格子数 (_count_blanks)
        3. 查找"提示"按钮，未找到则下滑重试（下滑后重新扫描选项）
        4. 滚动截图找到提示区域
        5. [极速] RedTextOCR提取红字 (_get_red_texts)
        """
        options = {}
        blank_num = 0
        self._choice_question_text = ''
        self._daily_choice_page = None
        self._daily_choice_text = None
        self._blank_context=None
        self._blank_hint_text=''
        if question_type in ("单选题", "多选题"):
            options = self._get_options()
            records = self._screen_records()
            self._choice_question_text = question_from_records(records,options)
            frame = getattr(self,'_screen_ocr_cache',None)
            if isinstance(frame,tuple) and len(frame)==2:
                self._daily_choice_page = choice_page(records,frame[0],options,question_type)
                if self._daily_choice_page:
                    options={letter:(text,options[letter][1])
                             for letter,text in self._daily_choice_page['options'].items()}
        if question_type == "填空题":
            result: TaskDetail = self.tasker.post_task("文本框").wait().get()
            blank_num = (
                len(result.nodes[0].recognition.filtered_results)
                if result.status.succeeded
                else 0
            )
            if blank_num:
                matches=result.nodes[0].recognition.filtered_results
                boxes=[list(row.box) for row in matches if hasattr(row,'box')]
                self._blank_context=blank_context(self._screen_records(),boxes) if len(boxes)==blank_num else None
        img_list = [self.tasker.controller.post_screencap().wait().get()]
        hint_found: TaskDetail = self.tasker.post_task("查找提示").wait().get()
        if hint_found.status.failed:
            self._daily_choice_page = None
            self.tasker.controller.post_swipe(
                randint(350, 360),
                randint(990, 1000),
                randint(540, 550),
                randint(580, 590),
                randint(1500, 2000),
            ).wait()
            self._sleep(0.5)
            img_list.append(self.tasker.controller.post_screencap().wait().get())
            if question_type in ("单选题", "多选题"):
                new_options = self._get_options()
                for k, v in new_options.items():
                    options[k] = v
        hint_open = self.tasker.post_task("查看提示").wait().get()
        if hint_open.status.failed:
            raise TaskIncomplete("Quiz hint was not opened")
        self._sleep(0.5)
        img_list.append(self.tasker.controller.post_screencap().wait().get())
        red_texts = []
        if self.fast_answer or question_type in ('填空题','点选填空题','单选题','多选题'):
            red_texts = self._get_red_texts()
        if (question_type=='填空题' and self._blank_context
                and len(''.join(red_texts))!=blank_num):
            self._blank_hint_text=hint_paragraph(self._screen_records())
        if (self._daily_choice_page and
                exact_hint_choice(question_type,options,red_texts,self._choice_question_text) is None):
            hint_records=self._screen_records()
            frame=getattr(self,'_screen_ocr_cache',None)
            if isinstance(frame,tuple) and len(frame)==2:
                hint=complete_hint(hint_records,frame[0])
                self._daily_choice_text=request_text(self._daily_choice_page,hint)
                # Preserve the same full hint frame if text completeness fails.
                img_list[-1]=frame[0].copy()
        hint_close = self.tasker.post_task("关闭提示").wait().get()
        if hint_close.status.failed:
            raise TaskIncomplete("Quiz hint was not closed")
        return img_list, options, red_texts, blank_num

    @observed('每日答题：确定答案')
    def _determine_answer(
        self,
        question_type: str,
        options: dict,
        red_texts: list,
        img_list: list,
        blank_num: int = 0,
    ):
        """确定答案：优先极速匹配，失败则调用AI。
        返回 (答案, 是否极速) 元组。极速成功直接返回；否则发截图给AI大模型。
        AI失败则通知用户接管。"""
        hinted = exact_hint_choice(question_type,options,red_texts,getattr(self,'_choice_question_text',''))
        if hinted is not None:
            self.send_log('每日答题：提示原文已严格匹配选项，无需请求DeepSeek')
            return hinted, True
        if question_type == '点选填空题' and red_texts:
            parts=exact_hint_parts(red_texts,self._scan_click_options())
            if parts is not None:
                self.send_log('点选填空：提示原文已完整匹配唯一顺序，无需请求DeepSeek')
                return parts,True
        if question_type=='填空题':
            answer=exact_blank_hint(getattr(self,'_blank_context',None),getattr(self,'_blank_hint_text',''))
            if answer is not None:
                self.send_log('填空题：题干前后文与提示唯一对齐，直接填写原文')
                return answer,True
        fast_answer = self._fast_try_answer(
            question_type, options, red_texts, blank_num
        ) if self.fast_answer and question_type == '填空题' else None
        if fast_answer is not None:
            return fast_answer, True
        if question_type == "填空题" and red_texts:
            answer = "".join(red_texts)
            if len(answer) == blank_num:
                return answer, True

        # 极速失败，发截图给AI大模型解答
        if question_type in ("单选题", "多选题"):
            self.send_log('提示未能直接确定答案，正在请求DeepSeek（有超时限制）')
            report = getattr(self,'run_report',None)
            if report:
                report.update(phase='每日答题：正在请求DeepSeek答案')
            if DEBUG_MODE:
                print("[AI] 请求选择题解答")
            text_input=getattr(self,'_daily_choice_text',None)
            if text_input:
                self.send_log('每日答题：题干、全部选项和提示已完整读取，只传文字给模型')
                answer = self.ai_resolver.resolve_choice(img_list,question_type,text_input=text_input)
            else:
                self.send_log('每日答题：保留完整题面与提示图片')
                answer = self.ai_resolver.resolve_choice(img_list, question_type)
        elif question_type == "填空题":
            if DEBUG_MODE:
                print("[AI] 请求填空题解答")
            answer = self.ai_resolver.resolve_blank(img_list, True, blank_num)
        elif question_type == "点选填空题":
            if DEBUG_MODE:
                print("[AI] 请求点选填空题解答")
            answer = self.ai_resolver.resolve_click_blank(img_list)
        else:
            answer = None
        if answer is None:
            reason = getattr(self.ai_resolver, 'last_error', None) or 'no_usable_answer'
            raise TaskIncomplete(f'{question_type}未获得可靠答案（{reason}），未提交；已停止此项，不无限等待人工操作')
        return answer, False

    @observed('每日答题：选择或填写答案')
    def _submit_answer(self, question_type: str, answer, from_fast: bool) -> bool:
        """提交答案到界面。
        - 选择题：逐个点击选项字母(选A/选B...)，被遮挡则下滑重试
        - 填空题：点击文本框 → 输入文字
        - 点选填空题：极速模式用颜色验证点选，AI模式用文字匹配点选
        """
        self._check_active()
        if question_type in ("单选题", "多选题"):
            if not isinstance(answer, list) or not answer or any(x not in list("ABCDEF") for x in answer) or len(set(answer)) != len(answer) or (question_type == "单选题" and len(answer) != 1):
                return False
            if from_fast:
                self.send_log(f"提示匹配解答成功，答案为{''.join(answer)}")
            else:
                self.send_log(f"AI解答成功，答案为{''.join(answer)}")
            failed = []
            for choice in answer:
                result: TaskDetail = (
                    self.tasker.post_task(
                        f"选项{choice}",
                        pipeline_override={f"选项{choice}": {"action": "Click"}},
                    )
                    .wait()
                    .get()
                )
                if result.status.succeeded:
                    self._sleep(0.2)
                else:
                    failed.append(choice)
            if failed:
                if DEBUG_MODE:
                    print(f"[选择题] 选项 {failed} 可能被遮挡，下滑重试")
                self.tasker.controller.post_swipe(
                    randint(300, 400),
                    randint(600, 700),
                    randint(300, 400),
                    randint(300, 400),
                    randint(200, 300),
                ).wait()
                self._sleep(0.3)
                for choice in failed:
                    retry = self.tasker.post_task(f"选项{choice}", {f"选项{choice}": {"action": "Click"}}).wait().get()
                    if retry.status.failed:
                        raise TaskIncomplete("An answer option was not selected")
                    self._sleep(0.2)
            return True
        if question_type == "填空题":
            if from_fast:
                self.send_log(f"极速模式解答成功: {answer}")
            if not isinstance(answer, str) or not answer.strip():
                return False
            field = self.tasker.post_task(
                "文本框", pipeline_override={"文本框": {"action": "Click"}}
            ).wait().get()
            if field.status.failed:
                return False
            self._sleep(0.5)
            if not self.tasker.controller.post_input_text(answer).wait().succeeded:
                return False
            return True
        if question_type == "点选填空题":
            if from_fast:
                if isinstance(answer,list):
                    self.send_log('点选填空：按提示匹配顺序选择')
                    return self._click_text_answers(answer)
                return self._fast_click_blanks(answer)
            answers = (
                answer
                if isinstance(answer, list)
                else [
                    a.strip() for a in answer.replace("，", ",").split(",") if a.strip()
                ]
            )
            self.send_log(f"AI识别到 {len(answers)} 个答案: {answers}")
            return self._click_text_answers(answers)
        return False

    def _fast_click_blanks(self, answer: str) -> bool:
        """极速点选填空题：扫描选项文字位置 → 按答案顺序逐个点击 → 颜色差值验证选中状态。
        颜色差值>50判定为选中成功，否则请求接管。"""
        text_positions = self._scan_click_options()
        if not text_positions:
            self.send_log("[点选填空题] 未识别到选项, 请求接管")
            plyer.notification.notify(
                title="MaaXuexi",
                message="未识别到选项文本",
                app_name="MaaXuexi",
                timeout=60,
            )
            self.pause()
            return True
        self.send_log(
            f'[点选填空题] 答案="{answer}", 选项={list(text_positions.keys())}'
        )
        clicked_any = False
        remaining = answer
        for text, box in text_positions.items():
            if text not in remaining:
                continue
            cx = box[0] + box[2] // 2
            cy = box[1] + box[3] // 2
            img_before = self.tasker.controller.post_screencap().wait().get()
            color_before = img_before[cy, cx].tolist()
            if DEBUG_MODE:
                print(f'[点选填空题] 点击 "{text}" 位置({cx},{cy})')
            self.tasker.controller.post_click(cx, cy).wait()
            self._sleep(0.5)
            img_after = self.tasker.controller.post_screencap().wait().get()
            color_after = img_after[cy, cx].tolist()
            diff = sum(abs(int(a) - int(b)) for a, b in zip(color_before, color_after))
            if diff > 50:
                clicked_any = True
                remaining = remaining.replace(text, "", 1)
                if DEBUG_MODE:
                    print(f'[点选填空题] "{text}" 选中成功')
            else:
                self.send_log(f'[点选填空题] "{text}" 选中失败, 请求接管')
                plyer.notification.notify(
                    title="MaaXuexi",
                    message=f'选项 "{text}" 选中失败',
                    app_name="MaaXuexi",
                    timeout=60,
                )
                self.pause()
                return True
        if not clicked_any:
            self.send_log("极速点选: 未匹配任何选项, 交给AI")
            return False
        self.send_log("极速点选完成")
        return True

    def _click_text_answers(self, answers: list[str]) -> bool:
        """Select exact option text; re-read positions after each selection."""
        if not answers or any(not isinstance(ans,str) or not ans.strip() for ans in answers):
            raise TaskIncomplete("点选填空题答案无效")
        for ans in answers:
            self._check_active()
            positions = self._scan_click_options()
            candidates = [box for text,box in positions.items() if strip_punct(text)==strip_punct(ans)]
            if len(candidates) != 1:
                raise TaskIncomplete("点选填空题未找到唯一的答案选项")
            x,y,w,h = candidates[0]
            img_before = self.tasker.controller.post_screencap().wait().get()
            patch_before = img_before[y:y+h,x:x+w].copy()
            if not self.tasker.controller.post_click(round(x+w/2),round(y+h/2)).wait().succeeded:
                raise TaskIncomplete("点选填空题点击失败")
            self._sleep(0.5)
            img_after = self.tasker.controller.post_screencap().wait().get()
            patch_after = img_after[y:y+h,x:x+w]
            if patch_before.shape != patch_after.shape or not patch_before.size:
                raise TaskIncomplete("点选填空题无法核对选中状态")
            diff = np.abs(patch_before.astype(np.int16)-patch_after.astype(np.int16)).mean(axis=2)
            if np.count_nonzero(diff > 20) < max(20,diff.size*0.01):
                raise TaskIncomplete("点选填空题点击后页面没有变化")
        self.send_log("点选完成")
        return True

    def _handle_fill_blank(self) -> bool:
        """处理填空题：先检测是否为视频题（视频题直接截图发AI），否则走标准流程 prepare → determine → submit。"""
        if self.stop_flag:
            return False
        video_result: TaskDetail = self.tasker.post_task("填空题视频").wait().get()
        if not video_result.status.succeeded:
            self.send_log("发现视频，正在请求AI解答")
            image = self.tasker.controller.post_screencap().wait().get()
            if DEBUG_MODE:
                print("[AI] 请求填空题解答(视频)")
            answer = self.ai_resolver.resolve_blank([image], False, None)
            if answer is None:
                reason = getattr(self.ai_resolver, 'last_error', None) or 'no_usable_answer'
                raise TaskIncomplete(f'视频填空题未获得可靠答案（{reason}），未提交；已停止此项，不无限等待人工操作')
            return self._submit_answer("填空题", answer, False)
        img_list, options, red_texts, blank_num = self._prepare("填空题")
        answer, from_fast = self._determine_answer(
            "填空题", options, red_texts, img_list, blank_num
        )
        if answer is None or answer == "":
            return False
        return self._submit_answer("填空题", answer, from_fast)

    def _handle_choice(self, question_type) -> bool:
        """处理单选题/多选题：prepare(OCR选项+红字) → determine(极速匹配/AI) → submit(点击选项)。
        多选题通过 question_type="多选题" 复用此方法。"""
        if self.stop_flag:
            return False
        img_list, options, red_texts, blank_num = self._prepare(question_type)
        answer, from_fast = self._determine_answer(
            question_type, options, red_texts, img_list, blank_num
        )
        if answer is None:
            return False
        return self._submit_answer(question_type, answer, from_fast)

    def _handle_click_blank(self) -> bool:
        """处理点选填空题：prepare → determine(红字拼接/AI) → submit(极速点选/AI点选)。"""
        if self.stop_flag:
            return False
        img_list, options, red_texts, blank_num = self._prepare("点选填空题")
        answer, from_fast = self._determine_answer(
            "点选填空题", options, red_texts, img_list, blank_num
        )
        if answer is None:
            return False
        return self._submit_answer("点选填空题", answer, from_fast)


