# NOW —— 现在正在做的事

> 这一页是**进行中**的状态，粒度到"步"。`HANDOFF.md` 是做完一轮之后的总结，粒度到"轮"。
> 换窗口 / 换会话后聊天记录一句都不过来，**这一页就是替代品**。读完它你就知道上一个窗口
> 手停在哪儿、下一步该敲什么。空着（"没有进行中的任务"）才说明可以从 HANDOFF 挑新活。

## 状态
**没有进行中的任务** —— v1.9.3 已完整收尾（代码 + 53 条新回归 + 文档 + zip + 推送）。
**用户正在换账号**：旧号额度耗尽。新账号的 agent 是全新工作区，
先读根目录 `BOOTSTRAP.md` 自举（恢复 AGENTS.md 记忆 + save.py + PAT），再回到这页接活。
下一步从 `HANDOFF.md` 的「未完成」挑活；头两件（要诊断包 / 等装 v1.9.3 回执）只能等用户。

**交接已完成**：旧号最后一个窗口用用户新发的 PAT 验证过 clone + push 全通
（这条提交本身就是验证）。PAT 会由用户在新号第一条消息里直接粘贴，不在仓库里。

## 最后更新
2026-07-31 · 旧号最后一个窗口：验证新 PAT、盖章交接。下一条消息应该来自新账号

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
