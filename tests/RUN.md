# dcwatch 回归

这个目录就在项目根下面（`<项目>/tests/`），`runall.sh` 自己会找到 `../server.py`，
不需要配任何路径。布局不一样时用 `DC=/path/to/dcwatch ./runall.sh ...`。

改完 server.py，**858 条全跑一遍**（14 件；F1 之后新增 e2e_name 82 条、e2e_wb 涨到 192）（一次两三套，命令超时一般 120s）：

```bash
pip install aiohttp        # 唯一依赖；某些一次性环境每次都要重装
cd tests
./runall.sh e2e.py e2e_ai.py                     # 48 + 27
./runall.sh e2e_multi.py e2e_wiz.py              # 26 + 83（wiz 单套约 40s，别再多塞）
./runall.sh e2e_v17.py e2e_diag.py                # 46 + 47
./runall.sh e2e_wb.py e2e_imp.py                 # 192 + 74
./runall.sh e2e_ext.py e2e_chat.py               # 53 + 42
./runall.sh e2e_tabs.py e2e_chk.py               # 53 + 56
./runall.sh e2e_watch.py e2e_name.py             # 26（D3 开服监听）+ 82（F1 名字→ID 名录）
```

`runall.sh` 做的事：起假 OpenAI（`mockllm.py` :8899）和假 webhook（`echo.py` :8898 → /tmp/bcode/echo.jsonl），
然后每套之间 **清 /tmp/p.db\* 并重启 server**（库不干净的话去重守卫会静默丢重复 msg_id，
表现成「命中数没加」「转发了两次」一片假红）。手动起服务的等价命令：

```bash
DCWATCH_DB=/tmp/p.db python3 server.py     # 别用真配置库
```

改完 `extension/content.js`：跑 `content_test.mjs` 的 `run()`（49 条）和 `runFresh()`（16 条）；改了 `background.js` 自动开帖再跑 `runTabs()`（42 条 chrome.tabs/windows 桩，含 C6 防回收 8 条 + D2 新开最小化窗口 7 条），
要一个 CDP 会话（`browser_execute` 之类）。它默认读 `../extension/content.js`，
也可以传路径或设 `DCW_CONTENT_JS`。

## content.js 的回归（不需要真装扩展）
```js
// browser_execute 里，已 connect+use 之后：
const m = await import(process.cwd()+"/tests/content_test.mjs?t="+Date.now())
console.log(JSON.stringify(await m.run(session), null, 1))   // 期望 {syntax:"ok", pass:26, fail:0}
```
不传参数就用默认路径。它自己会换 realm、造假 Discord DOM、伪造 /channels/<g>/<c>、注入 chrome 桩、等过 4s 静默期。
覆盖：折叠消息继承作者 / 头像抠 author_id / 默认头像留空 / 纯图片跳过 / bot 标记 /
子区认父频道 + 侧栏标题 / 私信 / 去重 / 合批 / 心跳带账号和频道 / 批次带 account+account_id / 页面药丸挂载。
**换路径会重新触发 4 秒静默期**（切频道不重报历史，设计如此）——测试里换完 URL 必须再等 4.6s，
否则新塞的消息会被当历史丢掉，看着像 is_dm 解析坏了。

## e2e_multi.py 覆盖（多浏览器 / 多账号 / 跨桥去重）
心跳带身份被单独列成桥（account/browser/ver/where/fresh）/ 两个桥不互相覆盖 / 旧版扩展版本可见 /
同一条消息两个桥各报一次只入库一条且命中只 +1 / 规则 accounts 分流 / 单条 account 优先于整批 /
旁听开关关掉时桥仍可见且带原因。
- 桥是**进程内内存**，跑过 e2e.py 会留下一个 anon 桥，所以别断言 `live == 2` 这种绝对值。
- 浏览器来源的 msg_id 入库时会加 `b` 前缀（和 Token 直连的 id 区分），断言要用 `"b"+mid`。
- 建规则是 `call("/api/rules", dict(rule, enabled=1))`，**不是** `{"rule": ...}`。
两套都用 :8777/:8899/:8898，但会互相清库清规则，**别同时跑**，一套跑完再跑另一套（各自换一个 DCWATCH_DB）。

覆盖：state / config 掩码不冲真值 / sinks 局部更新 / 拉模型 / ingest 入库 /
compose 清洗（编 ID 丢弃+notes、非法 action 退回、非法字段丢、字符串转 int）/
规则 CRUD / 试算三态 / 父频道→子区 / 提示音列表 / 出口 webhook+企业微信 + 未配置报错 /
命中计数 / 命中后转发内容 / 工作台快捷按钮 /
**保存 sinks 后 SSE 广播**（A5 回归：/api/config 存 sinks → /api/events 推到且是最新值）。

`e2e_ai.py` 覆盖 AI 动作路径：ai_tag（分数+标签+待办+matched，且只调一次模型）/
min_score 门槛拦低分 / ai_extract（模型输出带废话也能抽出 JSON、走 json_mode）/
ai_summary（攒够 N 条才调一次、调完清缓冲）/ ai_reply 与手动回复在 browser 模式必须明确报错 /
`/api/ask` 真把上下文喂给模型 / 每日调用上限拦住并说清 / cooldown 内不重复调模型且写日志 /
webhook 动作三态（规则自带地址能发、地址留空写告警、对方 500 记 error 且不影响入库）。
假模型按 system prompt 分任务返回，`/__calls` 看调了什么、`/__reset` 清零；
打分规则：内容里有 `@` → 88 分，否则 20 分（别用关键词做判据，会跟规则关键词撞）。

## 踩过的坑，别再踩
- 别在 bash 里写 `case "$c" in *server.py*)` 去杀进程：**pattern 出现在自己的 cmdline 里，会把自己杀掉**，
  bash 工具随后一直挂到超时。用 `kill.py <关键词>`（跳过自己和父 shell）。
- `kill.py` 按 cmdline 匹配，而 `python3 server.py` 的 cmdline 里没有目录，
  想精确杀就用 `python3 kill.py server.py` 并确认只有一个在跑。
- bash 工具超时会把 setsid 起的后台进程一起带走；长任务改成「后台起 + sleep + 读日志」。
- 期望值容易写错的三处（都不是 bug）：`/api/state` 里 webhook/api_key 是 `***` 掩码形式；
  出口转发跟规则 action 无关（notify 也会转，只受 sinks.min_score 管，无分数一律发）；
  转发 body 是双层 JSON，断言前要 `json.loads` 两次。
- `call("/api/messages/clear")` 是 POST，测试里 data 传 `{}` 才不会 405。
- echo.jsonl 每行是 `{"path","body"}`，body 是**字符串**：断言要 `json.loads(json.loads(line)["body"])`，
  在原始行里搜 `"score": "88"` 永远搜不到（引号被转义成 `\"`）。
- 日志记录的字段名是 `text`（不是 msg）：`/api/logs` → `{"logs":[{"ts","level","text"}]}`。
- compose 的 author_ids 取决于「本机最近说话的人」的排序，测试别硬编码某个人的 ID。

## e2e_wiz.py 覆盖（引导式建规则 /api/rules/wizard，33 条）
空对话挡住 / 第一轮必须反问不许直接出规则 / 复述+假设+why+推荐值都在 / 问题最多 3 个 /
够了就 done / 模糊意图必须走 ai_tag+粗筛词+阈值 / 字符串数字转 int / 空投类 ignore_bots=False /
编造 ID 丢弃并写 notes / 真 ID 保留 / 非法字段丢 / catches+misses+verify 都有 /
出来的规则能直接保存且试算能命中 / **弱模型不给 JSON 时降级成追问（loose=true）不许崩** /
```json 包裹能解析 / 超过 3 轮强制收尾 / 没传模型用默认模型、一个都没配才报错 /
providers 形状不对回 400 不是 500。
- mockllm 的 wizard 分支**自己数 assistant 轮数**决定 ask/done；user 里带 `GARBAGE` 出非 JSON、
  带 `FENCE` 套 code fence，专门测容错。
- `/api/rules/test` 的载荷键是 **`sample`**（不是 `message`），写成 message 会全字段空 → why='author'。
- `b.get("model") or cfg.default_model` 是有意的回退，测「没模型报错」必须先把 default_model 清空。

## content_test.mjs 的抖动（2026-07-30 修）
症状：runFresh 每次挂的项都不一样，got 是 `[]`。根因**不是 content.js**，是
①标签页在后台时定时器被节流，600ms 合批会迟到；②断言一次性读 `__sent`，读太早。
修法：`Page.bringToFront()` + 正向断言全走 `waitSent(id, 4000)` 轮询。
连跑 3 次 runFresh 10/10、run 26/26 稳定。**以后加「该上报」的断言一律用 waitSent，别单次读。**
手动复现时诊断区（`__dcwatch.stats`）能直接看出跳过原因，比猜快得多。

## 两个会让你误判成产品 bug 的坑（2026-07-31 各踩一次）
1. **echo.py 原来 `body[:600]`**：给事件加了 `kind`/`scanned` 两个字段后转发 body 超过 600，
   被截成非法 JSON，e2e.py 第 11 节直接 traceback。已改成 `[:8000]`。
   看到「转发内容」相关的 JSONDecodeError 先怀疑这里，不是 server 截断的。
2. **库不干净 = 第 11 节必挂**：去重守卫是查库的，上一轮跑过的 msg_id 再来一次会被静默丢掉，
   表现为「规则命中数 +1」「转发了 2 次」全 FAIL。**每套跑之前 `rm -f /tmp/p.db*` 并重启 server**。

## e2e_v17.py（36 条，v1.7.0 新能力）
覆盖：kinds 闸门六种组合 / 新帖入库并记 kind=thread / 抓历史只入库不加命中数不提醒 /
批量提取（空范围提示、verified 逐字核对、only_matched）/ env 自查字段 + 诊断包路径段。
- 它**自己会配 provider**（单独跑时库是空的，否则 /api/batch 一次也调不动模型，
  而且 server 仍会报 calls>=1，看着像成功 —— 所以断言里加了「没有批次失败」）
- `/api/rules/test` 的返回键是 **`match`**，不是 `hit`
- mockllm 加了 `batch` 分流（认 system 里的「批量翻一批」）+ `gen_batch()`：
  从 user 的 `[msg_id]` 行里抠 `ABCD-1234` 形状的串**原样**返回 —— 原样很重要，服务端要核对

## e2e_v17.py 现在 46 条（v1.7.1 加了 /version 与缓存头）
- `call(..., headers=True)` 返回响应头 dict（不读 body），用来断言 `Cache-Control: no-store`
- **断言里不要写死版本号**：原来的「版本是 1.7.0」在 bump 到 1.7.1 时假红了一次。
  现在只校验形状 `\d+\.\d+\.\d+`，再要求 `/version` 页面里出现同一个号
- `/version` 的断言包含「页面里没有 `<script`」—— 这一页故意不依赖 JS，改动时别加脚本

## 2026-07-31 v1.7.4 新增的两条
- `chrome` 桩里补了 `runtime.onMessage.addListener`（存成 `window.__onMsg`）。
  **content.js 里所有 chrome API 调用都包在 try/catch 里**，桩缺一个方法不会报错、只会静默跳过，
  于是新代码看着全绿其实一行没跑。加了 chrome 新用法就要同步补桩。
  第 12 节直接调 `__onMsg({type:"dcwatch-pull"})` 断言它同步 reply 一份心跳 body。
- `e2e_diag.py` 里桥的扩展版本**不再写死**，改成开头 `EXT_OK = /api/state.env.ext_min`。
  写死成 1.7.2 时，EXT_MIN 一提到 1.7.4，「一切正常时不许硬凑问题」那节就假红三条。

## e2e_wb.py 覆盖（AI 工作台的「手」，96 条，v1.8.0 / v1.9.2 / A2）
函数调用改规则 / 不许猜 id / 建停试算删 / 只读工具（状态、频道、搜消息）/ 编的 ID 洗掉 /
A2（2026-07-31）：第 3 节加「盯人试算」—— test_rule 不传 author_id 时默认取规则
author_ids[0]（盯人规则不再必挂「发的人不在名单里」）、显式传别人的 ID 就不命中。
接口不支持 tools 自动退回文本指令 / 「允许模型直接改规则」关掉后两条路都断 / 流式 SSE 与 error 事件 /
plain 模式不带工具 / 多轮上下文 / env 两个开关 / 提示词页能看到「手」那段 /
第 14 节（v1.9.2）`export_rules`：包的形状、不带 id 和 hits、导入戳不跟着走、
**转发地址打码**、ids 只导指定几条、不存在的 id 只跳过、超长提示词截断、条数太多退化成清单、
**没有 import_rules 这个工具**（模型硬调会失败且一条规则都不动）。
- **第 6 节之后 `mock-1` 被记成「不支持函数调用」**（`App.no_tools` 是进程内内存，清不掉）。
  之后要断言「工具结果真喂回给模型了」（mockllm 的 `tool_out` 只收 role=tool）的小节，
  必须先把 `default_model.model` 换成别的名字（第 14 节换成 `mock-2`），否则 `tool_out` 永远是空的。
- 给模型看的包有个 `WB_PACK_LIMIT`（3500 字）：喂回去的工具结果会被服务端截到 6000 字、
  mockllm 又只留 4000 字，包比它大就在测试里解不出 JSON。**改这个常量要一起看这两个上限**。

## e2e_imp.py 覆盖（规则导入 / 导出，74 条，v1.9.0 / v1.9.1）
导出包的形状（schema/version/count，不含 id 和 hits）/ 附件头和 no-store /
原样导回去认成「没变」/ **预览绝不写库** / 覆盖时逐字段列出「变成什么」且字段名是人话 /
replace 才列 removes / 真导入后字符串数字转 int、停用状态跟着文件 /
覆盖保留本机 hits、重复导入不造重复规则 / 脏数据（非法动作、非 ID、未知字段、非法 kinds）洗掉并说清 /
坏 JSON、别人家的 schema、空包、非规则包、非法 mode 一律 400 且带人话原因 / 裸数组能读但提示没 schema 头 /
第 13 节（v1.9.1）导入戳：预览不盖戳、新增和覆盖都盖、记住包的版本、手填的没有戳、
**戳不跟着导出走**、自己导出再导回去不抱怨未知字段、界面编辑不擦掉戳、诊断包 [4] 段印出来路和总结行。
- **导入不校验 ID 存不存在**（跟 `sanitize_draft` 的 known_ids 那套刻意不同）：导出方的频道 ID
  在导入方库里当然查不到，照 known_ids 洗会把规则洗空，用户看到「导进来了但什么都不听」。
  第 7 节就是钉这件事的，别为了「统一」把它改回去。
- 断言 400 的原因时要 `emsg()` 先把 body 解出来：`_body` 是转义过的 JSON，
  直接在原始串里搜中文永远搜不到（跟 echo.jsonl 那个坑同源）。
- `imported_at` / `imported_from` **故意不在 `DEFAULT_RULE` 里**（不然 `rule_for_export` 会把它导出去，
  等于把本机的账发给别人）。所以别改成「加进 DEFAULT_RULE 更统一」，第 13 节会红。
- 第 8 节靠 `/api/ingest` 造一次命中，**ingest 后要 sleep 0.8s** 再读 hits（异步落库）；
  而且样本正文必须比规则的 min_len 长，否则被 `len` 挡下，看着像导入坏了。

## e2e_ext.py 覆盖（批量提取模板，53 条，v1.9.3）
模板存在 cfg 的 `extract_templates` 里（`/api/config` 认它，`norm_tpls` 洗）：
存 / 自动 id / 空名空 want 丢掉 / limit 夹 1–2000 / 不像 ID 的频道清空 /
`/api/extract/export`：`dcwatch.extract/1` 包（version/count，不带 id、不带导入戳）/
`/api/extract/import`：**dry_run 默认真、预览绝不写库** / 1 新增 1 覆盖算得对、人话 diff /
merge 不删本机多的、replace 先列 removes 再问 / 覆盖不换 id（界面不跳位）/
导入戳（`imported_at`/`imported_from`）盖在导入项上、没动过的不盖、**不跟着导出走** /
自己导出再导回认成「没变」且不抱怨未知字段 / 字符串形式的包也能吃 /
本机查不到的纯数字频道 ID 照样留着（洗空才是最难查的 bug）/
坏 JSON、别人家的 schema 一律 400 且带人话原因。
- 跟规则包同样的三条铁律，别为「统一」改掉：① `dry_run` 默认真；② 导入戳不进 `DEFAULT_TPL`
  （进了就会被 `tpl_for_export` 导出去）；③ 频道 ID 只要求纯数字，**不按本机 known_ids 洗**。

## e2e_chat.py 覆盖（工作台聊天持久化 + 多会话，42 条，A1+C1）
两张新表 `wb_sessions` / `wb_msgs`，五个 `/api/wb/*` 接口：
空库 sessions=[] cur=0 / 新建会话 cur 指过去 / ask 不带 sid 自动建会话并把名字顶成第一句话头 20 字 /
open 取回消息（最多 60 条、顺序 u,a）/ 带 sid 追加 / 多会话各自独立 /
rename（空名 400、超长截 40）/ del 连消息一起删、**删当前会话 cur 清零**（测试要先 open 切成当前，
open 的职责就是记 wb_cur）/ plain=1 探针不进库 / 模型调用失败不进库 / open 不存在 404 /
用户输入截 4000。
- 坑：失败注入**不能**靠写错 provider 名 —— `App.provider()` 对未知名字兜底到第一个 provider，
  调用照样成功。要让 mockllm 真回错：`/__script` 排一条 `{"http":{"status":500,"body":"boom"}}`。

## e2e_chk.py 覆盖（B1 AI 复核专项，56 条，PLAN_B1 §3.3）
§1 关着复核行为不变（通知照发 / aicheck 空表 / 模型零调用 / 默认值洗出）/
§2 hit:true 放行：通知 + 「模型还原出来的」拼进正文 + {{extracted}}/{{need_human}} 占位符 +
   aicheck 留痕 passed=1 / 调用带 json_mode /
§3 hit:false 压掉：无通知、passed=0、日志「AI 复核判『不是』」带 msg_id /
§4 门槛边界：conf==min 放行（>=，不是 >）、min-1 压掉 /
§5 fail open 三连：HTTP 500 / 坏 JSON / 回散文 → 三条都照通知、标题 [AI 复核失败]、err 留痕、
   warn 日志「复核没做成，按放行处理」/
§6 反侦察转人工：[附件 xx.txt] / /下载 → **aiusage 不涨、mock 零调用**、
   标题【需要你人工看】、kind=unreadable /
§7 ai_check_human=False → 同样输入不通知、日志写明是「看不到也提醒」关了 /
§8 ai_check_ctx=3：user 里真有「同频道前面几条」+ 上文三条 + 「要判断的这条」排最后
   （靠 mockllm 回显的 user 断言，msys() 同款写法）/
§9 match() 没中不调模型 / §10 导出导入带走五个字段且不抱怨未知字段 /
§11 诊断包 [4] 段印五个字段 + [4.6] 段印放行/压掉战果 /
§12 每日上限：把 cap 压到当前用量 → 模型在调用前就被拦（mock 零调用）、fail open 照通知、
    err 写「已达今日调用上限」、跑完记得把 cap 改回 500。
- 坑 1：**本套模型名一律 "mock-chk"，别用 "mock-1"**。`App.no_tools` 是进程内内存，
  跟 e2e_wb 共用一个模型名的话，谁先把 no_tools 染上，另一套的工作台就莫名退化到文本指令模式。
- 坑 2：复核上下文是 `ts<=当前` 倒序取 n+1 条再翻回来 —— **当前这条也在查询结果里**，
  靠「正文 != 当前正文」滤掉。所以造上文消息时，别让它的正文跟触发消息一字不差，
  否则被误滤、上下文少一条，§8 断言挂得莫名其妙。
- 坑 3：mockllm 的 aicheck 分支走 `/__chk` 队列（`{"json":…}` / `{"raw":…}` /
  `{"http":…}` / `{"bad":true}`），队列空了默认回 hit:true 90 分；
  在 `/api/logs` 里搜中文要 `json.dumps(logs, ensure_ascii=False)` 再搜。

