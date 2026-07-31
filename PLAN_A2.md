# PLAN_A2 —— 修「检测某个用户」失效（施工图，照抄即可）

> 本文件是双模型流程的**实现方案**：Claude 已把根因查明、修法定死、验收写死。
> 实现者（Kimi K2 或任何模型）**只做本文件列出的改动**，不重构、不顺手改别处、不改格式。
> 每完成一个修改就 `python3 .bcode/agent-workspace/save.py "A2: 修改N 完成"` 存盘推送。
> 干活在 `/tmp/dcw`（`git clone https://github.com/tqzbceb/claude.git /tmp/dcw`），不在 `./claude/` 里改。

## 背景（不用再查，已坐实）
用户建「盯着某人」的规则（author_ids）后全频道都命中、通知里显示的却是别人。三个独立 bug：
- **A** 工作台 `test_rule` 工具的假消息硬编码 `author_id=""`，schema 也没这个参数 → 盯人规则试算必中
  「发的人不在名单里」→ 模型误以为条件坏了，把 author 过滤删掉。
- **B** 扩展 `content.js` 取 author_id 拿的是 li 子树里**第一个** `/avatars/` 头像。回复消息时回复预览
  （repliedMessage 容器）里被回复人的头像排在真作者前面 → author_id 记成被回复人。
  同理 `content` 取的是第一个 `[id^="message-content-"]`，回复预览里被回复的原文也用这种 id → 正文也可能拿错。
- **C** gateway（token 模式）作者名用 `global_name`，没优先服务器昵称 `member.nick`，和 Discord 界面显示不一致。

---

## 修改 1 · server.py：test_rule 的 schema 加 author_id 参数

位置：`server.py` 约 949–956 行，`"name": "test_rule"` 的 parameters.properties 里。
现状（953 行）：
```python
            "author": {"type": "string"}, "is_bot": {"type": "boolean"},
```
改成：
```python
            "author": {"type": "string"},
            "author_id": {"type": "string", "description": "发消息人的用户 ID；试算盯人规则时填，留空=用规则「只听这些人」的第一个"},
            "is_bot": {"type": "boolean"},
```

## 修改 2 · server.py：run_wb_tool 里 test_rule 的假消息不再硬编码空 author_id

位置：`server.py` 约 1123 行（`if name == "test_rule":` 块内）。
现状：
```python
              "author_id": "", "author": args.get("author") or "someone",
```
改成（跟同一段里 guild_id / channel_id「默认取规则第一个值」完全同款）：
```python
              "author_id": str(args.get("author_id") or (r["author_ids"] or [""])[0]),
              "author": args.get("author") or "someone",
```

## 修改 3 · extension/content.js：parseLi 别被回复预览骗走

### 3a · author_id 跳过回复预览里的头像
位置：`content.js` 约 136 行（parseLi 的作者回溯 while 循环内）。
现状：
```js
      const img = node.querySelector('img[src*="/avatars/"]');
```
改成：
```js
      const img = [...node.querySelectorAll('img[src*="/avatars/"]')]
        .find(i => !i.closest('[class*="repliedMessage"]'));
```
下一行 `const am = img && ...` 不动。
注意：53 行和 68 行附近 myAccount() 里还有一处 `/avatars/` 查询，那是左下角账号区，**不许动**。

### 3b · content 按精确 id 取，不吃回复预览的原文
位置：`content.js` 约 124 行。
现状：
```js
  let content = txt(li.querySelector('[id^="message-content-"]'));
```
改成（msg_id 此时已从 li.id 抠出来了，纯数字，不用转义）：
```js
  let content = txt(li.querySelector(`#message-content-${msg_id}`));
```
真 Discord 的回复预览里那段原文的 id 是 `message-content-<被回复消息id>`，前缀匹配会先撞上它；
精确 id 只认本条消息的正文。取不到时走原有 mediaOf 兜底，这个逻辑不动。

## 修改 4 · server.py：gateway 作者名优先服务器昵称

位置：`server.py` 约 2502 行（gateway on_message 构造 ev 处）。
现状：
```python
            "author": au.get("global_name") or au.get("username", ""),
```
改成：
```python
            "author": (d.get("member") or {}).get("nick") or au.get("global_name") or au.get("username", ""),
```
本项无自动化回归（gateway 无假服务），改完人眼比对这一行即可。

---

## 回归 1 · tests/content_test.mjs：加「回复消息」场景

### 加模板
位置：`MK` 对象里 `defaultAvatar` 之后加一项。回复预览的关键特征照真 Discord：
容器 class 含 `repliedMessage`，里面有被回复人的小头像和 `message-content-<被回复消息id>`：
```js
  /* 回复消息：预览里是被回复人的头像和原文，都排在真作者之前 —— 不许被骗走 */
  reply: (chan, mid, name, avatarId, refMid, refName, refAvatarId, refText, text) => `
    <li id="chat-messages-${chan}-${mid}">
      <div class="repliedMessage-1">
        <img src="https://cdn.discordapp.com/avatars/${refAvatarId}/r.png">
        <span class="username-2">${refName}</span>
        <div id="message-content-${refMid}">${refText}</div>
      </div>
      <img src="https://cdn.discordapp.com/avatars/${avatarId}/x.png">
      <h3 id="message-username-${mid}"><span class="username-1">${name}</span></h3>
      <div id="message-content-${mid}">${text}</div>
    </li>`,
```

### 塞消息
位置：`run()` 里 `MK.defaultAvatar(CHAN, "1005", ...)` 那行 push 之后加：
```js
  await push(MK.reply(CHAN, "1008", "Replier", "444100000000002",
    "1001", "Marcus", "444100000000001", "@我 明天提前到 10 点", "回复：收到"));
```
（msg_id 1008 未被占用；已占用的有 1001–1007、1010–1013、9999。）

### 加断言
位置：现有 `t("普通消息作者正确", ...)` 那一片断言区，挨着加三条：
```js
  t("回复消息作者是发言人不是被回复人", by["1008"] && by["1008"].author === "Replier", by["1008"]);
  t("回复消息 author_id 不取回复预览头像", by["1008"] && by["1008"].author_id === "444100000000002", by["1008"]);
  t("回复消息正文不取回复预览原文", by["1008"] && by["1008"].content === "回复：收到", by["1008"]);
```

## 回归 2 · tests/e2e_wb.py：第 3 节加盯人试算

位置：第 3 节里「试算说了人话」两条 ok() 之后（约 128 行后）、set_rule_enabled 那段 reset() 之前。
第 3 节还在 mock-1 支持函数调用的阶段，`calls()[-1]["tool_out"]` 拿得到工具结果。加：
```python
aid = mk({"name": "盯着张三", "author_ids": ["444100000000001"], "action": "notify"})
reset()
script([{"tools": [{"name": "test_rule", "args": {"id": aid, "content": "随便说点什么"}}]},
        {"content": "会命中。"}])
ask("盯人这条现在会命中吗")
out = json.loads(calls()[-1]["tool_out"])
ok("盯人试算默认用规则里第一个人，不再必挂 author", out.get("match") is True, out)
reset()
script([{"tools": [{"name": "test_rule", "args": {"id": aid, "content": "x",
                                                  "author_id": "555000000000000001"}}]},
        {"content": "不会命中。"}])
ask("换个人发呢")
out = json.loads(calls()[-1]["tool_out"])
ok("指定别人就不命中，且 why 说的是人的问题", out.get("match") is False and "名单" in str(out.get("why")), out)
call(f"/api/rules/{aid}", None, method="DELETE")
```
文件顶部若没 `import json` 就补上（先看，别重复导）。
（`why` 走 NICE_WHY，"author" → 「发的人不在『听谁』的用户 ID 名单里」，断「名单」二字稳。）

---

## 验收（全绿才算完）

1. 服务端全套回归（在 `/tmp/dcw/tests`，按 RUN.md 分批，命令超时 120s 一批两三套）：
   ```bash
   ./runall.sh e2e.py e2e_ai.py
   ./runall.sh e2e_multi.py e2e_wiz.py
   ./runall.sh e2e_v17.py e2e_diag.py
   ./runall.sh e2e_wb.py e2e_imp.py     # e2e_wb 应从 94 变 96
   ./runall.sh e2e_ext.py
   ```
2. content.js 回归（browser_execute 里，见 content_test.mjs 头部注释）：
   ```js
   const m = await import("/tmp/dcw/tests/content_test.mjs?t=" + Date.now())
   console.log(JSON.stringify(await m.run(session), null, 1))
   ```
   原有条数 + 新 3 条全部 pass；再跑一遍 `m.runFresh(session)` 不能挂。
3. 文档收尾：
   - `tests/RUN.md`：e2e_wb 94 → 96，总数 496 → 498；content_test 条数同步 +3
   - `NOW.md`：状态改「做完了 A2，下一件是 A3+A5」并注明四处修改点
   - `HANDOFF.md`：加一小节（照现有格式）
   - 版本号和 zip **不动**（发布轮再说，不属于 A2）
4. 每步 save.py 推送；最后 `grep -rl github_pat_ /tmp/dcw/.git/` 必须无输出。

## 明确不许做的事
- 不改 match()（1960 行的 author 判断没病）
- 不动 myAccount() 的头像查询（content.js 53/68 行附近）
- 不加新配置项、不改 UI、不升版本号、不打 zip
- 不改任何现有测试的断言，只按上文**新增**
