# 开发与维护

## 本地开发

使用 Windows x64 和 Python 3.12。通过安装脚本准备环境，或手动创建虚拟环境并安装 `backend/requirements-runtime.lock.txt`。前端是本地 HTML/CSS/JavaScript，无 npm 构建步骤。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-runtime.lock.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -q
```

不要同时把两个开发副本接到同一模拟器或争用端口8000。离线测试不需要注册真实计划或填写密钥。不要把别人的实际配置复制到测试目录。

## 文件职责

- `page/assets/ui-base.css` 维护共享的颜色、字体、按钮和控件；`console.css` 维护控制台布局，`key-setup.css` 维护密钥页和指引。样式随源码本地加载，不依赖在线字体或界面框架。
- `local-settings.js` 负责窗口、通知和设置页状态，`settings.css` 维护设置页；显示/通知偏好与任务、计划和密钥配置分开保存。
- `local-provider.js` 负责状态和任务操作，`local-schedule.js` 负责设置页执行方式和时间编辑，`schedule-summary.js` 只读展示首页计划，两者复用 `schedule-view.js`；`points-history.js` 负责日历与本次收尾倒计时。更改布局应保留控件ID、语义和键盘操作。
- `backend` 模块见[模块说明](architecture.md)。不要把已经分开的导航、识别、模型和核分重新塞进一个页面脚本。
- 专用浏览器profile位于`runtime/control-browser`，升级时保留旧`config/control-browser`的兼容与归属识别；只在关闭后原子迁移，不合并或清空目录。迁移需覆盖重定向、占用、未知进程、目标并发出现、新旧路径归属等检查。
- `config/*.example.json` 是可发布示例；真实JSON、DPAPI、数据库和运行记录一律保留在本机。
- `tests/fixtures` 只使用固定、最小化、可公开的测试样本。新截图需检查账号、头像、手机号、评论人信息和密钥是否需要去除。

旧的本机逐轮验收笔记不作为当前用户文档。历史修复提炼到 `CHANGELOG.md`，具体版本能证明什么、不能证明什么写清楚，不把“满分跳过”记成新题或新发布的通过。

## 检查命令

```powershell
.\.venv\Scripts\python.exe scripts\Check-Repository.py
node --check page\assets\local-provider.js
node --check page\assets\local-schedule.js
node --check page\assets\key-setup.js
node --check page\assets\points-history.js
```

仓库检查核对发布文件、源文件清单、文档本地链接和配置边界。GitHub Actions在Windows下安装锁定依赖并运行离线测试，不接模拟器、不配置API密钥、不操作真实账号。

浏览器验收应覆盖：任务选择与保存、配置失败提示、手动/自动切换、未保存时间编辑、运行/暂停/待验证状态、长错误、键盘操作和手机窄屏。纯外观改动不要用重复公开发言来验收。

设置导航需另外检查：720/1100 像素高度下，六个分区逐项点击与普通滚动都能高亮；页底三个链接可在同一滚动位置分别选中，重复点击、浏览器前后退、直接带锚点访问、无动画和窄屏均正常。参考 [Tabler 的页底导航讨论](https://github.com/tabler/tabler/issues/2973) 与 [scrollend 事件说明](https://developer.mozilla.org/en-US/docs/Web/API/Document/scrollend_event)，本项目按现有六个分区补齐尾部滚动区间和点击状态，没有引入导航框架。

## 界面维护

- 大屏按“状态概览 → 任务与执行设置 → 积分 → 可展开工具”布局，窄屏保持相同顺序；避免固定侧边导航挤占操作区域。
- 主要按钮用于执行或继续；需要先配置密钥时突出配置入口，保存与自检使用辅助样式。
- 优先复用共享样式，再按页面补充布局；外层留白、卡片间距与控件尺寸统一调整，避免累加临时覆盖规则。
- 验证提示同时提供文字和颜色；隐藏控件仍保留原生标签，选项必须有可见键盘焦点。未核实积分与实际零分要区别呈现。
- 在320、375、760、960、1180和1440像素等宽度检查溢出、表单、长提示与弹窗；截图使用演示数据，并明确区别界面交互测试和真实任务验收。

## 识别与模型改动

先回放固定样本，再在本人在场时做需要的最小实机验证。不要为减少等待裁掉题目选项、跳过结果页或伪造已完成状态；不确定就记录实际结果。

`scripts/Compare-QuizModels.py` 默认只检查带证据的历史样本。只有显式加 `--run` 才请求付费服务，不参加每日任务，也不会替换运行模型。选项换序只适用于语义独立的文字选项，不能从几次通过推断模型整体正确率。

## 源文件清单与发布

修改完成后，从Git已跟踪且未忽略的文件重新生成清单：

```powershell
.\.venv\Scripts\python.exe scripts\Update-SourceManifest.py
.\.venv\Scripts\python.exe scripts\Check-Repository.py
```

生成器不把 `config/source-manifest.json` 自身纳入哈希；新文件需先 `git add`，同时检查暂存文件，避免把本机配置放进索引。不要对整个目录不加筛选地打包 `.venv`、日志和数据库。

公开发布前确认文档链接、安装步骤、默认配置、许可证和截图；提交源码后等待CI结果。GitHub源码包不包含运行环境，不能称为免安装成品。需要更新GitHub时使用正常提交和推送，不为整理文件强制改写已经公开的历史。
