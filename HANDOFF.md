# HANDOFF

> **第一次接手这个项目？先读根目录的 `AGENTS.md`**（项目怎么运作、用户是谁、十条硬规矩、
> 排查「它没提醒我」的固定顺序），再读这份看进度，`CLAUDE.md` 是工作协议。
> 回归套件在 `tests/`，跑法 `cd tests && ./runall.sh e2e.py e2e_ai.py`，细节见 `tests/RUN.md`。
> 这三份文件 + tests/ 就是全部交接内容，**不需要上一轮的聊天记录**。

## 最后更新时间
2026-07-31 14:05（北京时间）

## 当前状态
dcwatch **v1.8.0**（扩展也是 v1.8.0，**本版改了扩展，用户必须重装/刷新扩展**）。
本轮做的是 v1.8.0 的收尾与验证：代码是上一轮写完的，但**没跑回归、没打包、没推**。
现在全部补齐 —— 回归**服务端 342 条**（e2e 46 + ai 27 + multi 26 + wiz 83 + v17 46 + diag 47 + **wb 67**）
+ 浏览器里的 content.js 回归 **43** 条 + 新旧判定 16 条，**全绿**。
交付 `outputs/dcwatch-v1.8.0.zip`（26 文件/345KB）+ `outputs/dcwatch-extension-v1.8.0.zip`（10 文件/40KB），
1.7.4 的两个 zip 已删。GitHub 远端已跟上 v1.8.0。
**仍在等用户的回执**：v1.7.3 起加的诊断包 [4.5] 段能直接指出「规则填了但不会命中」，
但不知道他改没改对，也不知道他现在跑的是哪一版。

## 已完成
- 单进程 aiohttp 服务端（server.py）：Discord 监听 / 规则引擎 / OpenAI 兼容模型调用 / SQLite 存储
- 单文件 GUI（ui.html，Claude 风格）：收信箱、AI 工作台、批量提取、监听规则、模型接入、通知与转发、设置、运行日志
- MV3 浏览器扩展（extension/）：读 discord.com DOM 上报消息，页面内状态药丸 + popup 自检 + 抓历史
- 三条接入方式：浏览器旁听（默认，不需要 token）/ Bot Token / User Token
- 引导式建规则（/api/rules/wizard）与一句话生成规则（/api/rules/compose）
- 通用出口 hooks：Windows toast、提示音、Discord Webhook、Telegram、Server酱、企业微信、自定义 webhook
- 新帖检测（论坛开帖）、抓历史、批量提取 + CSV 导出
- 自证渠道：/version 独立页面、诊断包 /diagnose.txt（[0] 一眼结论、[4.5] 拿真消息逐条试算）、启动时打印版本与目录
- Windows 双击即用：启动.bat / 停止.bat / build.bat + freeport.py 自动清理占用端口
- v1.7.4：后台标签页不再迟到（第一条消息立刻发 + background 每 30s 主动拉心跳，不需要新权限）
- **v1.8.0（上一轮写、本轮验证并交付）**
  · 工作台的模型**能自己动手改规则**：`WB_TOOLS`（列出/新建/改/启停/删/试算/查频道/搜消息/看状态）+
    函数调用，接口不支持 tools 时自动退回文本指令协议（`WB_TEXT_PROTO`），界面写一行「改用文本指令模式」
  · 纪律写死在 `WB_TOOLS_HOWTO`：改之前先列规则不许凭名字猜 id、编的频道 ID 会被 `sanitize_draft` 洗掉、
    没明说「删」只停用、每步动作显示在回答上方（`✔` 改了 / `·` 只读）
  · 「模型接入」页两个开关：**允许模型直接改规则**（关掉后两条动手的路都封死）、**边想边显示（流式）**
  · 表情包 / 图片 / 贴纸不再被当「没有文字」丢掉，翻译成 `[图片]`、`[贴纸 xx]`、`:emoji:` 上报（扩展侧）
  · 浏览器旁听模式下**不再画回复输入框**（以前填完一段话才吃报错），底部写清原因和换 Bot Token 的入口
  · 新回归套件 `e2e_wb.py`（67 条，13 节），`/api/prompts` 多一条 `workbench_tools`
- **跨会话/跨账号交接（本轮加的）**：`tests/` 整套回归**进了仓库**（以前只在某个 workspace 的
  私有目录里，换个账号接手就等于没有安全网）；根目录新增 `AGENTS.md` 作为项目记忆，
  这个文件名会被 Claude Code / BrowserCode / Cursor 自动读取。
  `tests/runall.sh` 自动定位项目目录（脚本所在目录的上一级），`content_test.mjs` 默认读
  `../extension/content.js`，两者都不再写死任何机器上的绝对路径。已在仓库布局下实跑验证

## 未完成（按优先级排序）
- [ ] 等用户装上 v1.8.0（**扩展也要重装**，Chrome 卡片上应显示 1.8.0）并确认两件事：
      ①「我为什么收不到消息」直接在工作台问，它会自己查状态并给结论；②让它改一条规则，看动作行有没有 `✔`。
      不通就要一份新诊断包，先看 [0] 和 [4.5] 两段
- [ ] 规则没有导入/导出。用户填错了只能一格格改，给不了他一份「拿去直接用」的规则。
      要做就加 `POST /api/rules/import` + 界面导出按钮（导出带版本号和 schema 名，导入前先预览会覆盖什么）
- [ ] exe 打包只能在用户本机做（PyInstaller 无法在 Linux 交叉编译），dist\dcwatch.exe 从未在真实 Windows 上验证过
- [ ] 扩展始终没能在真实浏览器里 load unpacked 跑过端到端（云环境上传桥不可用），靠假 Discord DOM 的回归兜底
- [ ] `pullTabs()` 会向所有标签页广播 `dcwatch-pull`（无权限方案的代价）。标签页极多时是几十次
      必然失败的 sendMessage，可接受但不优雅；若将来已经要加权限了，可顺手改回按 url 筛

## 注意事项 / 踩过的坑
- **本 workspace 会被整体同步覆盖**：`.bat`、`.gitignore`、`.gitattributes` 会凭空消失（本轮又丢了一次）。
  改包前 `find outputs/dcwatch -type f | wc -l`（应为 **30** = 交付 26 + CLAUDE/HANDOFF + 两个点文件），
  少了就从 `git clone` 的远端副本里 cp 回来，并复验 `.bat` 是 **GBK + CRLF**
- **打包前先 `rm -rf __pycache__`**：`python3 -m py_compile server.py` 会留下 .pyc，
  第一次打的 v1.8.0 zip 里混进了两个 `__pycache__/*.pyc`（26 变 28 才发现）
- **并行 run 会改同一份文件**：本轮开工时 VERSION 已经是 1.8.0 而 HANDOFF 还写着 1.7.4。
  动手前先 `diff` 工作树和远端（`git clone` 到 /tmp 再 `diff -rq`），别假设工作树是自己上次留下的样子
- **改了扩展就必须 bump manifest version 和 EXT_MIN**，并在回复里告诉用户该看到几点几。
  反过来，扩展没改就别动这两个 —— 白让他重装一遍比不修还糟（v1.6.1 / v1.7.1 / v1.7.3 都是这么处理的）
- **测试里不要写死版本号**（e2e_diag 曾把桥的扩展版本写死，EXT_MIN 一 bump 就假红三条）
- 加了 chrome.runtime 的新消息类型，要同时在 content_test.mjs 的 chrome 桩里补 `onMessage`，
  否则 content.js 的 try/catch 会静默吞掉，测试看着全绿其实那段代码根本没跑
- **「有人发消息」和「有人开新帖」是规则里两个独立勾选（kinds）**，只勾后者时频道里聊翻天也不会响
- 排查「填好了为什么不响」不能只看规则条件本身，要拿真消息跑 match()。诊断包 [4.5] 段就是干这个的
- messages 表**没有 mentions_me 列**，所以 [4.5] 里勾了「只在@我」的规则一定算不中，已单独注一句
- Windows 的 .bat 必须 GBK + CRLF；echo 文本里的 `>` 要转义成 `^>`
- 交付 zip 一律带版本号，且旧版 zip 删掉，避免他拿错
- 回归一套一套跑（`/tmp/bcode/runall.sh`，每套之间清 /tmp/p.db* 并重启 server），一次喂两三套，
  bash 工具 120s 会超时。`runall.sh` 本身也会被同步删掉，丢了就照 RUN.md 重写
- 所有出网调用必须走 `App.rq()`（trust_env + net.proxy），否则国内直连 api.openai.com 必超时
- 界面绝不能拿假数据冒充成功；`S.live` 一旦为真永不回退
- 微信只走 Server酱 / 企业微信官方通道；火狐已彻底放弃，只支持 Chrome / Edge
- **推送凭据不在仓库里，也不该在**。它存在某个 workspace 的私有目录（`.bcode/agent-workspace/secrets/`），
  换账号接手时拿不到 —— 那时候直接跟用户要一个新的 GitHub PAT，用一次性 URL 推：
  `git push "https://x-access-token:<PAT>@github.com/tqzbceb/claude.git" HEAD:main`，
  输出过一遍 `sed 's/github_pat_[A-Za-z0-9_]*/***TOKEN***/g'`，推完 `grep -rl github_pat_ .git/` 确认无残留。
  用户 2026-07-31 明确要求「每次任务做完都推」。推前先 `git ls-remote` 看远端到哪了，别信记忆里的 sha
- **交付给用户的 zip 里不含 `tests/`、`AGENTS.md`、`CLAUDE.md`、`HANDOFF.md`**（26 个文件，
  只有他要跑的东西）。所以让接手的 AI **从 GitHub clone**，不要拿用户手上的 zip —— zip 里没有交接内容
