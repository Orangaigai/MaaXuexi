# 模块说明

| 模块 | 负责什么 |
| --- | --- |
| `webui.py` | 本机HTTP/WebSocket接口，任务选择、控制和状态 |
| `utils.py` | 工作线程、任务执行、连接和每日题准备 |
| `home_navigation.py` / `score_navigation.py` | 确认主页、首次新鲜核分、积分页导航 |
| `content_tasks.py` / `local_channel.py` | 文章、音视频和本地频道，实际进度与积分核对 |
| `daily_flow.py` | 每日提交、本人验证等待、结果页与自动返回 |
| `daily_hints.py` / `blank_hints.py` / `daily_text.py` / `daily_gaps.py` / `daily_choices.py` | 提示匹配、填空上下文、完整文本分流、多选空格计数和长图片选项的收集与点击 |
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
| `history_reader.py` / `history_import.py` | 独立历史读取、双向 OCR 核对、导入状态及首次选择；不创建每日任务报告或调用模型 |
| `ledger_reading.py` / `history_ledger.py` | 手机端逐日流水读取、重叠拼接、正反两遍及总分对账、SQLite 日快照 |
| `completion_actions.py` | 完成后倒计时、取消及后台退出协调；未完成或保存失败时保留现场 |
| `Control-Browser.ps1` / `Finish-Session.ps1` | 独立控制台窗口归属核对、指定模拟器实例关闭与资源收尾 |

正常流程：启动入口准备设备 → 用户或Windows计划发起任务 → 确认应用页面 → 新鲜核分 → 依次执行未满分项目 → 再核分并保存积分与任务结果 → 全部完成后按选项倒计时关闭。只查看当天已完成记录时不启动模拟器，执行新任务或次日定时启动仍会准备设备。

本人验证不是模型或自动化模块的一部分。程序等待正常页面恢复；超时或不能确认时保留已确认结果和待处理项目，不把动作成功当成加分成功。

## 历史积分导入接口

以下仅限本机控制台调用，供后续设置页复用：

| 请求 | 用途 |
| --- | --- |
| `GET /api/local/history-import` | 首次选择、当前作业、阶段、已读取天数及结果 |
| `POST /api/local/history-import` | 启动导入；已有导入时返回同一作业，学习任务运行时拒绝 |
| `POST /api/local/history-import/cancel` | 请求取消，写库阶段不可中断 |
| `POST /api/local/history-import/skip` | 记住“从今天开始记录”，不删历史、不运行任务 |
| `GET /api/local/points-ledger?day=YYYY-MM-DD` | 按日期延迟读取已导入的原始流水；未导入为 `available: false` |

启动 POST 使用 `{"include_details": false}`（默认，也兼容空 JSON）或 `{"include_details": true}`。只接受布尔值，未知字段拒绝。页面完整导入必须先经过确认弹窗；快速导入不调用逐日流水读取器。其它 POST 使用空 JSON。`/api/status` 同时携带 `history_import`，页面刷新后可恢复进度。首次选择与最近作业保存在私有 `config/history-import.json`；重启时把未结束作业标为中断，不自动制造成功结果。

积分仍保存在原 SQLite 库中：`daily_totals` 保存日总分，`ledger_days` 保存已对账日元数据，`score_transactions` 按 `(day, position)` 保存有序明细。不按文本去重，保留同一分钟的多笔相同积分。重导以完整日快照替换，不累加；旧观察不覆盖新观察。未取得的流水不从任务上限推算，不伪造 `runs` 或 `task_results`。月接口仅附带流水摘要，用户选择日期后再读取明细。

图表总分正反核对，逐日明细的相邻画面必须重叠，正向和反向的完整有序记录必须一致，且明细分数合计必须与该日总分相符，才保存该日。单日未核实会报告部分导入，不将空白当作零分。采集器使用独立控制器、空模型密钥和取消事件；快速读取上限 4 分钟，含明细上限 20 分钟，平台验证最多等待 2 分钟。定时执行可等待导入结束，避免两个流程同时操控设备。

OCR 行解析保留时间、项目与实际分值：装饰性引号和空白归一化，名称与末尾分值被合并识别时按文本及位置拆分；临近底边的文字延后到下一幅完整画面读取。不用模糊名称匹配或凭总分补造缺失流水。单日识别未一致时完整重读一次，第二次仍失败则报告未核实；取消、登录及平台验证不进入这种重读。

当前阅读通道是现有 MaaFramework/MuMu 的截图与本机 OCR。官方公开 H5 定义了 `getDaysScore` 和 `queryScoreDetailByPage`，但调用依赖 App 内的已登录 LWP 桥；尚无已接通并验收的手机端结构化通道。没有把接口名称或静态文件当作已经取得个人记录，也不要求额外官网登录。实现没有提取登录凭证、启用 App 调试或增加模型调用。后续优先项见[读取限制与改进计划](roadmap.md)。

此功能沿用单个本机账号配置。切换学习强国账号前应使用独立安装目录与积分库，当前不能自动隔离多个账号的历史。
