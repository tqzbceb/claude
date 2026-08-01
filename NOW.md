# NOW —— 现在正在做的事

> 这一页是**进行中**的状态，粒度到"步"。`HANDOFF.md` 是做完一轮之后的总结，粒度到"轮"。
> 换窗口 / 换会话后聊天记录一句都不过来，**这一页就是替代品**。读完它你就知道上一个窗口
> 手停在哪儿、下一步该敲什么。空着（"没有进行中的任务"）才说明可以从 HANDOFF 挑新活。

## 窗口工作协议（用户 2026-07-31 定的，严格执行）
1. 用户消息有新需求 → **动任何代码之前**先写进 `BACKLOG.md` 并推送。
   窗口随时可能被额度掐断，没推送的需求＝丢了，用户说「继续」下个窗口就不知道继续什么
2. 挑定这个窗口的**一个**小任务后，先把本页「状态」改成「正在做 XX，步骤：…」并推送，再动手
3. 干活中每完成一步就 save 一次（save.py 会顺手推送），本页始终反映真实进度
4. 做完把本页改成「做完了 XX，下一件是 XX」并推送
5. 回复末尾报：✅ 本窗口完成 XX，已推送；下窗口发「继续」即可

## 双模型流程（用户 2026-07-31 启用，从 A2 开始）
Claude 只做深度思考，把方案写成 `PLAN_<任务>.md` 推送；实现窗口用户会换 **Kimi K2**（或别的快模型）
照 PLAN 写代码。实现完，之后再来一个 Claude 窗口只做 review（对照 PLAN 检查 diff + 回归结果）。
**实现窗口接到「继续」：读本页 → 读状态里指的 PLAN 文件 → 照施工图干，别自由发挥。**

## ⚠ 并行窗口警告（2026-08-01，实测到三个窗口同时在干这一轮）

用户同一段接手提示词发了不止一次，**同时有三个 worker 在改同一个仓库**。
远端 log 上能看出来：`386a8bb` 是 A 窗口写的 B3+B4+B2 服务端，`216a936` / `aeb1970`
是 B / C 窗口宣布「开写 B3 界面侧」。**第四个窗口来的时候先做这三件事**：

1. `git fetch` 看远端最新几条 commit message —— 上面写着别人正在做哪一块，**别撞**；
2. 动手前把「我这个窗口做哪一块」推一条状态（就一行也行），让后来的人看得见；
3. 干完 `git pull --rebase` 再推。save.py 撞了会直接 fail（不会强推，安全）。

D 窗口（本条的作者）实测到的分工结果，**已经做完的别重做**：
- **D 定稿**：`docs/discord-命名约定.md` ✓（`9761700`）
- **B3+B4+B2 服务端**：A 窗口 ✓（`386a8bb`，sys_prompts / `/api/preset/*` / ai.params / ai.post 三档）
- **全套回归复跑在 A 窗口的代码上：545/545 全绿** ✓（D 窗口亲测：e2e 48 · ai 27 · multi 26 ·
  wb 99 · imp 74 · ext 53 · v17 46 · diag 47 · chat 42 · wiz 83）→ **A 的服务端 review 通过**
- **B3 界面侧 / C3 / B5**：B、C 两个窗口在做（别再插手 `ui.html`，会打架）
- **B1 施工图 `PLAN_B1.md`** ✓ D 窗口写完（AI 复核规则，含三种反侦察场景 + 附件转人工）
- **C2 施工图 `PLAN_C2.md`** ✓ D 窗口写完（新帖可见性真相 + 自动开帖四道闸 + 闲置自关）
- 还没人做：~~B1 实现~~（**E 窗口 2026-08-01 认领了，正在做，见下面「E 窗口」段**）、
  **C2 实现**、**B6**（模糊需求，见 BACKLOG）、**出 v1.10.0 包**（C 窗口的 ⑥ 在做）

## E 窗口（第四个，2026-08-01）认领：B1 实现（AI 复核规则）
照 `PLAN_B1.md` 施工。**只动 server.py + tests/e2e_chk.py**，
`ui.html` 的那一小块（规则里「AI 复核」开关）留到最后一步、`git pull` 之后再动，
免得跟 C 窗口正在收尾的 ⑥ 打架。C 窗口出 v1.10.0 包时如果 B1 还没进去，就别把 B1 算进版本说明。

## F 窗口（第五个，2026-08-01 18:58 UTC）认领：C2 实现（新帖可见性 + 自动开帖 + 闲置自关）
照 `PLAN_C2.md` 施工。**主战场是 `extension/`（没人碰）+ server.py 里心跳/指令那几段**。
- 跟 E 窗口共用 server.py：我只加新的函数和路由，不改 B1 的 `aicheck` 相关代码；推之前 pull --rebase
- 硬规矩 2：动了 extension → 必须 bump `extension/manifest.json` 的 version + server.py 的 `EXT_MIN`。
  **C 窗口的 v1.10.0 出包如果在我之前完成，我就把两处一起提到 1.10.0**；
  它没完成的话由它统一 bump（我只保证 manifest 与 EXT_MIN 数值一致）
- 不动 ui.html 的大结构（C 窗口刚改完），只在必要处加「自动开帖」那一小块开关

## G 窗口（第六个，2026-08-01）认领：C 窗口没做完的 ⑥（回归 + 真·目检 + 收尾），不动别人的地盘

C 窗口把 ②③④⑤ 推上来（`18c7b62`）之后就断了，**⑥ 一步没做**：没跑全套回归、没目检、没出包。
我（G 窗口）接这一步。**只做验证 + 修验证里发现的 bug，不加新功能**：
- 全套回归复跑（含 C 的界面轮 + E 的 B1 服务端 + F 的 C2 服务端）
- **真·live 目检**（不是演示模式）：新写了一条桥 `.bcode/agent-workspace/uibridge.mjs` ——
  云浏览器够不到本机 8777，用 CDP 的 `Fetch` 域把页面请求在 node 侧转发给真 server.py，
  于是页面是真 http 源、`S.live` 是真的、点按钮真的写库。这是唯一能真目检 live 模式的路子
  （`data:` URL 被策略拦；`document.write` 会把整块 `<script>` 弄坏 —— 两条都踩过了）
- **出包留到最后**：E 窗口的 B1 界面块和 F 窗口的 C2 扩展改动都还没进来，
  现在出 v1.10.0 会出一个半成品。谁最后一个完成谁出包，出包前必须 `git pull --rebase`

## H 窗口（第七个，2026-08-01）认领：用户需求总核销 + BACKLOG 打勾（不动任何人的代码地盘）

用户把原始需求清单又完整贴了一遍（自己也说「可能有些已做完，你自己审核」）。本窗口只做**核销**：
- clone 新工作区 → 五份文档读完 → 根 AGENTS.md 指针 ✓ → PAT 两个长存 secrets/（P2 新贴的优先试，P1 备用）✓
- 环境自检 e2e 48/48 + imp 74/74 全绿 ✓
- **核销结论：用户 16 条需求全部已在 BACKLOG，无新条目**。A1–A5、C1、B2、B3、B4、B5、C3、D 已定稿 =
  代码全落地（commit 见 BACKLOG 各条）；B1 剩 ui.html 开关（E 收尾）、C2 剩扩展侧（F 施工）、
  ⑥ 回归目检（G 在做）、出 v1.10.0 包 = 等 E/F/G 收尾。BACKLOG 勾已照此补齐。
- **唯二还张着嘴的事**：① B6「模型可活动空间太少」是模糊需求，没人认领，已在回复里问用户要具体场景；
  ② 用户说「日志也发了」但这条消息里没看到诊断包内容，已请他把 txt 原样重贴。
- 本窗口**不改代码**（E/F/G 都在各自地盘上，撞车是本轮最大风险），推完这条状态就交棒。

## I 窗口（第八个，2026-08-01）认领：A6（模型全拉不到）→ C4（输入框对齐）→ B6（只读工具）

用户新消息三条：①工作台模型能调用的功能太少（=B6 澄清）；②工作台输入框第一个字不顶左上角（=C4）；
③**最大的问题：两个模型端点全拉不到**（官方 401 + 中转 ClientPayloadError:400），并附了诊断包。
- 工作区又是新的：clone ✓、五文档读完 ✓、根 AGENTS.md 指针 ✓、PAT 长存 secrets/ ✓
  （用户又贴 token2 …QFAiRGbA，**实测 401 无效**；能推的还是 token1 …0Dx28of，已用它）
- 诊断包读完，A6 根因已定位三条（见 BACKLOG A6）：cleanProviders 抹 key / base_url 尾巴 /model 没剥 /
  ClientPayloadError 没人话
- 施工顺序：A6（ui.html cleanProviders + server base_candidates/list_models 提示）→ C4（先目检复现）
  → B6（一批只读工具 + WB_TEXT_PROTO 同步 + e2e_wb 断言）。每步 save。
- E/F/G 的残活（B1 界面开关 / C2 扩展侧 / 回归出包）**本窗口不碰**，做完上面三件有富余再说。

## J 窗口（第十个，2026-08-01）认领：接着 I 窗口断点 —— C4 → B6 →（有富余）B1 界面开关

I 窗口把 A6 修完推完（`50ea64d`）就断了，**C4 / B6 一行没动**（代码里核实过：wbInput CSS 没改、
WB_TOOLS 还是 12 个没有新增）。E/F/G 三个窗口也全断：B1 界面开关（ui.html 无 ai_check 痕迹）、
C2 扩展侧（extension/ 无 tabs_report 痕迹）、回归目检出包都没做完。
本窗口顺序：① A6 打勾 ✓ → ② C4（重建 uibridge 目检复现再定改法）→ ③ B6 只读工具
（+WB_TEXT_PROTO 同步 + e2e_wb 断言）→ ④ B1 界面开关（照 PLAN_B1 §3.2，纯 ui.html）。
**C2 扩展侧本窗口不碰**（动 extension 要 bump 版本逼用户重装，单独一轮，见 PLAN_C2.md）。
工作区又是新的：clone ✓、五文档读完 ✓、PAT 长存 secrets/ ✓（还是 …0Dx28of 那个）。

## K 窗口（第十一个，2026-08-01）认领：全套回归 + 出 v1.10.0 包（本轮最后一步）

J 窗口留的唯一一件事。A6/C4/B6/B1 全是程序侧，扩展自 v1.9.4 起一行没动 → **只 bump
VERSION，不碰 manifest/EXT_MIN，不出扩展包**。步骤：
① 接手（新工作区：clone ✓、五文档读完 ✓、根 AGENTS.md 指针 ✓、PAT 长存 secrets/ ✓，token1 可推）
→ ② 远端核实 HEAD=e046e9c 无人撞车 → ③ 全套回归（e2e/ai/multi/wb/imp/ext/v17/diag/chat/wiz）
→ ④ bump VERSION 1.9.4→1.10.0 + README/release 文档改版 → ⑤ /version 冒烟 → ⑥ 出程序 zip
（release/ + outputs/）→ ⑦ NOW/BACKLOG/HANDOFF 收尾推送。
**C2 扩展侧不碰**（单独一轮）；e2e_chk.py 专项债有余力就还。

## L 窗口（第十二个，2026-08-01）认领：C2 扩展侧（自动开帖 + 闲置自关）

照 `PLAN_C2.md` 施工。服务端 F 窗口已全落地（`f5cd2c5`，本窗口核实过：norm_browser /
threads_open / tab_orders / /api/ext/tabs / list_open_threads / 诊断[5.8] 全在）。
**剩下全是本窗口的活**：extension/（manifest 加 tabs 权限 + background 执行开/关 + 回报 +
storage 记 opened + popup 自检行 + 药丸「· 自动」标记）、ui.html 设置页「自动点开新帖」折叠区、
tests/e2e_tabs.py（≥35 条）+ content_test.mjs 加 chrome.tabs 桩（≥10 条）、README 新一节、
三处版本号一起 bump（1.10.0→1.11.0，动了扩展，硬规矩 2）。
工作区又是新的：clone ✓、五文档读完 ✓、根 AGENTS.md 指针 ✓、PAT 长存 secrets/ ✓（token1 …0Dx28of）、
环境自检 e2e 50/50 + imp 74/74 全绿 ✓、远端核实 HEAD=887e22d 无人撞车 ✓。

## M 窗口（第十三个，2026-08-01）只做审计 + 需求拆分，一行代码没动

用户又把 16 条需求整体贴了一遍并问「帮我拆分，可能有些已做完你自己审」。本窗口**只审不改**
（L 窗口的地盘 extension/ 还张着嘴，撞车代价大）。核销结论：**16 条 15 条已落地**
（A1–A6 / B1–B6 / C1 / C3 / C4 / D 全部 ✅，见 BACKLOG 各条 commit），只有 **C2 未完**。

**L 窗口真实断点（M 窗口 11:00 UTC 复核，已修正本条早前的错判）**：
- ⚠ **更正**：本窗口第一版写「extension 侧一行没做」是**错的** —— 我拿服务端字段名
  （`tabs_report`/`threads_open`/`idle_close`）去 grep 扩展，扩展里用的是另一套命名
  （`aoOn` / `rep.opened|closed|failed` / `chrome.tabs.create|remove` / `onRemoved` / `/api/ext/tabs`）。
  **C2 扩展侧实际已落地**（`c13a0d8`）：3 秒轮询取指令、开帖建后台标签、闲置关帖、失败回报、
  标签被手关时清 opened —— 全在 background.js 里。别重做。
- ✅ 已做：extension 四件（`c13a0d8`，manifest 加 tabs 权限 + bump 1.11.0）· ui.html 折叠区
  （`842d069`）· `tests/e2e_tabs.py` 53 条全绿（`66a78a9`，还顺带修出服务端幻影桥真 bug）
- ❌ **剩余（下个窗口从这里接）**：① `tests/content_test.mjs` 的 chrome.tabs 桩（≥10 条，现在只有
  1 处提及）→ ② 全套回归 → ③ **三处版本号钉齐 1.11.0**（现在 1.10.0 / 1.9.4 / 1.11.0 不一致）
  → ④ README 新一节（现在没有「自动点开」字样）→ ⑤ 出程序 + 扩展两个 zip 进 release/ 和 outputs/
  → ⑥ NOW/BACKLOG/HANDOFF 收尾
- L 窗口最后一次推送 10:53:39 UTC，到 11:00 无新 commit：要么在跑回归，要么已断（用户侧看不到，
  因为它跑在池子 key 的项目里）。接手前先 `git fetch` 看有没有新 commit。

**⚠ 雷：三处版本号现在不一致** —— `server.py VERSION=1.10.0` / `EXT_MIN=1.9.4` /
`manifest=1.11.0`。违反硬规矩 2+5，**在这个状态出包用户必装错版本**。下个窗口第一件事：
要么把 C2 做完后三处一起钉到 1.11.0，要么先把 manifest 回退到 1.10.0，别让它半吊着。

**给用户的拆分（三轮，本窗口回复里也是这个）**：
1. **C2 扩展侧收尾**（最大一轮，照 PLAN_C2.md）：background 开/关帖执行 + 回报 + storage 记
   opened + popup 自检行 + 药丸「· 自动」→ e2e_tabs.py ≥35 条 + content_test.mjs chrome.tabs 桩 ≥10
   → 三处版本号统一 1.11.0 → README 新节 → 出程序 + 扩展两个 zip
2. **e2e_chk.py 专项债**（PLAN_B1 §3.3，≥40 条 AI 复核断言，E 窗口欠的）
3. **真机验收**（用户侧，不用 AI 窗口）：拉模型 401 修好没 → B1 AI 复核真盯 key 社群 →
   A2 回复消息盯人 / A3 多标签只弹一条 / A5 关开关立刻停 → 导一份诊断包发来

## 状态
**✅ e2e_chk.py 专项债已还清（O 窗口，2026-08-01）—— BACKLOG 代码侧清零。**
五步全做完：① 认领推送（`750ff37`）→ ② mockllm 加 aicheck 分支 + `/__chk` 队列
（{"json"}/{"raw"}/{"http"}/{"bad"}，队空默认 hit:true 90 分，「复核员」判型排最前）→
③ `tests/e2e_chk.py` **56 条一次全绿**（§3.3 的 13 条必钉全覆盖：
回归保护 / 放行留痕 / 压掉日志 / 门槛边界>= / fail open 三连 / 附件转人工零调用 /
human=False 不通知 / ctx 真喂上文 / 不命中不调模型 / 导出导入五字段 / 诊断[4]+[4.6] /
每日上限 fail open）→ ④ 全套回归复跑 **675 条全绿**（e2e 50 · ai 27 · multi 26 · wb 118 ·
imp 74 · ext 53 · v17 46 · diag 47 · chat 42 · wiz 83 · tabs 53 · chk 56）→
⑤ RUN.md 覆盖清单+三个坑、BACKLOG 勾债、AGENTS.md 条数 619→675、NOW/HANDOFF 收尾。
**没有进行中的任务。代码侧一件都不剩了。** 下一件全在用户侧：
装 v1.11.0（程序+扩展都换，**扩展重装三步必做**，多了 tabs 权限）→ 验自动开帖 + A6 拉模型 +
B1 AI 复核 + A2/A3/A5 → 导一份新诊断包发来。

## 状态
**🔨 O 窗口（第十五个，2026-08-01）认领：e2e_chk.py 专项债（PLAN_B1 §3.3，代码侧最后一块）。**
用户发「1」让继续。接手核实：C2 已由 N 窗口收尾、v1.11.0 已出包（`6bc3ac8`），
BACKLOG 代码侧只剩这一条债。步骤：① 认领推送 → ② 读 mockllm/e2e_wiz.msys/e2e_imp§13/e2e_tabs
模板 → ③ 写 tests/e2e_chk.py（≥40 条，钉死 §3.3 的 13 条）→ ④ 自测全绿 + 相关回归复跑
（e2e/ai/imp/diag/wb/chat）→ ⑤ RUN.md 加覆盖清单+两个坑、BACKLOG B1 打勾、NOW/HANDOFF 收尾推送。
工作区又是新的：clone ✓、根 AGENTS.md 指针 ✓、PAT 长存 secrets/ ✓（token1 …0Dx28of 可推）、
远端核实 HEAD=6bc3ac8 无人撞车 ✓。

## 状态
**✅ C2 收尾完成，v1.11.0 已出包已推送（N 窗口，2026-08-01）。**
六步全做完：① content_test chrome.tabs 桩 27/27 ✓ → ② 全套回归 **619 服务端 + 89 浏览器侧全绿**
（e2e 50 · ai 27 · multi 26 · wb 118 · imp 74 · ext 53 · v17 46 · diag 47 · chat 42 · wiz 83 · tabs 53；
content run 46 · runFresh 16 · runTabs 27）→ ③ VERSION/EXT_MIN/manifest 钉齐 **1.11.0** →
④ README 新节「不点开的帖子看不到帖内消息」+ v1.11.0 改版说明 + 故障表 → ⑤ 程序 zip（27 文件）+
扩展 zip（10 文件）进 release/ 和 outputs/，旧 1.10.0/1.9.4 删了 → ⑥ NOW/BACKLOG/HANDOFF 收尾。
**没有进行中的任务。下一件在用户侧**：装 v1.11.0（程序+扩展都换，扩展重装三步必做）→
验自动开帖（设置页默认关；开了看后台标签页 + 药丸「· 自动」）+ 老验收项 + 要诊断包。
代码侧下一件：e2e_chk.py 专项债（PLAN_B1 §3.3）。


~~✅ v1.10.0 已出包已推送（K 窗口，2026-08-01），本轮（A1–A5、B1–B6、C1、C3、C4、D）全部落地。~~
K 窗口做完：全套回归 **566/566 全绿**（e2e 50 · ai 27 · multi 26 · wb 118 · imp 74 · ext 53 ·
v17 46 · diag 47 · chat 42 · wiz 83）→ bump VERSION 1.9.4→1.10.0（**EXT_MIN/manifest 没动**：
extension/ 自 458e1e6 起 0 commit，实测过）→ README/release/START_HERE 改版
（新增「v1.10.0 这一版加了什么」+ 故障对照 5 条）→ /version 冒烟印 v1.10.0 ✓ →
程序 zip（**27 文件**，多了 presets/，打包子流程和交付 zip 里 /api/presets 实测能列出来）进
release/ + outputs/，旧 1.9.4 程序包删了（扩展包还是 v1.9.4 那份）。打包文档 26→27 已同步改。
**没有进行中的任务。下窗口的活**（按序）：① 等用户真机验收 v1.10.0（重点：拉模型 401 修好没、
B1 AI 复核真开起来盯 key 社群）+ 顺嘴要新诊断包；② C2 扩展侧（PLAN_C2.md，动 extension，
单独一轮，bump manifest + EXT_MIN）；③ e2e_chk.py 专项债（PLAN_B1 §3.3，≥40 条）。
<details><summary>K 窗口之前的状态存档（I/J 窗口，点开看）</summary>

**I 窗口进度：A6 ✅（50ea64d）→ C4 ✅ CSS 钉死 → B6 ✅ 只读工具 6 个（list_providers/list_hooks/
recent_hits/test_message/get_logs/export_extract_templates）+ WB_TOOLS/WB_TEXT_PROTO/READ_HUMAN 同步 +
e2e_wb 第 15 节 19 条（118/118 全绿，中间踩了 json.dumps 转义中文的坑，断言要按解析后的结构写）。**
顺手重写了 G 窗口丢掉的 uibridge（python 版 `.bcode/agent-workspace/uibridge.py`，
CDP 转发本机 8777，真 live 目检通了）。
**J 窗口对 C4 做了二次升级**（跟 I 的 CSS 钉死合并保留）：空盒从 52px 改成单行 26px 起步、
随内容长高（160px 封顶）、发送后缩回、点盒子空白处聚焦 —— 两家都复现不了他的现象
（空 textarea 点击落点恒为 (0,0)），真凶最可能是「高空盒造成的字与点击位置错位感」，
单行盒从根上消除；演示目检 26→98→26 + 聚焦全过。若他还说不行，请他发截图。
**J 窗口做完 B1 界面开关**（最后一块）：规则编辑页动作块下「AI 复核」折叠区（五控件 +
人话说明照抄 PLAN + 滑杆实时文案）+ 规则列表 `AI 复核` 标签，演示目检全过，e2e 50 + imp 74 绿。
**B1 至此全落地；遗留债：PLAN_B1 §3.3 的 tests/e2e_chk.py（≥40 条专项）E 窗口没写。**
**J 窗口环境发现二则**：① `Page.setDocumentContent` 会执行内联脚本，演示目检不用桥 ——
先导航到任意 https 真源再注入（about:blank 上 localStorage 抛错，app 脚本会同步崩在中段）；
② **同一标签页重复注入会 SyntaxError（const 重复声明），旧 JS 全局残留 —— 每次注入前必须
先重新导航刷掉全局**，不然改完的代码根本不生效，目检会骗你。Fetch 拦截桥（mjs 版）反复
重连后会拖死 CDP 消息泵，慎用。
**下一个窗口只剩：全套回归 + 出 v1.10.0 包**（A6/C4/B6/B1 全是程序侧，扩展没动 → 只 bump
VERSION，不碰 manifest/EXT_MIN，不出扩展包）；C2 扩展侧仍是单独一轮（动 extension 才
bump manifest + EXT_MIN）；e2e_chk.py 专项债有余力就还。
本窗口进度：① 接手/PAT/指针 ✓ → ②③④⑤ 代码已写完（提示词抽屉可编辑+预设导入导出、
采样参数折叠区+后处理三档、模型选择进服务卡片、presets/教模型写规则.json + /api/presets 两个接口）
→ 现在做 ⑥ 回归 + 目检 + 出包。
服务端已在 386a8bb 落地（sys_prompts 可覆盖 / /api/prompts 可编辑 / /api/preset 导出导入 dry_run /
ai.params / ai.post 三档 / ai_tag 补格式标尺 / 诊断包 [5.7]）。界面侧动 ui.html + presets/，
另外给 server.py 补了 `/api/presets` `/api/presets/file` 两个只读接口（界面要列自带预设）。
新工作区（第三个）已重建：PAT 长存 secrets/、save.py 就位、根 AGENTS.md 指针已写。

本轮步骤（每步一 save）：
① 接手 + 指针 + PAT 长存 ✓（2026-08-01 第三个窗口重做了一遍：工作区又是新的，claude/ 已重新
   clone 成活仓库，PAT：用户给的两个里**第一个只能读不能写**，能推的是 …0Dx28of 那个，已存 secrets/github_pat_tqzbceb.txt，根 AGENTS.md 指针已写，自检 48+74 绿）
② 界面：骨架提示词抽屉从「只读」改成可编辑（6 条 textarea + 存 + 逐条恢复出厂）
   + 预设 ⇡导出 / ⇣导入（走 dry_run 预览闸，照提取模板抄）
③ 界面：B4 采样参数折叠区（温度/top_p/max_tokens/两个惩罚/附加 JSON）+ B2 后处理三档下拉
④ C3：模型选择挪进「接入的模型服务」每张服务卡片里（默认模型区保留但改成只显示结果）
⑤ B5：presets/教模型写规则.json 内置预设 + 界面一键装（引用 docs/discord-命名约定.md）
⑥ 回归（e2e/ai/wb/diag/imp）+ 出 v1.10.0 包（程序 zip，扩展没动不出）+ 推送
⑦ B1 / C2 / B6 留给下一轮（B1 最大，单独一轮；C2 动 extension 要 bump 版本）
</details>

---
（以下为 v1.9.4 出包轮的存档状态）
v1.9.4 已出包 + 已推送 GitHub（2026-07-31，远端 main = 458e1e6），等用户真机验收。
出包窗口做完：① 三处版本号 bump（VERSION/EXT_MIN/manifest 全到 1.9.4）✓ → ② README/release/
START_HERE 文档改版 ✓ → ③ 回归 542/542 全绿重跑 ✓ → ④ /version 冒烟印 v1.9.4 ✓ →
⑤ 程序 zip（26 文件）+ 扩展 zip（10 文件）进 release/ 和 outputs/，旧 1.9.3 删了 ✓。
推送窗口做完：用户给了 PAT，核实 458e1e6 早已在远端（上个窗口其实推成功了）；把远端 .git
拉回工作区快照 ./claude/（现在它又是活仓库了，PAT 在 .git/config 里，本工作区内推送直接可用）；
补回换窗口丢的 4 个文件（启动.bat/停止.bat/build.bat/tests/runall.sh）；两个 zip 重新进 outputs/。
**代码侧没有任何未完事项，下一件全在用户侧**（见发版后待办）。

**review 结论：通过，无返工项。** diff 对照 PLAN_A1C1.md 逐行核过：服务端两表/五接口/
wb_save_pair/落库点位/[5.6] 段与 PLAN 一致；前端会话栏/wbSend 带 sid/新话题/boot 装载一致；
「不许做的事」七条全遵守（extension 未动、VERSION/EXT_MIN 未动、wb_prepare 截 8 条未改、
诊断包只印条数时间、演示不调后端）。回归亲测复跑 **542/542 全绿**
（e2e 48 + ai 27 + multi 26 + wiz 83 + v17 46 + diag 47 + wb 96 + imp 74 + ext 53 + chat 42）。
施工记录：
- server.py：wb_sessions/wb_msgs 两表、`wb_save_pair`、五个 `/api/wb/*`、ask/stream 成功路径
  落库（plain=1 与失败分支不进库）、诊断包 `[5.6] 工作台会话`（只印条数+时间）
- ui.html：左侧会话栏（新建/切换/改名/删除）、boot 装载、`wbSend` 带 sid、发送后刷新列表、
  「清空」改「新话题」语义、演示分支 renderSess() 补一句（不然演示看不到「还没有会话」提示）
- tests/e2e_chat.py 新建 **42 条**全绿（跑三遍稳定）；**全套回归 542/542 全绿**（500 旧 + 42 新）
- 浏览器目检：演示模式（会话栏/新话题/演示发消息）✓；live 全流程序（fetch 桥+localStorage 模拟
  后端，因云浏览器够不到本机 8777、本机出网拦 Cloudflare 隧道）：启动建会话 → 发两条入库顶名 →
  模拟 F5 完整恢复 → 新会话/切换/改名(prompt)/删除(confirm)/新话题 全部真实点击通过 ✓；
  真服务端 curl 验证 ask→落库→诊断包 [5.6] 印出 `#1 … | 2 条消息 | 最后 …` ✓
- 坑已写进 tests/RUN.md：失败注入不能写错 provider 名（`App.provider()` 会兜底到第一个），
  要用 mockllm `{"http":{"status":500}}`；「删当前会话 cur 清零」的测试要先 open 切成当前
- RUN.md（542 条+e2e_chat 段）、README（AI 工作台存库+会话列表三行）、BACKLOG（A1/C1 打勾）已更新
- 没动 extension/、没 bump 版本号、没碰 EXT_MIN（PLAN 写死的三条都遵守）

### 发版后待办（用户侧，v1.9.4 装好后验）
- 重装扩展三步缺一不可：覆盖 extension 文件夹 → chrome://extensions 点 ⟳ → Discord 页面 F5，
  卡片和界面顶部都应显示 v1.9.4；
- 真 Discord 验三条：①回复消息的盯人命中（A2）；②开两个 dcwatch 标签页只弹一条（A3）；
  ③关掉三个开关立刻全停（A5）；
- 顺嘴要一份诊断包（运行日志页 → 导出诊断 → txt 原样发来），到现在还没人见过他机器上的规则。

## 最后更新
2026-08-01 · **新号接手完成**。照 START_HERE 走完：clone 到新工作区 ./claude（活仓库）→
读完五份 .md → 指针已写进新工作区根 AGENTS.md（5.5 节那段）→ 环境自检 e2e 48/48 +
imp 74/74 全绿 → v1.9.4 两个 zip 从 release/ 补回 outputs/（用户可直接下载）。
用户在交接消息里给了 PAT（未落盘未进仓库，一次性 URL 推送）。
**代码侧仍零欠账，没有进行中的任务。下一件在用户侧**：真机验三条（A2/A3/A5，
见「发版后待办」）+ 顺嘴要诊断包。用户回执后再开 BACKLOG 的 B3+B4+B2（模型接入大轮）。

## 上一轮（v1.9.3）做完了什么
批量提取的模板 + 整包导出 / 导入，全流程收尾：
- [x] server.py：`EXTRACT_SCHEMA`（dcwatch.extract/1）、`DEFAULT_TPL`、`norm_tpls`、
      `tpl_for_export`、`diff_tpl`、`sanitize_import_tpl`；`/api/extract/export` +
      `/api/extract/import`（dry_run 默认真）；`/api/config` 认 `extract_templates`；诊断包 `[5.5]` 段
- [x] ui.html：批量提取页模板 chips / 存为模板 / ⇡ 导出 ⇣ 导入 + 预览抽屉（浏览器目检过）
- [x] `tests/e2e_ext.py` 53 条全绿；全套回归 496 / 496 全绿
- [x] 文档：README（批量提取一节 + 故障表 + 版本号）、tests/RUN.md（e2e_ext 覆盖 + 496）、
      HANDOFF.md（本轮总结 + 未完成更新）、START_HERE.md、AGENTS.md、release/README.md
- [x] zip：`dcwatch-v1.9.3.zip`（26 文件 / 361KB，.bat 复验 GBK+CRLF）进 `release/` 和 `outputs/`，
      旧 1.9.2 删掉。解压后真起过一次：`/version` 写 v1.9.3，存模板 → 导出接口口径全对
- [x] 提交推送

## 交接时的关键决定（做类似功能照抄这套）
- 模板存在 **cfg 的 `extract_templates`** 里（照 `quick_actions`），不新建表
- 导入走跟规则包**同一道预览闸**（硬规矩 10）：dry_run 默认真 → 界面预览 → 用户确认才写
- 重名认 `name` 不认 `id`；`imported_at` / `imported_from` **不进 `DEFAULT_TPL`**（不外泄）
- `channel_id` 只要求纯数字，**不按本机 known_ids 洗**（导出方的 ID 本机必然查不到）

## 换窗口丢什么（实测过，别再测一遍）
| 换窗口后 | 结果 |
|---|---|
| 工作区文件（.md/.py/.json/.zip/.wav/图片） | 在 |
| 根 `AGENTS.md` | 在，且**每个新会话自动读** |
| `.bat` / `.sh` | **丢** |
| `.git/` | **丢**，工作区的 `claude/` 是死快照不是仓库 |
| `outputs/` | **整个清空** |
| `/tmp` / 聊天记录 | 没了 |

对策：干活 `git clone` 到 `/tmp/dcw`；每做完一步 `python3 .bcode/agent-workspace/save.py "做了什么"`；
交付 zip 同时进 `release/` 和 `outputs/`；进行中状态写在这一页。
