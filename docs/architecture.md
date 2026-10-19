# 模块说明

| 模块 | 负责什么 |
| --- | --- |
| `webui.py` | 本机HTTP/WebSocket接口，任务选择、控制和状态 |
| `utils.py` | 工作线程、任务执行、连接和每日题准备 |
| `home_navigation.py` / `score_navigation.py` | 确认主页、首次新鲜核分、积分页导航 |
| `content_tasks.py` / `local_channel.py` | 文章、音视频和本地频道，实际进度与积分核对 |
| `daily_flow.py` | 每日提交、本人验证等待、结果页与自动返回 |
| `daily_hints.py` / `blank_hints.py` / `daily_text.py` | 提示严格匹配、填空上下文、完整文本分流 |
| `fun_challenge.py` / `four_player.py` | 当日趣味模式、限时答案、结算与退出 |
| `screen_reading.py` / `question_visual.py` | 完整画面读取、相同画面OCR复用、图片证据分流 |
| `answer_client.py` / `timed_http.py` | 官方DeepSeek请求、结构化答案、限时取消和连接复用 |
| `subscription_tasks.py` / `subscription_store.py` | 订阅核分、程序新增关注清理、轮次计数 |
| `comment_reading.py` / `comment_drafts.py` / `comment_tasks.py` | 同篇全文、短评候选与复核、一次发布和持久记录 |
| `task_support.py` | 任务顺序、分数解析、持久报告与版本 |
| `key_store.py` / `provider_support.py` | DPAPI密钥与官方接口验证 |
| `schedule_support.py` | 本地时间设置、Windows计划读写和每日重排 |
| `diagnostics.py` / `runtime_logs.py` / `log_retention.py` | 有限诊断现场、日志写队列、已知日志保守保留 |
| `runtime_doctor.py` | 按需只读汇总配置检查，不发起付费模型测试 |
| `points_history.py` | 按北京时间保存实际积分与任务结果，同日更新快照；提供月历和日期详情 |
| `completion_actions.py` | 完成后倒计时、取消及后台退出协调；未完成或保存失败时保留现场 |
| `Control-Browser.ps1` / `Finish-Session.ps1` | 独立控制台窗口归属核对、指定模拟器实例关闭与资源收尾 |

正常流程：启动入口准备设备 → 用户或Windows计划发起任务 → 确认应用页面 → 新鲜核分 → 依次执行未满分项目 → 再核分并保存积分与任务结果 → 全部完成后按选项倒计时关闭。只查看当天已完成记录时不启动模拟器，执行新任务或次日定时启动仍会准备设备。

本人验证不是模型或自动化模块的一部分。程序等待正常页面恢复；超时或不能确认时保留已确认结果和待处理项目，不把动作成功当成加分成功。
