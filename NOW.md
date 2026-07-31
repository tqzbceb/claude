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
**A2 方案已写完：`PLAN_A2.md`（施工图，四处修改 + 两套回归 + 验收命令都定死了）。**
下一个窗口（不管什么模型）发「继续」= 照 `PLAN_A2.md` 施工：
1. [ ] 修改 1+2：server.py test_rule 加 author_id 参数 + 默认取规则 author_ids[0]
2. [ ] 修改 3a+3b：content.js 头像跳过回复预览 + 正文按精确 id 取
3. [ ] 修改 4：gateway 作者名优先 member.nick（一行）
4. [ ] 回归：content_test.mjs 加回复场景（3 条）、e2e_wb.py 第 3 节加盯人试算（2 条）
5. [ ] 全套回归 498/498 全绿，RUN.md/HANDOFF.md 收尾，save 推送，更新本页

### 根因（已坐实，第二个窗口查明的）
- **A · 工作台 test_rule 工具没有 author_id 参数**（server.py `run_wb_tool` 里 ev 硬编码
  `author_id=""`，工具 schema 也没有这个参数；guild/channel 都有「默认取规则第一个值」，
  唯独 author_id 漏了）。模型建完盯人规则 → 按纪律试算 → 必中「发的人不在名单里」→
  模型误以为条件坏了，把 author 过滤删掉 → 全频道都命中（症状①）。
- **B · content.js 取 author_id 拿的是子树里第一个 /avatars/ 头像**。回复消息时，
  回复预览里是**被回复人**的头像、排在真作者前面 → author_id 记成被回复人。
  盯张三 = 所有「回复张三」的消息都中，通知显示的是实际发言人（症状①+②）。
- **C · gateway 模式 author 用 global_name，没优先 member.nick**（服务器昵称），
  和 Discord 界面显示不一致（token 模式下的症状②）。顺带修。
- 修法：A 给 test_rule 加 author_id 参数并默认取规则 author_ids[0]；B 限定在
  contents 容器里找头像且排除回复预览；C 一行。回归：e2e_wb 加节、content_test 加回复场景。

BACKLOG 其余任务（A1/A3/A4/A5、B、C）不动，等后续窗口按优先级来。

### 本窗口新发现（写方案时读代码看出来的，已并入 PLAN）
- content.js 的 `content` 取值也有同款病：`[id^="message-content-"]` 前缀匹配会先撞上
  回复预览里的原文（其 id 是 message-content-<被回复消息id>）→ PLAN 修改 3b 按精确 id 取。

## 最后更新
2026-07-31 · 双模型流程启用；A2 施工图 PLAN_A2.md 写完推送。下一窗口照 PLAN 施工（用户会换 Kimi K2）

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
