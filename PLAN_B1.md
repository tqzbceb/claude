# PLAN_B1 —— 监听规则接 AI 判定（「脚本筛不住，得让模型上」）

> 施工图。实现窗口照这份干，**别自由发挥**；「不许做的事」在最后一节，写死了。
> 前置：B3（提示词可改）/ B4（模型参数）/ B2（后处理严格模式）的服务端已在 `386a8bb` 落地，
> 本 PLAN 直接用它们的机制（新提示词进 `DEFAULT_PROMPTS`，参数走 `ai.params`）。

## 0. 为什么要做（用户原话拆出来的三个场景）

用户盯的是白嫖 API key 的社群，发 key 的人会**主动反侦察**，正则必然筛不住：

| # | 反侦察手段 | 正则为什么失效 | 要模型做什么 |
|---|---|---|---|
| ① | key 中间插字符：`sk-ab*c1**23` / `sk-ab（去掉这个）c123` | 关键词和正则都对不上 | 还原成真 key 并交出来 |
| ② | key 后面加杂物，**下一条消息**说「删掉后面 xxx 才是真的」 | 单条消息看不出，要跨消息 | 带上下文理解，交出净化后的 key |
| ③ | 用类脑社区的 `/下载` 命令发一个 txt，key 在附件里 | 正文里根本没有 key | **承认自己看不到**，让程序提醒用户人工去看 |

③ 是这一条里最容易做错的地方：模型看不到附件时会**编一个 key** 或者干脆说「没有 key」。
所以提示词里必须给它一条体面的退路：`need_human=true`。

## 1. 总体设计：AI 复核是一道「宽进严出」的闸

现在的流程：`消息 → match()（条件全中才算命中）→ 动作 + 通知`
加一层之后：`消息 → match()（条件写宽）→ AI 复核 → 通过才通知（并可能附上模型提取的 key）`

三条设计铁律，实现时别改：

1. **复核只减不增**：`match()` 没中的消息，永远不会因为 AI 复核而被通知。
   （否则每条消息都要烧一次模型，额度分分钟见底。）
2. **模型挂了要 fail open**：调用失败 / 超时 / 返回读不懂 → **照旧通知**，
   并在通知正文头上标 `[AI 复核失败]`。宁可吵一次，不能吞掉。
   （硬规矩 1 的同一条道理：程序绝不能悄悄假装一切正常。）
3. **被压掉的消息必须留痕**：复核判「不是」而没通知的，要写日志、要能在诊断包里数出来。
   静默丢消息是这个程序最不该有的行为 —— 用户根本没法自己发现。

## 2. 数据结构

### 2.1 规则新增字段（进 `DEFAULT_RULE`）

```python
"ai_check": False,          # 开关：这条规则命中后，交模型复核一遍
"ai_check_prompt": "",      # 留空＝用 DEFAULT_PROMPTS["ai_check"]
"ai_check_min": 60,         # 模型给的 confidence 低于这个不通知（0 = 只要 hit 就通知）
"ai_check_ctx": 3,          # 连同这条消息之前同频道的几条一起给模型看（场景②靠它）
"ai_check_human": True,     # 模型说「我看不到内容」时也通知，正文标「需要你人工看」
```

- 硬规矩 5：**这五个字段全部要进 `/diagnose.txt` 的 `[4]` 段**，且 `[4.5]` 试算段要说明
  「这条规则开了 AI 复核，下面的试算只算脚本条件那一半」。
- `sanitize_draft` / `sanitize_import_rule` / `rule_for_export` / `diff_rule` 都要认这五个字段
  （照 `min_len` 那几个抄，`ai_check_ctx` 夹 0–12，`ai_check_min` 夹 0–100）。
- 导出/导入：这五个字段跟着规则包走，没有任何本机特有的东西，不需要打码。

### 2.2 新提示词 `DEFAULT_PROMPTS["ai_check"]`

进 `DEFAULT_PROMPTS`（B3 之后它自动就是「界面可见可改可导出」的），并进 `PROMPT_META`
（`name="AI 复核（规则的第二道闸)"`, `skeleton=False`）。内容要求：

- 说明它在一个 Discord 监听程序里，用户盯的是 API key / 邀请码 / 名额 / 新资源；
- **只输出一个 JSON 对象**，不要 ```、不要解释（B2 那套格式标尺照抄 `ai_tag` 的写法）：

```json
{"hit": true, "confidence": 0-100, "kind": "key|invite|quota|resource|other|unreadable",
 "extracted": ["还原出来的真 key，可以多个，没有就空数组"],
 "need_human": false, "reason": "一句话，30 字内"}
```

- **三个 few-shot 例子，正好对上上面那三个场景**：
  - 例 1（插字符）：输入 `新号池 sk-ab*c1**23 手慢无` → `hit:true, confidence:90,
    extracted:["sk-abc123"], reason:"key 中间插了星号，已还原"`
  - 例 2（跨消息）：输入里带上一条 `sk-abc123ZZZZ` + 下一条 `上面那个删掉最后四位才是真的`
    → `extracted:["sk-abc123"], reason:"按下一条的说明去掉了尾部"`
  - 例 3（附件）：输入 `/下载 key.txt` 或 `[附件 key.txt]`
    → `hit:true, confidence:50, kind:"unreadable", extracted:[], need_human:true,
      reason:"key 在附件里，我看不到"`
- 结尾三句硬话：**看不到就说看不到，绝对不许编 key**；不确定给中间分；
  `extracted` 里只放你真的在文本里看见的字符，不许补全、不许猜。

### 2.3 复核记录（新表，诊断和「为什么没提醒我」都要用）

```sql
CREATE TABLE IF NOT EXISTS aicheck(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, rule TEXT, msg_id TEXT,
  hit INT, conf INT, kind TEXT, human INT, passed INT, extracted TEXT, reason TEXT, err TEXT);
```

`passed` = 最终有没有放行通知。保留期跟 messages 一样走 `retention_days` 清理
（照现有清理逻辑加一行，别新写一套）。

## 3. 代码怎么落（文件 / 函数级）

### 3.1 `server.py`

1. **`DEFAULT_RULE`** 加 2.1 那五个字段；`norm_rule()`（或现有的洗规则那处）夹取值范围。
2. **`SCHEMA`** 加 2.2 的 `aicheck` 表。
3. **`UNREADABLE_HINTS`** 常量：`("[附件", "[图片", "[贴纸", "/下载", ".txt", ".json", ".zip")`。
   命中它就**跳过模型直接判 `need_human`**（省一次调用；模型对着 `[附件 x.txt]`
   只会瞎猜）。这条要写注释说明为什么不走模型。
4. **`async def act_check(self, rule, ev) -> dict`**（放在 `act_tag` 旁边，同一套写法）：
   - 先查 `UNREADABLE_HINTS` → 直接返回
     `{"hit": True, "confidence": 50, "kind": "unreadable", "need_human": True,
       "extracted": [], "reason": "内容在附件/图片里，程序读不到", "by": "rule"}`；
   - 否则取上下文：`self.db.q("SELECT author,content FROM messages WHERE channel_id=? AND ts<=? "
     "ORDER BY ts DESC LIMIT ?", (ev["channel_id"], ev["ts"], rule["ai_check_ctx"] + 1))[::-1]`
     —— **注意 `ts<=` 且要把这条本身排在最后**，场景②要的是「这条 + 它前面几条」；
   - 调 `self.chat(prov, model, [...], json_mode=True, max_tokens=400, rule=rule["name"])`，
     system 用 `rule["ai_check_prompt"] or self.prompt("ai_check")`；
   - 解析照 `act_tag` 的 `re.search(r"\{.*\}", out, re.S)` 那套；解析不出来
     → 返回 `{"err": ...}`（上层 fail open）。
5. **接进规则处理流程**（在跑动作和发通知**之前**，`handle_event` 里 `match()` 成立那一支）：

```python
chk = None
if rule.get("ai_check"):
    try:
        chk = await self.act_check(rule, ev)
    except Exception as e:
        chk = {"err": str(e)}
    passed, why = self.check_verdict(rule, chk)     # 见下
    self.db.x("INSERT INTO aicheck(...) VALUES(...)", (...))
    if not passed:
        self.log("info", f"{rule['name']}: AI 复核判「不是」，没提醒 —— {why}"
                         f"（消息 {ev['msg_id']}，原文前 40 字：{ev['content'][:40]}）")
        return          # 这条规则到此为止，别往下走通知
```

6. **`def check_verdict(self, rule, chk) -> (bool, str)`**（纯函数，好测）：
   - `chk.get("err")` → `(True, "复核失败，按 fail open 放行")`
   - `chk["need_human"] and rule["ai_check_human"]` → `(True, "需要人工看")`
   - `chk["hit"] and chk["confidence"] >= rule["ai_check_min"]` → `(True, "")`
   - 其余 → `(False, f"hit={chk['hit']} 置信 {chk['confidence']} < {rule['ai_check_min']}")`
7. **通知正文要带上复核结果**（`fmt_msg` 那一层，别在 hooks 里各写一遍）：
   - `need_human` → 标题前缀 `【需要你人工看】`，正文补一行
     `模型说：key 在附件里，我看不到 —— 你自己去看一眼`；
   - `extracted` 非空 → 正文补一行 `模型还原出来的：sk-abc123`（多个用空格分开）；
     **同时塞进 `{{text}}` / `{{body}}` 占位符**，转发出去的消息里也要有；
   - `err` → 前缀 `[AI 复核失败]` + 原因前 60 字。
   - 新增占位符 `{{extracted}}` / `{{need_human}}` 进 `HOOK_VARS`（硬规矩 5 的同一条道理：
     加了字段就得让用户用得上、看得见）。
8. **诊断包**：
   - `[4]` 段每条规则多印一行：`AI 复核 [开] 门槛 60 | 上下文 3 条 | 附件也提醒`；
   - 新增 `[4.6] AI 复核最近战果`：最近 20 条 `aicheck`，印 `时间 | 规则 | 放行/压掉 |
     置信 | kind | 提取到几个 | 原因`，末尾一行汇总
     「最近 24 小时复核 N 次，放行 X，压掉 Y，失败 Z（失败一律按放行处理）」。
   - `[4.5]` 试算段：开了复核的规则加一句
     「这条还有 AI 复核，下面只算脚本条件那一半 —— 脚本这半都不中的话，模型根本不会被叫起来」。

### 3.2 `ui.html`（规则编辑页）

- 在「动作」那一块下面加一个折叠区 **「AI 复核（让模型再看一眼）」**，五个控件对 2.1 五个字段；
- 折叠区顶部一段人话（**照抄，别自己编**）：
  > 脚本只会认死字：`sk-` 中间被插了星号、key 后面加了一坨杂物、或者 key 干脆在附件里，
  > 脚本都筛不出来。开了这个之后：条件先粗筛，命中的再交模型看一眼，
  > 模型能把真 key 还原出来写在通知里；它要是说「我看不到」（比如 key 在附件里），
  > 就给你发一条「需要你人工看」。**模型调用失败时照旧提醒你**，不会把消息吞掉。
- 门槛滑杆旁边实时印一句：`60 = 模型有六成以上把握才提醒你`；
- 开了复核的规则，在规则列表那一行加一个 `AI 复核` 小标签（照「导入」那个标签抄）；
- 演示模式下这一块照常能点开、能填，只是存不进去（`S.live` 那套现成逻辑）。

### 3.3 `tests/e2e_chk.py`（新套，目标 ≥ 40 条）

用 `tests/mockllm.py` 造模型回答（**别用 `mock-1`**，`e2e_wb.py` 那条坑：
`App.no_tools` 是进程内内存；这套自己用 `mock-chk`）。必须钉住的断言：

1. 关着复核时，行为跟以前**一模一样**（回归保护，先写这条）；
2. 开了复核 + 模型回 `hit:true, confidence:90` → 通知发了，`aicheck` 有一行 `passed=1`；
3. 模型回 `hit:false` → **没通知**，但日志有一条 `AI 复核判「不是」`，`aicheck` 有 `passed=0`；
4. `confidence` 正好等于 `ai_check_min` → 放行（边界，别写成 `>`）；
5. 模型回 500 / 超时 / 返回一段散文 → **照旧通知**，正文里有 `[AI 复核失败]`；
6. 正文含 `[附件 key.txt]` / `/下载` → **一次模型都没调**（`aiusage` 不涨），
   直接走「需要你人工看」，标题带 `【需要你人工看】`；
7. `ai_check_human=False` 时同样的输入 → 不通知；
8. `extracted` 里的 key 出现在通知正文里、也出现在 hook 的 `{{text}}` 里；
9. `ai_check_ctx=3` 时喂给模型的 user 内容里**真的有前面那几条**
   （靠 mockllm 回显收到的 prompt 断言，`e2e_wiz.py` 的 `msys()` 那套写法抄过来）；
10. `match()` 没中的消息，开着复核也**不会**调模型（第 1 条铁律）；
11. 规则包导出/导入带得走这五个字段（照 `e2e_imp.py` 第 13 节抄）；
12. 诊断包 `[4.6]` 段印出了最近战果，`[4]` 段印出了五个字段；
13. 每日调用上限已满时，复核**不放行也不阻断** → 走 fail open 并在日志说明原因。

### 3.4 文档

- `README.md`：新增一节「AI 复核：脚本筛不住的时候」，含那三个场景的例子和「模型挂了会怎样」；
  故障对照表加两行（「开了复核就收不到消息了」→ 看诊断 `[4.6]`；「通知里没有还原的 key」→
  模型没提取到，看 `reason`）。
- `tests/RUN.md`：加 `e2e_chk.py` 覆盖清单 + 两个坑（mockllm 不要用 `mock-1`；
  `ts<=` 那个上下文取法容易把当前这条漏掉）。
- `BACKLOG.md` 把 B1 打勾。

## 4. 验收命令（实现窗口自己跑，全绿才算完）

```bash
cd tests && python3 kill.py server.py
./runall.sh e2e_chk.py            # 新套，≥40 条全绿
./runall.sh e2e.py e2e_ai.py      # 48 / 27，一条都不许挂
./runall.sh e2e_imp.py e2e_diag.py  # 74 / 47（新字段进了包和诊断）
./runall.sh e2e_wb.py e2e_chat.py   # 99 / 42
```

浏览器目检两件事（演示模式即可）：折叠区能展开、五个控件能填；规则列表出现 `AI 复核` 标签。

## 5. 不许做的事（写死，违反就返工）

1. **不许动 `extension/`**。附件识别在服务端靠 `UNREADABLE_HINTS` 完成，
   动了扩展就得 bump `manifest` + `EXT_MIN` 并逼用户重装（硬规矩 2），这一轮不值得。
2. **不许让 AI 复核「扩大」命中面**。`match()` 不中的消息一律不进模型（省钱 + 可预测）。
3. **不许在模型失败时静默丢消息**（fail open 是这条功能的底线）。
4. **不许给模型写库的权限**。复核只读、只回 JSON，一个 `WB_TOOLS` 都不给它
   （硬规矩 10 的同一条道理）。
5. **不许把 `ai_check_prompt` 做成「只能在 server.py 里改」**。它是规则自己的提示词，
   界面上就得能改；空着走 `DEFAULT_PROMPTS["ai_check"]`。
6. **不许 bump 版本号**（`VERSION` / `EXT_MIN` / manifest 三处都别碰）—— 发版是单独一轮的事。
7. **不许顺手重构 `handle_event`**。只在 `match()` 成立那一支插入 3.1-5 那段。
