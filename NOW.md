# NOW —— 现在正在做的事

> 这一页是**进行中**的状态，粒度到"步"。`HANDOFF.md` 是做完一轮之后的总结，粒度到"轮"。
> 换窗口 / 换会话后聊天记录一句都不过来，**这一页就是替代品**。读完它你就知道上一个窗口
> 手停在哪儿、下一步该敲什么。空着（"没有进行中的任务"）才说明可以从 HANDOFF 挑新活。

## 状态
进行中

## 最后更新
2026-07-31 19:40（北京时间）· 界面 + 全量回归已完成，剩文档 / zip / 推送

## 正在做什么
两件事并行：
1. **（已完成）换窗口连续性加固** —— 见下面「本轮已完成」。
2. **（进行中）批量提取模板的导出 / 导入**（HANDOFF「未完成」第 3 项，第一件能自己动手的）。

## 手停在哪一步
- [x] 环境就绪：`pip install aiohttp`，`cd tests && ./runall.sh e2e.py e2e_imp.py` → 46 / 74 全绿
- [x] 摸清现状：`批量提取`（ui.html 的 `v-batch`）现在**根本没有模板这回事**，
      "要提取什么"是个每次手打的 textarea（`#bWhat`），换台机器 / 换个人就得重打
- [x] 连续性加固（NOW.md / 根 AGENTS.md / save.py）
- [x] server.py：`EXTRACT_SCHEMA`、`DEFAULT_TPL`、`norm_tpl(s)`、`tpl_for_export`、
      `diff_tpl`、`sanitize_import_tpl`；`/api/extract/export` + `/api/extract/import`
      （**dry_run 默认真**）；`/api/config` 认 `extract_templates`；诊断包 `[5.5]` 段
- [x] `tests/e2e_ext.py` **53 条全绿**
- [x] ui.html：批量提取页加「存为模板 / 选模板 / 导出 / 导入」+ 预览抽屉（已在浏览器目检：chips / 填表 / 存为模板 / 导入预览抽屉都对）
- [x] 回归全套重跑一遍：496 / 496 全绿（ext53 e2e46 imp74 ai27 wb94 diag47 multi26 v17-46 wiz83）
- [ ] bump 版本到 v1.9.3、README / RUN.md / HANDOFF.md、出 zip 进 `outputs/` + `release/`
- [ ] 提交推送

## 这一步的关键决定（省得下一个窗口重新想一遍）
- 模板存在 **cfg 的 `extract_templates`** 里（照 `quick_actions` 的样子），不新建表：
  它就是几个输入框的值，没有命中数这类本机账要记
- 导入**必须走跟规则一样的预览闸**（硬规矩 10）：`dry_run` 默认真，界面先摆
  「新增 / 覆盖 / 没变 / replace 会删谁」，用户点确认才第二次请求真写
- 重名认 `name` 不认 `id`（跟规则包一致，id 在两台机器上必然不同）
- `imported_at` / `imported_from` **不进 `DEFAULT_TPL`** —— 那是本机的账，
  进了就会跟着导出去（`e2e_imp.py` 第 13 节为规则钉过同一件事）
- `channel_id` 照导入规则那套：**只要求是纯数字，不校验本机存不存在**。
  导出方的频道 ID 在导入方库里当然查不到，按 known_ids 洗会把模板洗空

## 本轮已完成（换窗口连续性）
用户说他的账号是「10x10」：每个窗口 10 条消息，用完换窗口。目标是 10 个窗口跑成 1 个。
实测了换窗口到底丢什么（`.promote-manifest.json` 47 个文件 + 跟远端 `git ls-files` 对比）：

| 换窗口后 | 结果 |
|---|---|
| 工作区文件（.md/.py/.json/.zip/.wav/图片） | 在 |
| 根 `AGENTS.md` | 在，且**每个新会话自动读** —— 用户看到的"好像有记忆"就是它 |
| `.bat` / `.sh` | **丢**（`启动.bat`/`停止.bat`/`build.bat`/`tests/runall.sh` 四个全没） |
| `.git/` | **丢**，所以工作区的 `claude/` 是死快照不是仓库 |
| `outputs/` | **整个清空**，上一轮交付的 zip 已经不在了 |
| `/tmp` | 临时盘，别把唯一副本放那儿 |
| 聊天记录 | 一句不过来 |

对策（已落地）：
1. **这一页**（`NOW.md`）——粒度到步的进行中状态，每做完一步就更新并推送
2. 根 `AGENTS.md` 补了「换窗口丢什么」+「先读 NOW.md」的指针
3. `.bcode/agent-workspace/save.py` —— 一条命令存盘：commit + push + 把 .md 同步回工作区快照
   （**写成 .py 不是 .sh**，因为 .sh 不会被 promote，下个窗口就没了）
4. 交付 zip 一律**同时**进 `release/`（跟着 git 走），只放 `outputs/` 的话换窗口就没了
