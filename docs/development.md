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

- `page/assets/console.css` 集中维护控制台外观；`local-provider.js` 负责控制台状态和任务操作，`local-schedule.js` 负责时间与执行方式。更改布局应保留控件ID、语义和键盘操作。
- `backend` 模块见[模块说明](architecture.md)。不要把已经分开的导航、识别、模型和核分重新塞进一个页面脚本。
- `config/*.example.json` 是可发布示例；真实JSON、DPAPI、数据库和运行记录一律保留在本机。
- `tests/fixtures` 只使用固定、最小化、可公开的测试样本。新截图需检查账号、头像、手机号、评论人信息和密钥是否需要去除。

旧的本机逐轮验收笔记不作为当前用户文档。历史修复提炼到 `CHANGELOG.md`，具体版本能证明什么、不能证明什么写清楚，不把“满分跳过”记成新题或新发布的通过。

## 检查命令

```powershell
.\.venv\Scripts\python.exe scripts\Check-Repository.py
node --check page\assets\local-provider.js
node --check page\assets\local-schedule.js
node --check page\assets\key-setup.js
```

仓库检查核对发布文件、源文件清单、文档本地链接和配置边界。GitHub Actions在Windows下安装锁定依赖并运行离线测试，不接模拟器、不配置API密钥、不操作真实账号。

浏览器验收应覆盖：任务选择与保存、配置失败提示、手动/自动切换、未保存时间编辑、运行/暂停/待验证状态、长错误、键盘操作和手机窄屏。纯外观改动不要用重复公开发言来验收。

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
