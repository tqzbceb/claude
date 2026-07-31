# PLAN_A3A5 —— 修「重复通知」(A3) +「关了开关还弹」(A5)（施工图，照抄即可）

> 双模型流程的**实现方案**：Claude（Fable5）已把根因查明、修法定死、验收写死。
> 实现者**只做本文件列出的改动**，不重构、不顺手改别处、不改格式。
> 每完成一个修改就 `python3 .bcode/agent-workspace/save.py "A3A5: 修改N 完成"` 存盘推送。
> 干活在 `/tmp/dcw`（`git clone https://github.com/tqzbceb/claude.git /tmp/dcw`），不在 `./claude/` 里改。
> **这轮不动 extension/**，不用提 EXT_MIN、不用重装扩展。

## 根因（不用再查，已坐实）

两个 bug 是同一个东西的两面：**ui.html 的「网页通知」`new Notification(...)` 在 Windows
上就显示在系统通知中心里**，用户分不出它和 dcwatch 的「Windows 系统通知」。

- **A3（一条消息弹 3 条）**：每个打开的 dcwatch 标签页各持一条 SSE（ui.html `connectSSE`，
  2592 行），收到命中就各自 `new Notification`（2597–2598 行，**没有 tag，Chrome 不合并**）。
  N 个标签页 = N 条，再加 server 的原生 toast 1 条 → 2 个标签页恰好 3 条。
  标签页很容易多出来：用户每次双击 dcwatch.exe，第二个进程发现端口被占就再开一个
  浏览器标签页（server.py main() 3796–3798 行，这是有意的设计，不改它）。
- **A5（三个开关全关了还弹）**：`S.cfg` 只在 boot 时取一次；15 秒轮询（2624–2628 行）
  **有意不刷新 S.cfg**（防止冲掉正在编辑的表单）；而 `/api/config` 保存 sinks 后服务端
  **不广播**（bus 只在 sinks/test 改 verified 时才 push "sinks"，3410 行）。
  → 用户在一个标签页里把三个开关全关掉，**其它早开着的标签页里 `S.cfg.sinks.browser`
  永远停在 true**，继续弹「系统通知」。服务端自己的 toast/sound 路径（notify → queue_local，
  2196 行）读的是实时 cfg，是好的——所以用户关掉开关后 server toast 确实停了，
  但旧标签页的网页通知长得一模一样，用户以为开关全坏了。

排查过、**没有问题、不要动**的地方（review 时别被绕进去）：
- 服务端 msg_id 去重（handle_event 的 `_inflight` + DB 查重，1996–2010 行）是好的，
  多桥重复投递不会重复通知，e2e_multi.py 已覆盖；
- 本机提醒合并队列（queue_local / _flush_local，5 秒窗口攒成一条）是好的；
- `local_toast` 的 4 秒防刷屏、`local_sound` 的 2 秒闸都是好的；
- freeport / 双实例：第二个进程起不来（端口占用直接退出），不存在两份服务同时弹。

---

## 修改 1 · server.py：保存 sinks 后广播给所有开着的界面

位置：`server.py` 约 2659–2660 行，setcfg 里 `app.cfg.update(patch)` / `app.save_cfg()` 之后、
`if "discord" in patch:` 之前。
现状：
```python
        app.cfg.update(patch)
        app.save_cfg()
        if "discord" in patch:
```
改成：
```python
        app.cfg.update(patch)
        app.save_cfg()
        if "sinks" in patch:
            # 开关改了要立刻告诉所有开着的标签页：不然旧页面拿着旧配置
            # 继续弹网页通知，用户以为「关了开关还弹」（A5）
            await app.bus.push("sinks", app.cfg["sinks"])
        if "discord" in patch:
```
说明：ui.html 已有 `kind==='sinks'` 的 SSE 处理器（2602 行：`S.cfg.sinks=data` + 重画），
不用改前端就能生效。push 原值不掩码——`safe_cfg` 本来就不掩码 hooks（2517–2527 行），
sinks/test 的既有 push（3410 行）也是原值，口径一致。

## 修改 2 · ui.html：网页通知加 tag，多标签页只弹一条

位置：`ui.html` 约 2597–2598 行（connectSSE 里）。
现状：
```js
        if(data.alert&&S.cfg?.sinks?.browser&&Notification?.permission==='granted')
          new Notification(`${data.author} · ${data.channel_name}`,{body:(data.content||'').slice(0,120)});}
```
改成：
```js
        if(data.alert&&S.cfg?.sinks?.browser&&Notification?.permission==='granted')
          new Notification(`${data.author} · ${data.channel_name}`,{body:(data.content||'').slice(0,120),
            tag:'dcw-'+(data.msg_id||data.id||'')});}
```
原理：同源同 tag 的 Notification，Chrome 只显示一条（后来的替换先前的）——
开 3 个 dcwatch 标签页也只弹 1 条（A3）。

## 修改 3 · ui.html：帮助文案把这层窗户纸捅破

位置：`ui.html` 约 2175 行，notify 帮助里「Windows 系统通知」那个 `<p>` 之后加一行：
```html
    <p><b>网页通知</b>：由浏览器弹出，在 Windows 里<b>同样出现在系统通知中心</b>（发件人是
      Chrome/Edge）。它和上面的「Windows 系统通知」是两条独立通道——两个都开就会各收一条，
      只想要一条就关掉其中一个。开关保存后对所有开着的 dcwatch 页面立刻生效。</p>
```

## 修改 4 · tests/e2e.py：新增第 12 节回归（SSE 广播）

位置：`tests/e2e.py` 第 175 行（`chk("删除规则", ...)`）之后、末尾统计 print 之前。
插入：
```python
print("12. 保存 sinks 后 SSE 广播（A5：别的标签页要立刻知道开关变了）")
import threading
got_sse = []
def _listen():
    req = urllib.request.Request(B + "/api/events")
    with urllib.request.urlopen(req, timeout=15) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if line.startswith("data: "):
                e = json.loads(line[6:])
                got_sse.append(e)
                if e["kind"] == "sinks":
                    return
th = threading.Thread(target=_listen, daemon=True)
th.start()
time.sleep(0.5)                          # 等订阅真挂上
call("/api/config", {"sinks": {"toast": False, "sound": False, "browser": False}})
th.join(6)
ev = [e for e in got_sse if e["kind"] == "sinks"]
chk("SSE 推了 sinks 事件", bool(ev), [e["kind"] for e in got_sse][:5])
chk("推的就是最新开关", bool(ev) and ev[0]["data"].get("toast") is False
    and ev[0]["data"].get("browser") is False, ev and ev[0]["data"])
```
注意：不用再「放回」开关——本文件第 2 节本来就把 toast/sound 设成 False 跑全程，
browser 字段后面的节不读；每个测试文件都自己设自己要的 sinks。

---

## 验收（实现者自测，全绿才算完）

在 `/tmp/dcw` 下按 `tests/RUN.md` 的方式起服务跑全套（mock 8899 / echo 8898 / 服务 8777）：
```bash
cd /tmp/dcw && bash tests/runall.sh
```
- `e2e.py` 应比上轮多 2 条（新第 12 节），其余各套数目不变；
- 全套通过数 = **500**（上轮 498 + 2），一条不许红；
- `node tests/content_test.mjs` 不受影响（没动扩展），46+16 全绿。

UI 目检（有浏览器就做，没有就在交付说明里写明没做）：
- 打开 `ui.html`（演示模式即可）→ 控制台无报错 → 「通知与转发」页能开、三个开关能切。

## 收尾（照窗口协议）

1. `tests/RUN.md`：e2e.py 覆盖清单加一行「保存 sinks 后 SSE 广播」，总数 498 → 500；
2. `HANDOFF.md`：本轮总结加 A3+A5 一段（根因两行 + 修法三行即可）；
3. `BACKLOG.md`：A3、A5 打勾，备注「网页通知与系统通知是两条通道，已在帮助里说明；
   真机多标签页场景请用户发版后验一次」；
4. `NOW.md`：状态改成「A3+A5 施工完毕待 review，下一件按 BACKLOG 是 A4」；
5. `python3 .bcode/agent-workspace/save.py "A3+A5: sinks 广播 + Notification tag + 回归 500"`。

## 发版时要提醒用户的（写进交付说明，这轮不发版）

- 网页通知在 Windows 通知中心里的发件人是浏览器（Chrome/Edge），不是 dcwatch——
  想彻底关掉它，dcwatch 里关「网页通知」即可，改完立刻生效，不用重启；
- 如果还想只留一条：三个开关只开一个；
- A2 那轮动过 extension/content.js，发版仍要提 EXT_MIN、让用户重装扩展（见 NOW.md 旧注）。
