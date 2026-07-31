# PLAN_A1C1 —— AI 工作台「聊天持久化 + 多会话」（施工图）

> 给实现窗口（Kimi 等快模型）：**只照本文件改，别自由发挥。** 动 `server.py`、`ui.html`、
> 新增 `tests/e2e_chat.py`，改 `tests/RUN.md`、`README.md`。行号以本 PLAN 提交时（A4 完工后）
> 的版本为准（server.py 3825 行 / ui.html 2661 行）。**不许动 extension/、不许 bump 版本号、
> 不许碰 EXT_MIN。**

## 0. 需求与根因

- **A1**：工作台聊天记录只活在前端内存 `S.chat`（ui.html:792），刷新就没。要持久化到 SQLite。
- **C1**：只有一个聊天，想同时开几个话题做不到。要会话列表，可新建、切换、改名、删除。
两条一起做：持久化落库时按会话分，多会话就是多张记录。

**现状**（实现方不用再查）：
- 后端 `/api/ask` 和 `/api/ask/stream`（server.py:3075-3121）收 `history` 参数（前端拼好
  传上来），**自己一行都不记**。
- 前端 `S.chat` 里每条消息是 `{r:'u'|'a', t, acts?, busy?}`（ui.html:2407-2410）。
- 历史只带**最近 8 条**（server.py:3059-3060 后端截、ui.html:2459 前端拼）。
- DB 是 `db.x(sql,args)` 写、`db.q(sql,args)` 读，schema 在 `SCHEMA` 常量（server.py:343-359），
  表都是 `CREATE TABLE IF NOT EXISTS`，老库启动时自动补。

## 1. 服务端（server.py）

### 1a. 两张新表（SCHEMA 里追加，server.py:357 后面）

```sql
CREATE TABLE IF NOT EXISTS wb_sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT DEFAULT '', created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS wb_msgs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, sid INTEGER, r TEXT, t TEXT, acts TEXT, ts REAL);
```

放法：在 `SCHEMA` 字符串里 `aiusage` 表那条（server.py:357-358）**之后**、`"""` 收尾之前加。
老库不用 ALTER：`CREATE TABLE IF NOT EXISTS` 对两边都安全。

### 1b. 五个接口（加在 `@r.post("/api/ask/stream")` 后面，server.py:3121 之后）

约定：消息 `acts` 在库里是 JSON 文本（空数组存 `''`，读回时 parse 不成就是 `[]`）。

```python
    def wb_sess_list():
        """会话列表 + 每个会话最近一条消息的时间（界面排序、显示用）。"""
        rows = app.db.q("SELECT id,name,created,updated FROM wb_sessions ORDER BY updated DESC")
        last = {r["sid"]: r["n"] for r in app.db.q(
            "SELECT sid,COUNT(*) n FROM wb_msgs GROUP BY sid")}
        for r in rows:
            r["n"] = last.get(r["id"], 0)
        return rows

    @r.get("/api/wb/sessions")
    async def wb_sessions(_):
        cur = app.db.q("SELECT v FROM kv WHERE k='wb_cur'")
        cur = int(cur[0]["v"]) if cur and cur[0]["v"] else 0
        return web.json_response({"ok": True, "sessions": wb_sess_list(), "cur": cur})

    @r.post("/api/wb/session/new")
    async def wb_new(_):
        ts = now()
        sid = app.db.x("INSERT INTO wb_sessions(name,created,updated) VALUES('新会话',?,?)", (ts, ts))
        app.db.x("INSERT INTO kv(k,v) VALUES('wb_cur',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (str(sid),))
        return web.json_response({"ok": True, "id": sid})

    @r.post("/api/wb/session/open")
    async def wb_open(req):
        """打开一个会话：记为当前，并把它的消息捞出来（最多 60 条，防止超长）。"""
        b = await req.json()
        sid = int(b.get("id") or 0)
        if not app.db.q("SELECT id FROM wb_sessions WHERE id=?", (sid,)):
            return web.json_response({"ok": False, "error": "会话不存在"}, status=404)
        app.db.x("INSERT INTO kv(k,v) VALUES('wb_cur',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (str(sid),))
        rows = app.db.q("SELECT r,t,acts,ts FROM wb_msgs WHERE sid=? ORDER BY id DESC LIMIT 60", (sid,))
        msgs = [{"r": x["r"], "t": x["t"],
                 "acts": json.loads(x["acts"]) if x["acts"] else []}
                for x in reversed(rows)]
        return web.json_response({"ok": True, "id": sid, "name": app.db.q(
            "SELECT name FROM wb_sessions WHERE id=?", (sid,))[0]["name"], "msgs": msgs})

    @r.post("/api/wb/session/rename")
    async def wb_rename(req):
        b = await req.json()
        sid, name = int(b.get("id") or 0), str(b.get("name") or "").strip()[:40]
        if not sid or not name:
            return web.json_response({"ok": False, "error": "缺 id 或 name"}, status=400)
        app.db.x("UPDATE wb_sessions SET name=? WHERE id=?", (name, sid))
        return web.json_response({"ok": True})

    @r.post("/api/wb/session/del")
    async def wb_del(req):
        """删会话连着它的消息一起删。界面必须先 confirm，这里没有 second chance。"""
        b = await req.json()
        sid = int(b.get("id") or 0)
        app.db.x("DELETE FROM wb_msgs WHERE sid=?", (sid,))
        app.db.x("DELETE FROM wb_sessions WHERE id=?", (sid,))
        # 删的是当前会话就把指针清掉，界面会自己建新会话
        cur = app.db.q("SELECT v FROM kv WHERE k='wb_cur'")
        if cur and cur[0]["v"] == str(sid):
            app.db.x("DELETE FROM kv WHERE k='wb_cur'")
        return web.json_response({"ok": True})
```

注意点：
- `now()` 在 server.py 里有现成的（别的表 ts 都是它），直接用。
- 名字截 40 字（防止贴一大段当名字）。
- `wb_sess_list()` 是内部函数，定义在路由内部（照 `wb_prepare` 的样子）。

### 1c. ask 两条路落库（`wb_prepare` 之后）

`@r.post("/api/ask")`（server.py:3075-3090）里 `text, acts, changed = await wb_run(...)`
**成功拿到结果后**（`return` 之前）加：

```python
            wb_save_pair(b.get("sid"), b.get("prompt", ""), text, acts)
```

`@r.post("/api/ask/stream")`（server.py:3092-3121）里 `done = {...}` 那行**之前**加：

```python
            wb_save_pair(b.get("sid"), b.get("prompt", ""), text, acts)
```

`wb_save_pair` 定义放在 `wb_prepare` 后面（server.py:3073 之后）：

```python
    def wb_save_pair(sid, user_text, ai_text, acts):
        """一轮一问一答落库。sid 为空或不存在就建个新会话；写进去同时把会话名顶成
        第一句话的头 20 个字（还是「新会话」才顶），并刷新 updated。"""
        user_text, ai_text = str(user_text)[:4000], str(ai_text or "")[:8000]
        if not user_text:
            return None
        sid = int(sid or 0)
        row = app.db.q("SELECT id,name FROM wb_sessions WHERE id=?", (sid,))
        if not row:
            ts = now()
            sid = app.db.x("INSERT INTO wb_sessions(name,created,updated) VALUES('新会话',?,?)",
                           (ts, ts))
            app.db.x("INSERT INTO kv(k,v) VALUES('wb_cur',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                     (str(sid),))
            row = [{"name": "新会话"}]
        ts = now()
        app.db.x("INSERT INTO wb_msgs(sid,r,t,acts,ts) VALUES(?,?,?,'',?)", (sid, "u", user_text, ts))
        app.db.x("INSERT INTO wb_msgs(sid,r,t,acts,ts) VALUES(?,?,?,?,?)",
                 (sid, "a", ai_text, json.dumps(acts or [], ensure_ascii=False), ts))
        name = row[0]["name"]
        if name == "新会话":
            name = user_text.replace("\n", " ").strip()[:20] or "新会话"
        app.db.x("UPDATE wb_sessions SET name=?,updated=? WHERE id=?", (name, ts, sid))
        return sid
```

**重要边界**（实现方照抄，别改）：
- 只在**成功**路径落库：`/api/ask` 的 `except` 分支、`plain=1` 提前 return 的分支、
  流式 `{t:error}` 分支**都不存**（一次失败的调用不该污染聊天）。`plain=1` 是「测试一次
  调用」探针，存进去会把聊天搞乱。
- 单条截断：用户输入 4000、模型回答 8000，防止有人贴整本书进库。
- 不用事务：一轮两条消息，丢了其中一条下次刷新也就是少半句，可接受（别为这层加锁，
  单进程单连接，照 messages 表的样子裸写即可）。

### 1d. 诊断包加一段（硬规矩 5 的邻居：新增存储要能自查）

诊断包 `[5.5] 批量提取的模板` 段（server.py diagnose 里，`P("\n[5.5] 批量提取的模板")`
那段之后）加 `[5.6] 工作台会话`：每个会话一行 `id / 名字 / 几条消息 / 最后更新时间`
（用 `time.strftime` 格式化 `updated`），一行都没有时印一句「还没有会话」。照 `[5.5]`
的写法抄。**只印条数和时间，不印消息内容**（聊天是隐私）。
加这一段的理由：用户说「聊天记录丢了」时，诊断包能直接看到库里到底存没存。

## 2. 前端（ui.html）

### 2a. 布局：工作台左侧加会话栏（ui.html:486-522 的 `#v-ai` section）

把 `<div class="wb">` 改成两栏：会话列表 + 原来的主区。最小改动方案——在
`<div class="pad" ...>` 里面、`<div class="wb">` **前面**加：

```html
        <div id="wbSide">
          <div class="hd"><span>会话</span><button class="btn sm" id="wbNew">＋ 新会话</button></div>
          <div id="wbSess"></div>
        </div>
```

`.wb` 那行（488）本身不动，把 487 行 `<div class="pad" ...>` 的 style 改成
`display:flex;gap:14px;align-items:stretch`，`.wb` 加 `flex:1;min-width:0`
（`.wb` 的 CSS 定义在 ui.html:255 `.wb{display:flex;flex-direction:column;gap:14px;height:100%}`，
在那里加 `flex:1;min-width:0;` 前缀即可）。

CSS 追加到样式区末尾（找 `/* workbench */` 附近没有就直接加在 `<style>` 结束前）：

```css
/* ============ workbench sessions ============ */
#wbSide{width:210px;flex-shrink:0;display:flex;flex-direction:column;gap:8px;
  border-right:1px solid var(--line);padding-right:12px}
#wbSide .hd{display:flex;align-items:center;gap:8px;font:500 12px var(--sans);
  color:var(--accent);letter-spacing:.06em}
#wbSide .hd .btn{margin-left:auto}
#wbSess{overflow-y:auto;display:flex;flex-direction:column;gap:4px;max-height:70vh}
.sess{padding:7px 10px;border-radius:8px;cursor:pointer;font-size:13px;
  display:flex;align-items:center;gap:6px;border:1px solid transparent}
.sess:hover{background:var(--bg-sunk)}
.sess.on{background:var(--bg-elev);border-color:var(--line2)}
.sess .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sess .n{font-size:11px;color:var(--text3)}
.sess .ops{display:none;gap:2px}
.sess:hover .ops{display:flex}
.sess .ops button{border:none;background:none;cursor:pointer;font-size:12px;padding:0 3px;
  color:var(--text3)}
```

### 2b. 状态 + 启动时装载（ui.html:792 附近 S 定义、boot 流程）

- `S` 里加两个字段：`wbSid:0, wbSess:[]`。
- 启动流程在 ui.html:2635 `const st=await api('/api/state');` 成功拿到 st 之后
  （`S.live` 置真的那段里）加调用 `wbLoadSessions()`（下面定义），**只在 `S.live` 为真时**
  （演示模式不调后端，演示模式 S.chat 照旧内存，刷新就没——可接受，演示模式本来就是给人看的）。
- 演示/演示按钮那块不用动。

### 2c. 会话函数（加在 `renderWb` 附近，ui.html:2415 之后）

```js
async function wbLoadSessions(){
  const r=await api('/api/wb/sessions');
  if(!r||!r.ok)return;
  S.wbSess=r.sessions;S.wbSid=r.cur||0;renderSess();
  if(S.wbSid)await wbOpen(S.wbSid,true);
  else if(!S.chat.length)await wbNewSess(true);       // 第一次用：建个空会话
}
function renderSess(){
  $('#wbSess').innerHTML=S.wbSess.map(s=>
    `<div class="sess${s.id===S.wbSid?' on':''}" data-sid="${s.id}">
      <span class="nm" title="${esc(s.name)}">${esc(s.name||'新会话')}</span>
      <span class="n">${s.n||0}</span>
      <span class="ops">
        <button data-ren="${s.id}" title="改名">✎</button>
        <button data-del="${s.id}" title="删除">🗑</button>
      </span></div>`).join('')
    ||`<div class="hint" style="padding:6px">还没有会话，点上面「＋ 新会话」</div>`;
  $$('#wbSess .sess').forEach(el=>el.onclick=e=>{
    if(e.target.closest('.ops'))return;
    wbOpen(+el.dataset.sid);});
  $$('#wbSess [data-ren]').forEach(b=>b.onclick=async e=>{
    e.stopPropagation();
    const s=S.wbSess.find(x=>x.id===+b.dataset.ren);if(!s)return;
    const name=prompt('会话改名叫：',s.name);if(!name||!name.trim())return;
    await api('/api/wb/session/rename','POST',{id:s.id,name:name.trim()});
    wbLoadSessions();});
  $$('#wbSess [data-del]').forEach(b=>b.onclick=async e=>{
    e.stopPropagation();
    const s=S.wbSess.find(x=>x.id===+b.dataset.del);if(!s)return;
    if(!confirm(`删掉「${s.name}」？里面的 ${s.n||0} 条消息一起删，找不回来。`))return;
    await api('/api/wb/session/del','POST',{id:s.id});
    if(s.id===S.wbSid){S.chat=[];S.wbSid=0;renderWb();}
    wbLoadSessions();});
}
async function wbOpen(sid,silent){
  if(sid===S.wbSid&&S.chat.length&&silent){renderSess();return;}
  const r=await api('/api/wb/session/open','POST',{id:sid});
  if(!r||!r.ok){toast(r&&r.error||'打不开会话');return;}
  S.wbSid=sid;
  S.chat=(r.msgs||[]).map(m=>({r:m.r,t:m.t,acts:m.acts||[]}));
  renderWb();renderSess();
}
async function wbNewSess(silent){
  const r=await api('/api/wb/session/new','POST',{});
  if(!r||!r.ok)return;
  S.wbSid=r.id;S.chat=[];renderWb();wbLoadSessions();
  if(!silent)toast('新会话开始了，之前聊的在左边列表里');
}
```

绑定（加在 `$('#wbClear').onclick=...`（2556 行）旁边）：

```js
$('#wbNew').onclick=()=>wbNewSess();
```

### 2d. 发送时带上 sid（`wbSend`，ui.html:2455-2474）

2462 行 `const body={prompt:t,...}` 加 `sid:S.wbSid`：

```js
  const body={prompt:t,provider:prov,model,msg_ids:[...S.ctx],history:hist,sid:S.wbSid};
```

演示分支（2463）不变（不入库）。

非演示、成功之后（2469-2474）：`wbAfter(r)` 调用**前后都行**，加一句刷新会话列表，
让左侧名字/条数跟着更新：

```js
  else if(r.ok){cur.t=r.text||'（模型这轮没说话）';cur.acts=r.acts||[];wbAfter(r);wbLoadSessions();}
```

流式路径：在 `wbApply` 的 `done` 分支（2424-2426）里 `wbAfter(ev)` 后加 `wbLoadSessions();`。

### 2e. 「清空」语义改一下（ui.html:2556）

现在「清空」只清内存。改成：**清空 = 删掉当前会话里的消息、但会话留着**。
简单做法——直接删会话再建新的，但别动库结构。改成调 `/api/wb/session/del` +
`/api/wb/session/new`？不好，会话 id 变了历史指向就断了。**改成只清前端 + 提示**：

```js
$('#wbClear').onclick=async()=>{
  S.ctx.clear();renderList();
  if(!S.live){S.chat=[];renderWb();return;}          // 演示：照旧
  if(!S.wbSid){S.chat=[];renderWb();return;}
  if(!confirm('把当前会话清成一个全新的？之前的对话会从库里删掉，找不回来。'))return;
  await api('/api/wb/session/del','POST',{id:S.wbSid});
  S.chat=[];S.wbSid=0;renderWb();
  await wbNewSess(true);
};
```

按钮文案改成「新话题」（同一个按钮，2556 行附近那个 `wbClear`，改 id 不动、只改界面上的字：
498 行 `清空` → `新话题`）。这样语义跟行为对上了：「新话题」= 旧存档删掉、开个新的。

### 2f. 硬规矩 1 检查

- 演示模式**不调**任何 `/api/wb/*`（`wbLoadSessions` 只在 `S.live` 真时调）；
- 会话列表加载失败静默不弹 toast（它不该打扰主流程）；
- 真失败（`/api/wb/session/open` 返回 ok:false）才 toast 真实错误。

## 3. 测试（tests/e2e_chat.py 新建）

照 `e2e_wb.py` 的写法（http/call/ok/script 一套）。**不改 mockllm**：ask 走默认假模型
返回固定文本即可。覆盖（目标 30 条上下）：

1. `/api/wb/sessions` 空库：sessions=[] cur=0；
2. `/api/wb/session/new` 建一个 → sessions 里多一条 name=新会话 n=0，cur 指向它；
3. 不带 sid 调 `/api/ask`（prompt=「第一句话测试」）→ 之后 sessions 里那个会话 n=2、
   name 被顶成「第一句话测试」、cur 指过去；
4. `/api/wb/session/open` 拿回 2 条消息，r/t 对，acts=[] ；
5. 再发一条带 sid → n=4；open 拿回 4 条、顺序 u,a,u,a；
6. 新建第二个会话发一条 → 两个会话各自独立；open 第一个还是 4 条；
7. rename：改名叫「bbb」，sessions 里看得到；传空 name → 400；
8. del：删掉第二个会话 → sessions 少一条、open 它 404；删当前会话 cur 清零；
9. plain=1 调 ask → **不进库**（n 不变）；
10. 失败后端（mockllm 回 error 或把模型指向不存在的 provider）→ 不进库；
11. open 不存在的 id → 404；
12. 长输入截断：发 5000 字的 prompt → 库里那条 ≤4000。

跑法挂进 `tests/RUN.md`：`./runall.sh e2e_chat.py`（写在 e2e_ext.py 后面那段，
总数改成 500+30=530 条）。

README 的「AI 工作台」一节加三行：聊天会存库、左侧会话列表可切换/改名/删、
「新话题」按钮是开新会话（旧的存档删掉）。

## 4. 验收（实现方自己跑完再交）

1. 全套回归：`cd tests && ./runall.sh e2e.py e2e_ai.py` … 照 RUN.md 分批全跑，
   必须 **530/530 全绿**（500 旧 + 30 新）。content_test 46/16 不用重跑（没动 extension），
   但 PLAN 里写明「没动 extension」。
2. 演示模式浏览器目检（直接开 ui.html，不起服务端）：左侧会话栏出现、写「还没有会话」，
   发一条消息演示分支照旧，不报错不调后端。
3. 真服务端浏览器目检：`DCWATCH_DB=/tmp/smoke.db python3 server.py` 起服务，
   开界面 → 工作台发两条 → **按 F5 刷新** → 聊天还在、左侧会话在、名字是第一句；
   点「＋ 新会话」→ 聊天清空、列表多一条；切回第一个会话 → 聊天回来；
   改名、删除走一遍 confirm；「新话题」点完聊天清空、列表里多一个「新会话」。
4. 诊断包里能看到 `[5.6] 工作台会话` 段。

## 5. 不许做的事（写死）

- 不许动 `extension/`、`manifest.json`、`EXT_MIN`、`VERSION`；
- 不许给会话加导出/导入（这轮不做，别顺手）；
- 不许改 `wb_prepare` 的 history 截 8 条逻辑；
- 不许把消息内容塞进诊断包正文（只印条数和时间，内容是隐私）；
- 不许给 `/api/wb/*` 加鉴权或锁（本机程序，照其他接口的样子裸奔即可）；
- 演示模式不许调后端；
- 删除必须前端 confirm（服务端不设防，照本 PLAN 注释里写的「没有 second chance」）。

## 6. 完工动作

- BACKLOG.md A1、C1 打勾（注明「工作台聊天落 SQLite + 左侧会话列表」）；
- NOW.md 状态改成「A1+C1 施工完成，下一件 Claude review」；
- save.py 存盘推送。
