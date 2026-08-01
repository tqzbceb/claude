# HANDOFF

> **换了账号 / 新窗口第一次来？先读 `START_HERE.md`**（5 分钟，头四件事写死在那儿），
> 再读 `AGENTS.md`（项目怎么运作、用户是谁、十一条硬规矩、排查「它没提醒我」的固定顺序），
> 然后读这份看进度，`CLAUDE.md` 是工作协议。
> 回归套件在 `tests/`，跑法 `cd tests && ./runall.sh e2e.py e2e_imp.py`，细节见 `tests/RUN.md`。
> 这四份文件 + `tests/` + `release/` 就是全部交接内容，**不需要上一轮的聊天记录**。

## 最后更新时间
2026-08-01 · e2e_chk 还债轮（O 窗口）—— BACKLOG 代码侧清零

## 新账号 / 新窗口先看这三行
- 用户的账号是「10x10」：**每个窗口 10 条消息，用完换窗口**。进行中的状态在 `NOW.md`（粒度到步），
  接手先读它，别问用户上次做到哪。每做完一步就用 `.bcode/agent-workspace/save.py` 存盘推送。
- 接手后照 `START_HERE.md` 第 5.5 节把项目指针写进**新工作区根目录的 `AGENTS.md`**，
  别往后拖 —— 换窗口丢的从来不是代码，是「该去哪读」这个指针。
- 工作区里的 `claude/` 是**死快照**（没有 `.git`）。干活永远 `git clone` 到 `/tmp` 上改，改完推。

## 当前状态
**v1.11.0 已出包（2026-08-01）**：C2 全链路落地（服务端 + 扩展 + 界面 + 测试 + 文档 + 双 zip）。
三处版本号钉齐 **1.11.0**（程序 / EXT_MIN / manifest）。用户**必须重装扩展**（多 tabs 权限）。
回归：服务端 619 条 + 浏览器侧 89 条全绿。程序 zip 27 文件 + 扩展 zip 10 文件在 `release/` 和 outputs/。

下一件按顺序：
1. **等用户装 v1.11.0 真机验收**（扩展重装三步缺一不可；设置页「自动点开新帖」默认关；
   开了看后台标签 + 药丸「· 自动」；再验 A6 拉模型 / B1 AI 复核 / A2 A3 A5；要诊断包）。
2. 用户新反馈（验收后大概率有）。
**代码侧 BACKLOG 已清零**（O 窗口把最后一笔债 e2e_chk.py 还了）。

## 已完成
- **e2e_chk.py 专项债（O 窗口）** —— mockllm 加 aicheck 分支 + `/__chk` 脚本队列；
  `tests/e2e_chk.py` 56 条一次全绿（PLAN_B1 §3.3 的 13 条必钉全覆盖，含 fail open 三连、
  附件转人工零调用、每日上限）;全套回归复跑 **675 条全绿**；RUN.md 补覆盖清单+三个坑
- **v1.11.0（本轮 N 窗口）C2 收尾** ——
  · 扩展侧早前已落地（L `c13a0d8`：tabs 权限、background tabOrders 开/关/回报、storage opened、
    popup 自检行、content 药丸「· 自动」）；ui.html 折叠区 `842d069`；e2e_tabs 53 `66a78a9`
  · 本窗口补：`tests/content_test.mjs` runTabs + `tabs_harness.js` **27 条** chrome.tabs 桩全绿
  · VERSION/EXT_MIN/manifest **1.11.0**；README 新节 + 改版说明 + 故障表；START_HERE/release 同步
  · 全套回归全绿；程序+扩展两个 zip
- **v1.10.0 及以前**（详见 git 历史 / 下面存档）：A1–A6、B1–B6、C1、C3、C4、D 全落地

## 未完成（按优先级排序）
- [ ] **等用户装 v1.11.0 真机验收 + 要新诊断包**（扩展必须重装到 1.11.0）
- [ ] exe 打包只能在用户本机做；扩展未在真 Discord load unpacked 端到端验过（靠假 DOM 回归）

## 注意事项 / 踩过的坑
- **e2e_chk 的模型名用 "mock-chk" 不用 "mock-1"**：`App.no_tools` 是进程内内存，跟 e2e_wb
  共用模型名会互相污染（PLAN_B1 点名过的坑）；复核上下文是 `ts<=当前` 取 n+1 条，
  当前这条也在结果里靠正文不等滤掉 —— 造测试数据别让上文跟触发消息一字不差
- **C2 扩展字段名 ≠ 服务端字段名**：扩展用 `aoOn` / `rep.opened|closed|failed` / `chrome.tabs.*`，
  服务端用 `tabs_report` / `threads_open` / `idle_close`。用服务端名 grep 扩展会**错判没做**（M 窗口踩过）
- **runTabs 的 harness 必须是 async IIFE**，且宜拆成 `tabs_harness.js` 再 Runtime.evaluate 注入；
  塞进 content_test.mjs 模板字符串里容易把 `async` 弄丢 → Uncaught
- 交付包 **27 文件**（含 presets/）；扩展包 **10 文件**；动了扩展才出扩展 zip（硬规矩 2）
- 出包前三处版本号必须一致（VERSION / EXT_MIN / manifest）。半吊着出包用户必装错
- 其余坑见 v1.10.0 段（import_rules 不加、export 打码、WB_PACK_LIMIT、跑回归先 kill 8777、
  .bat GBK+CRLF、setsid 起服务、推送用一次性 URL PAT）


<details>
<summary>v1.10.0 及更早的技术细节（点开看）</summary>

见 git 历史中本文件 2026-08-01 K 窗口版本，以及 PLAN_*.md / BACKLOG 各条 commit。
要点：A1/C1 工作台落库；A2 回复消息作者；A3/A5 通知 tag+sinks SSE；A4 转发出口 UX；
A6 拉模型三根因；B1 AI 复核；B2/B3/B4/B5/C3 提示词/采样/后处理/预设；B6 只读工具；
C4 输入框；C2 服务端 f5cd2c5；D 命名约定。

</details>
