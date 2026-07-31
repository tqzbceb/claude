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
**正在做 A4 施工**（Claude 窗口顺手接了实现：照 `PLAN_A4.md` 四处改 ui.html）。
步骤：①改添加行重排+说明 → ②行内类型下拉+applyPreset → ③模板地址不打码 → ④帮助文案
→ ⑤演示模式浏览器目检 → ⑥全套回归 500/500（server.py 没动，纯保险）→ ⑦BACKLOG 打勾+存盘。

### 发版前待办（攒着，哪轮发版哪轮做）
- 提 `EXT_MIN`（A2 动了 extension/content.js），用户需重装扩展；
- 让用户在真 Discord 验：①回复消息的盯人命中（A2）；②开两个 dcwatch 标签页只弹一条（A3）；
  ③关掉三个开关立刻全停（A5）；
- 发 zip 时同步 release/ 和 outputs/。

## 最后更新
2026-07-31 · PLAN_A4 出完（Claude 窗口，含根因实测：前端合成事件/真鼠标键盘/服务端 curl 三路
都证明选择器没坏，纯 UX 问题）。下一件：实现窗口照 PLAN_A4.md 施工，再下一件 Claude review。

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
