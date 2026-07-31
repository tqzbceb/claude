# PLAN_C2 —— 新帖到底看不看得见 + 自动开帖 + 闲置了自己关

> 施工图。**这一轮必然要动 `extension/`**，所以硬规矩 2 全套适用：
> `manifest.json` 的 version 和 `server.py` 的 `EXT_MIN` 一起 bump，
> 回复里明确告诉用户「重装扩展三步：覆盖文件 → chrome://extensions 点 ⟳ → Discord 页面 F5，
> 卡片应显示 vX.Y.Z」。**建议单独一轮做，别跟别的功能挤一个版本。**

## 0. 先回答用户的问题（这是这份 PLAN 存在的原因）

> 「如果有新帖子，你不打开新帖子，你用这个浏览器监听真的能看到这个帖子的消息吗？」

**看不到。** 用户问到了点子上。浏览器旁听模式的原理是扩展读 discord.com 网页的 DOM，
Discord 是「点开哪个频道才把哪个频道的消息渲染进 DOM」的：

| 情况 | 扩展能拿到什么 |
|---|---|
| 论坛频道列表页开着 | **只有帖子标题 + 作者 + 有没有新回复**（这就是现在「有人开新帖」的来源） |
| 帖子没点开 | 帖内消息**一条都没有**（DOM 里根本不存在） |
| 帖子点开着（哪怕在后台标签页） | 帖内消息能实时拿到（v1.7.4 之后后台标签页也不迟到） |
| 帖子归档 / 标签页关掉 | 停止，重新点开时靠消息 ID 里的时间戳判新旧，不会把历史当新消息 |

所以「盯论坛里的新帖正文」现在只有两条路：**用户自己把帖子点开挂着**，
或者**程序自己去点开** —— 后者就是这一轮要做的事。

（Bot Token 模式没有这个问题，Gateway 直接推 thread 里的消息。但用户不是服主，拿不到 bot。
这一点要写进 README，别让他以为是程序坏了。）

## 1. 设计：自动开帖是一把「有闸门的」自动化

自动开标签页是个很容易失控的功能：论坛一天开 50 个帖，浏览器就被 50 个标签页撑爆，
Discord 那边看着也像脚本行为。所以**默认关**，开了也必须有四道闸：

| 闸 | 默认 | 作用 |
|---|---|---|
| 总开关 `auto_open` | **False** | 不开就完全是现在的行为 |
| 同时最多开几个 `max_tabs` | 6 | 超了就不再开新的（并在界面/日志里说清「已到上限」） |
| 每小时最多开几个 `per_hour` | 8 | 防论坛刷帖把浏览器打爆 |
| 只开哪些频道 `only_rule_channels` | **True** | 只开「有规则在盯的父频道」下面的新帖，别的一律不开 |

再加一条收尾闸：**闲置多久自己关** `close_idle_min`（默认 30，0 = 不自动关）。
帖子在设定时间内没有新消息 → 程序让扩展把那个标签页关掉，腾位置给新帖。

### 为什么不用 AI 判断该不该开 / 该不该关

用户提到「可以用让 AI 判断某个帖子很久不发言了，可以关掉浏览器标签页了」。
**判断「多久没发言」不需要模型** —— 库里有每条消息的 `ts`，一句 SQL 就出来了，
而且模型每 30 秒判一次是纯烧钱。所以：

- **开 / 关的决策一律用确定性规则**（上面那五个数）。
- **AI 只做一件事，而且是用户主动问的时候**：工作台新增一个只读工具
  `list_open_threads`，让用户能问「现在开着哪些帖子？哪些可以关了？」，
  模型拿着「帖子名 + 最后一条消息多久前 + 命中过几次」念给他听，并**建议**关哪些。
  真正的关闭动作还是用户点，或者交给 `close_idle_min`。
  （硬规矩 10 的同一条道理：会造成后果的操作不交给模型自己按。）

## 2. 协议：服务端怎么指挥扩展

扩展已经有心跳（`background.js` 每 30 秒拉一次）。**别新造通道**，就往心跳的响应里加指令：

`POST /api/ext/hb`（现有的心跳接口）响应体新增：

```json
{"ok": true,
 "open": [{"url": "https://discord.com/channels/<g>/<thread_id>", "why": "新帖：xxx", "tid": "<thread_id>"}],
 "close": [{"tid": "<thread_id>", "why": "闲置 42 分钟"}],
 "limits": {"max_tabs": 6, "open_left_this_hour": 5}}
```

- `open` / `close` 一次最多各给 **3 个**（一次开 20 个标签页看着就是脚本）。
- 扩展执行完**必须回报**：`POST /api/ext/tabs`，带
  `{"opened": ["tid", ...], "closed": ["tid", ...], "tabs": [{"tid": "...", "url": "..."}], "failed": [{"tid": "...", "err": "..."}]}`。
  服务端据此维护「现在到底开着哪些」——**不要靠服务端自己猜**，它猜不准（用户手动关了标签页
  服务端不知道）。这一条是整个功能的准确性来源。
- 幂等：同一个 `tid` 只发一次 `open`，发过就记进 `opened_at`；扩展回报失败才允许再发一次，
  最多重试 2 次（`tries` 字段），再失败就写日志「这个帖子自动打开失败了两次，你手动点一下」。

## 3. 代码怎么落

### 3.1 `server.py`

1. **cfg 新增一块**（进 `DEFAULT_CONFIG`，照 `net` 那样一小块）：

```python
"browser": {"auto_open": False, "max_tabs": 6, "per_hour": 8,
            "only_rule_channels": True, "close_idle_min": 30},
```
洗值函数 `norm_browser()`：`max_tabs` 夹 1–20，`per_hour` 夹 1–60，`close_idle_min` 夹 0–1440。

2. **新表**（要持久化，重启后不能忘了自己开过哪些）：

```sql
CREATE TABLE IF NOT EXISTS threads_open(
  tid TEXT PRIMARY KEY, url TEXT, name TEXT, parent_id TEXT,
  wanted REAL, opened_at REAL, closed_at REAL, tries INT DEFAULT 0, last_msg REAL, err TEXT);
```

3. **谁往 `threads_open` 里排队**：现有的「有人开新帖」那条路径（`kind="thread"` 的事件）。
   条件：`auto_open` 开着 + （`only_rule_channels` 关掉，或这个帖子的 `parent_id`
   出现在任何一条**启用**规则的频道条件里）+ 这个 `tid` 没排过。
   写一行 `wanted=now()`，其余留空。
4. **`def tab_orders(self) -> dict`**（心跳调它，纯逻辑好测）：
   - 现在开着的数量 = `threads_open` 里 `opened_at` 非空且 `closed_at` 空的行数；
   - 本小时已开 = `opened_at > now()-3600` 的行数；
   - `open` 候选 = `wanted` 非空、`opened_at` 空、`tries < 2`，按 `wanted` 升序，
     受 `max_tabs - 现在开着` 和 `per_hour - 本小时已开` 双限，最多 3 条；
   - `close` 候选（`close_idle_min > 0` 时）= 开着的行里
     `last_msg`（取 `messages` 里该频道最后一条的 ts，没有就用 `opened_at`）
     早于 `now() - close_idle_min*60`，最多 3 条；
   - **永远不关「本轮刚开」的**（`opened_at > now()-300`），不然刚开就关来回抖。
5. **`/api/ext/hb`** 响应挂上 `tab_orders()`；**新接口 `/api/ext/tabs`** 收回报，
   更新 `opened_at` / `closed_at` / `tries` / `err`，并 `log("info", ...)` 每一次开和关
   （用户要能在运行日志里看见「程序替我点开了什么」——这是信任问题，不是调试信息）。
6. **工作台只读工具 `list_open_threads`** 进 `WB_TOOLS` + `WB_TEXT_PROTO` 的清单 +
   `READ_HUMAN`（人话："看了一遍现在开着哪些帖子"）。返回每个帖子：名字、父频道、
   开了多久、最后一条消息多久前、命中过几次。**只读，不给它关标签页的工具。**
7. **诊断包新增 `[6] 自动开帖`**：五个配置值 + 现在开着几个（列出来，带闲置时长）+
   最近 10 次开/关记录 + 失败的那几个。`[0] 一眼结论` 加一条：
   「你开了自动开帖，但扩展是旧版（< 新 EXT_MIN），指令根本没人执行」。
8. **README** 新增一节，标题就叫「不点开的帖子看不到帖内消息（以及怎么让程序自己点开）」，
   把第 0 节那张表照搬进去。

### 3.2 `extension/`（必然要动，所以版本号一起 bump）

1. **`manifest.json`**：`version` bump；权限需要 `"tabs"`（创建/关闭标签页）。
   **注意**：`tabs` 权限会让 Chrome 在安装时多显示一条提示，README 的重装说明里要写清楚
   「这次会多要一个『读取浏览记录/标签页』的权限，是用来自动开帖的，不开自动开帖就不会用到」。
   —— 用户看到新权限会害怕，不解释清楚他会不装。
2. **`background.js`**：
   - 心跳响应里有 `open` → `chrome.tabs.create({url, active: false, pinned: false})`；
     `close` → 先按 url 里的 tid 找到那个 tab（`chrome.tabs.query({url: "*://discord.com/*"})`），
     找不到就当已经关了（回报 `closed`），找到了 `chrome.tabs.remove`；
   - **每一步都 try/catch，失败进 `failed` 回报**，别让一个失败的 create 把整个心跳打断；
   - 回报走 `POST /api/ext/tabs`；
   - 本地也存一份 `chrome.storage.local` 的 `opened` 集合，用于扩展重载后不重复开。
3. **`popup.html` / `popup.js`**：自检面板加一行「自动开帖：开/关 · 现在开着 N 个」，
   数据从心跳响应里拿（popup 不要自己再请求一遍）。
4. **页面右下角状态药丸**：自动开的标签页上加一个标记（比如药丸文案后加「· 自动」），
   让用户一眼看出「这个标签页是程序开的，不是我自己点的」。**这条别省**，
   否则他会以为浏览器中毒了。

### 3.3 `ui.html`

「设置」页（旁听模式那一块下面）新增折叠区 **「自动点开新帖（实验性）」**：
五个控件 + 一段人话 + 一个实时状态行「现在开着 N 个帖子（扩展回报的）」。
文案照抄：

> 论坛里的新帖，只要没点开，网页里就没有它的消息 —— 扩展也就读不到。
> 开了这个之后，程序会替你在后台悄悄点开新帖（不抢你的焦点），
> 闲置超过 30 分钟自动关掉。**要装新版扩展才有效**，而且新版会多要一个「标签页」权限。

### 3.4 测试

- `tests/e2e_tabs.py`（新套，≥ 35 条）：`tab_orders()` 的所有闸门（总开关、两个上限、
  只开规则频道、幂等、tries 上限、刚开的不关、闲置判定用最后一条消息的 ts）、
  `/api/ext/tabs` 回报后状态正确、诊断 `[6]` 段、工作台 `list_open_threads` 只读。
- `tests/content_test.mjs`：加一批（≥ 10 条）—— `chrome.tabs` 桩要补
  （`create` / `remove` / `query`），断言心跳带 `open` 时真调了 `create`、
  失败时进 `failed`、`storage.local` 里记了 `opened`。
  **加了新的 chrome API 就得在桩里补**，这是 `tests/RUN.md` 里已经写着的坑。

## 4. 验收

```bash
cd tests && python3 kill.py server.py
./runall.sh e2e_tabs.py                  # 新套全绿
./runall.sh e2e.py e2e_diag.py           # 48 / 47（诊断多了 [6] 段）
./runall.sh e2e_wb.py                    # 99 + 新工具那几条
# 浏览器侧（需要 CDP 会话）：
node -e '...' 或 await m.run(session) / m.runFresh(session)   # 43+16 条重跑，外加新增那批
```

发版前**必须**在回复里写清三处版本号（程序 / `EXT_MIN` / manifest）和重装三步。

## 5. 不许做的事

1. **不许默认打开 `auto_open`**。用户没要求过「自动开一堆标签页」，他要求的是「能不能自己开」。
2. **不许用模型决定开/关**（第 1 节写了理由：SQL 就够，模型是纯烧钱）。
   模型只在用户主动问时念现状 + 给建议。
3. **不许省掉「这个标签页是程序开的」这个视觉标记**。
4. **不许让扩展自己决定开哪些**（服务端才知道规则在盯什么；扩展只执行 + 回报）。
5. **不许一次给超过 3 条 open / close 指令**。
6. **不许在这一轮顺手做 B1**（AI 复核是 `PLAN_B1.md`，两件事都动通知路径，一起改会打架）。
7. **不许不 bump 版本号**：这一轮动了 `extension/`，三处版本号必须一起走（硬规矩 2）。
