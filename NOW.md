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

## 状态
**正在做：B3+B4+B2+C3+D+B5 模型接入大轮，直接实现（2026-08-01 本窗口，Claude 亲自写码）**。
上个窗口只推了一句状态就被掐断，`PLAN_B3B4B2.md` 没写出来 —— 本窗口不再走「先写 PLAN 再换窗口
实现」两段式（那等于把 10 条消息里的一半花在写文档上），直接改代码 + 每步 save。
用户 2026-08-01 重发整包需求，逐条对过 BACKLOG：**没有新条目**，A1–A5+C1 已在 v1.9.4 里（等他装）。
新 PAT 已长存工作区私有目录 `.bcode/agent-workspace/secrets/`（不进仓库），用户授权长存。

本轮步骤（每步一 save）：
① 指针写进新工作区根 AGENTS.md + PAT 长存 + 环境自检（imp 74/74 绿）✓ ← 本次推送
② D：`docs/discord-命名约定.md` 定稿（5 层 + 4 点修订），B5 要引用它
③ B3：4 条骨架提示词（wizard/compose/workbench/tools）改成 cfg 可覆盖 + 界面可编辑；
   预设整包导出/导入（`dcwatch.preset/1`，dry_run 预览闸，照 extract_templates 抄）
④ B4：`ai.params` 温度/top_p/max_tokens/penalty/附加 JSON 透传，界面折叠区
⑤ B2：`ai.strict` 提示词后处理严格模式（合并同角色、system 归一、收尾必须 user）
   + ai_tag 默认提示词补格式指导和 few-shot
⑥ C3：模型选择挪进「接入的模型服务」每个服务卡片里
⑦ B5：内置「教模型写规则」预设（presets/ 目录，引用 D）
⑧ 回归 + 出 v1.10.0 包 + 推送；B1 / C2 写成 PLAN 留给下轮

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
