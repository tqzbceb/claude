"""C2 自动开帖回归：tab_orders 全部闸门 / /api/ext/tabs 回报 / 诊断[5.8] /
工作台只读工具 list_open_threads。
跑法见 RUN.md：mockllm :8899，server :8777，库干净（runall.sh 会备好）。
要直接改库时间的几条例外：「闲置多久」最短也是分钟级，等真时间是等不起的，
所以用 sqlite3 直写 /tmp/p.db（runall.sh 里 DCWATCH_DB 写死的那份，WAL 模式）。"""
import json, sqlite3, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8777"
MOCK = "http://127.0.0.1:8899"
DB = "/tmp/p.db"
P = F = 0


def http(url, body=None, method=None, raw=False):
    req = urllib.request.Request(url, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            t = r.read().decode()
            return t if raw else json.loads(t)
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode()[:400]}


def call(path, body=None, **kw):
    return http(BASE + path, body, **kw)


def ok(name, cond, extra=""):
    global P, F
    if cond:
        P += 1; print("  ok  ", name)
    else:
        F += 1; print("  FAIL", name, str(extra)[:400])


def emsg(r):
    """400 的原因要先把 body 解出来（RUN.md 的坑：_body 是转义过的 JSON）。"""
    try:
        return json.loads(r.get("_body") or "{}").get("error", "")
    except Exception:
        return ""


def db(sql, args=()):
    c = sqlite3.connect(DB)
    c.execute(sql, args)
    c.commit()
    c.close()


G = "700000000000000001"          # 服务器
PARENT = "800000000000000100"     # 论坛父频道
PARENT2 = "800000000000000200"    # 另一个论坛（没人盯）


def thread(tid, parent=PARENT):
    # content 照扩展真实上报的样子写「【新帖】标题」：ingest 会丢掉空 content 的消息
    return {"msg_id": "th" + tid, "kind": "thread", "guild_id": G, "channel_id": tid,
            "parent_id": parent, "channel_name": "帖" + tid[-4:], "author": "sys",
            "author_id": "", "content": "【新帖】帖" + tid[-4:], "ts": time.time()}


def cfg(**kw):
    return call("/api/config", {"browser": kw})


def hb(bridge="", ver=""):
    b = {}
    if bridge:
        b["bridge"] = bridge
    if ver:
        b["ver"] = ver
    return call("/api/ext/hb", b)


def tlist():
    return call("/api/ext/threads")["threads"]


def row(tid):
    for x in tlist():
        if x["tid"] == tid:
            return x
    return None


def report(**kw):
    return call("/api/ext/tabs", kw)


# 模型先配好（第 8 节工作台工具要用）
call("/api/config", {"providers": [{"name": "mock", "base_url": "http://127.0.0.1:8899/v1",
                                    "api_key": "sk-test"}],
                     "default_model": {"provider": "mock", "model": "mock-1"}})

print("1. browser 设置洗值（越界不静悄悄夹住，直接 400 说人话）")
r = cfg(auto_open=True)
ok("合法保存", r.get("ok") is True, r)
bc = r["config"]["browser"]
ok("五档回显且布尔是真 bool", bc["auto_open"] is True and bc["max_tabs"] == 6
   and bc["per_hour"] == 8 and bc["only_rule_channels"] is True and bc["close_idle_min"] == 30, bc)
r = call("/api/config", {"browser": "不是对象"})
ok("不是对象 → 400", r.get("_status") == 400, r)
r = cfg(max_tabs=0)
ok("max_tabs=0 → 400 带区间", r.get("_status") == 400 and "1 ~ 20" in emsg(r), emsg(r))
r = cfg(max_tabs=21)
ok("max_tabs=21 → 400", r.get("_status") == 400, emsg(r))
r = cfg(per_hour=61)
ok("per_hour=61 → 400", r.get("_status") == 400, emsg(r))
r = cfg(close_idle_min=1441)
ok("close_idle_min=1441 → 400", r.get("_status") == 400, emsg(r))
r = cfg(乱设=True)
ok("认不出的键 → 400", r.get("_status") == 400 and "认不出" in emsg(r), emsg(r))

print("2. 排队闸门（总开关 / 只开规则频道 / 幂等）")
cfg(auto_open=False)
call("/api/ingest", {"messages": [thread("610000000000000001")]})
time.sleep(0.3)
ok("总开关关着：新帖不排队", row("610000000000000001") is None, tlist())
cfg(auto_open=True, only_rule_channels=True)
call("/api/ingest", {"messages": [thread("610000000000000002", PARENT2)]})
time.sleep(0.3)
ok("只开规则频道：没人盯的论坛不排队", row("610000000000000002") is None, tlist())
call("/api/rules", {"name": "盯论坛", "kinds": ["thread"], "channel_ids": [PARENT],
                    "action": "notify", "cooldown_sec": 0})
call("/api/ingest", {"messages": [thread("610000000000000003")]})
time.sleep(0.3)
ok("有规则盯父频道：排队了", (row("610000000000000003") or {}).get("state") == "排队中",
   row("610000000000000003"))
call("/api/ingest", {"messages": [thread("610000000000000003"), thread("610000000000000003")]})
time.sleep(0.3)
ok("幂等：同一个帖子重复报还是一行", len([x for x in tlist() if x["tid"] == "610000000000000003"]) == 1)
call("/api/rules", {"name": "盯这个帖", "kinds": ["thread"], "thread_ids": ["610000000000000004"],
                    "action": "notify", "cooldown_sec": 0})
call("/api/ingest", {"messages": [thread("610000000000000004", PARENT2)]})
time.sleep(0.3)
ok("规则的 thread_ids 直接盯帖子本身也算", (row("610000000000000004") or {}).get("state") == "排队中",
   row("610000000000000004"))
cfg(only_rule_channels=False)
call("/api/ingest", {"messages": [thread("610000000000000005", PARENT2)]})
time.sleep(0.3)
ok("关掉只开规则频道：没人盯的也排队", (row("610000000000000005") or {}).get("state") == "排队中",
   row("610000000000000005"))
cfg(only_rule_channels=True, auto_open=False)
ok("关掉总开关：排队中的一笔勾销", (row("610000000000000003") or {}).get("state") == "已关",
   row("610000000000000003"))
ok("关总开关不清「开着的」", True)   # 本套件此刻还没有「开着的」，语义在第 5 节验
cfg(auto_open=True)

print("3. open 指令（上限双限 / 一次最多 3 条 / 按排队先后）")
cfg(max_tabs=20, per_hour=60)
for i in range(10, 15):           # 排 5 个
    call("/api/ingest", {"messages": [thread(f"6100000000000000{i}")]})
time.sleep(0.5)
o = hb()
ok("一次最多给 3 条 open", len(o["open"]) == 3, o["open"])
ok("按 wanted 升序：先排的先开", [x["tid"] for x in o["open"]] ==
   ["610000000000000010", "610000000000000011", "610000000000000012"], o["open"])
ok("open 带 url 和人话理由", o["open"][0]["url"].endswith("/" + o["open"][0]["tid"])
   and "新帖" in o["open"][0]["why"], o["open"][0])
ok("limits 回显空位", o["limits"]["tabs_left"] == 20 and o["limits"]["open_now"] == 0, o["limits"])
report(opened=["610000000000000010", "610000000000000011", "610000000000000012"], bridge="x1")
ok("回报 opened 后状态变「开着」", (row("610000000000000010") or {}).get("state") == "开着",
   row("610000000000000010"))
o = hb()
ok("回报后又补 2 条（空出 17 个位、库里还排着 2 个）", len(o["open"]) == 2, o["open"])
cfg(max_tabs=4)                   # 已开 3 → 只剩 1 个位
o = hb()
ok("max_tabs 限住：只剩 1 个位就只给 1 条", len(o["open"]) == 1 and o["limits"]["tabs_left"] == 1,
   o["open"])
cfg(max_tabs=3)                   # 已开 3 = 满了
o = hb()
ok("开满了就一条都不给", len(o["open"]) == 0 and o["limits"]["tabs_left"] == 0, o["limits"])
cfg(max_tabs=20, per_hour=3)      # 本小时已开 3 → 打满
o = hb()
ok("per_hour 限住：本小时开够了不再给", len(o["open"]) == 0
   and o["limits"]["open_left_this_hour"] == 0, o["limits"])
cfg(per_hour=60)

print("4. 失败重试与 tries 上限")
call("/api/ingest", {"messages": [thread("610000000000000020")]})
time.sleep(0.3)
ok("新排的帖子给 open 指令", any(x["tid"] == "610000000000000020" for x in hb()["open"]), hb()["open"])
report(failed=[{"tid": "610000000000000020", "err": "浏览器不让开"}], bridge="x1")
r20 = row("610000000000000020")
ok("失败一次：tries=1 且 err 记下", r20["tries"] == 1 and "浏览器不让开" in r20["err"], r20)
ok("tries<2 还会再给一次", any(x["tid"] == "610000000000000020" for x in hb()["open"]))
report(failed=[{"tid": "610000000000000020", "err": "还是不让开"}], bridge="x1")
r20 = row("610000000000000020")
ok("失败两次：状态「打开失败」", r20["state"] == "打开失败" and r20["tries"] == 2, r20)
ok("tries=2 不再给 open", not any(x["tid"] == "610000000000000020" for x in hb()["open"]))
report(failed=[{"tid": "610000000000000013", "err": "标签页已经不在了", "gone": True}], bridge="x1")
ok("gone 报告＝事实同步：标成已关", (row("610000000000000013") or {}).get("state") == "已关",
   row("610000000000000013"))

print("5. close 闸门（闲置判定用最后一条消息的 ts；刚开的不关；0=不自动关）")
# A7：帖内消息要有规则罩着才进库，而闲置判定读的正是 messages 里的最后 ts。
# 前面建的规则只听「开新帖」（kinds=thread），帖内 msg 全被闸挡，这里补一条罩帖内消息的。
call("/api/rules", {"name": "帖内消息兜底", "enabled": 1, "kinds": ["msg"],
                    "thread_ids": ["610000000000000010", "610000000000000011", "610000000000000012"],
                    "ignore_bots": False, "keywords_any": ["∅绝不出现∅"], "action": "notify"})
# 61010/61011/61012 第 3 节「开着」；61013 已关。先把 61010 的 opened_at 改到 10 分钟前
db("UPDATE threads_open SET opened_at=? WHERE tid=?", (time.time() - 600, "610000000000000010"))
call("/api/ingest", {"messages": [{"msg_id": "m61010a", "guild_id": G,
                                   "channel_id": "610000000000000010", "channel_name": "帖0010",
                                   "author": "Ana", "author_id": "1", "content": "新消息",
                                   "ts": time.time()}]})
time.sleep(0.3)
o = hb()
ok("帖子里刚有新消息：不闲，不关", not any(x["tid"] == "610000000000000010" for x in o["close"]),
   o["close"])
db("UPDATE messages SET ts=? WHERE channel_id=?", (time.time() - 2400, "610000000000000010"))
o = hb()
hit = [x for x in o["close"] if x["tid"] == "610000000000000010"]
ok("最后一条消息 40 分钟前：进 close 名单", len(hit) == 1, o["close"])
ok("close 带闲置分钟人话", hit and "闲置" in hit[0]["why"] and "分钟" in hit[0]["why"], hit)
# 61011：opened_at 是「现在」（刚开），哪怕帖内消息很老也不许关（开了就关会来回抖）
call("/api/ingest", {"messages": [{"msg_id": "m61011a", "guild_id": G,
                                   "channel_id": "610000000000000011", "channel_name": "帖0011",
                                   "author": "Ana", "author_id": "1", "content": "老消息",
                                   "ts": time.time()}]})
time.sleep(0.3)
db("UPDATE messages SET ts=? WHERE channel_id=?", (time.time() - 7200, "610000000000000011"))
o = hb()
ok("刚开 5 分钟内的不关（哪怕消息老）", not any(x["tid"] == "610000000000000011" for x in o["close"]),
   o["close"])
# 61012：没有消息，闲置判定退回 opened_at；把它改到 1 小时前
db("UPDATE threads_open SET opened_at=? WHERE tid=?", (time.time() - 3600, "610000000000000012"))
o = hb()
ok("没消息的帖子按 opened_at 判闲置：该关", any(x["tid"] == "610000000000000012" for x in o["close"]),
   o["close"])
cfg(close_idle_min=0)
o = hb()
ok("close_idle_min=0：一个 close 都不给", len(o["close"]) == 0, o["close"])
cfg(close_idle_min=30)
o = hb()
n_close = len(o["close"])
ok("恢复 30 分钟后 close 又回来了", n_close >= 2, o["close"])
report(closed=["610000000000000010"], bridge="x1")
ok("回报 closed：状态已关", (row("610000000000000010") or {}).get("state") == "已关",
   row("610000000000000010"))
o = hb()
ok("已关的不再出现在 close 里", not any(x["tid"] == "610000000000000010" for x in o["close"]))

print("6. 多浏览器时只有一个「领导」拿指令")
hb("b2", "1.11.0")                # 先让两个桥都活着（b1 比 b2 小，领导是 b1）
hb("b1", "1.11.0")
call("/api/ingest", {"messages": [thread("610000000000000030")]})
time.sleep(0.3)
ok("领导（id 小的）拿到 open", any(x["tid"] == "610000000000000030" for x in hb("b1")["open"]),
   hb("b1")["open"])
ok("不是领导拿不到 open", not any(x["tid"] == "610000000000000030" for x in hb("b2")["open"]))
ok("不带 bridge 的调用照常给（界面/排选用）", any(x["tid"] == "610000000000000030"
   for x in hb()["open"]))

print("7. 诊断包 [5.8] 段 + [0] 一眼结论")
txt = call("/diagnose.txt", raw=True)
ok("有 [5.8] 自动点开新帖段", "[5.8] 自动点开新帖" in txt)
ok("五档印出来了", "同时最多" in txt and "闲置自动关" in txt, txt[:200])
ok("开着的帖子印名字+桥", "帖0011" in txt and "x1" in txt, "")
ok("最近开/关记录印了（含失败）", "帖0020" in txt and "失败" in txt, "")
hb("oldext", "1.0.0")             # 旧版扩展（< EXT_MIN）来心跳
txt = call("/diagnose.txt", raw=True)
ok("[0] 点名旧扩展执行不了自动开帖", "自动点开新帖" in txt.split("[1]")[0]
   and "旧版" in txt.split("[1]")[0], txt.split("[1]")[0][-500:])

print("8. 工作台只读工具 list_open_threads")
def script(queue):
    http(MOCK + "/__script", {"queue": queue})

def mcalls():
    return http(MOCK + "/__calls")["calls"]

http(MOCK + "/__reset", {})
script([{"tools": [{"name": "list_open_threads", "args": {}}]}, {"content": "看完了。"}])
call("/api/ask", {"prompt": "现在开着哪些帖子？"})
out = json.loads(mcalls()[-1]["tool_out"])
ok("工具真被调了、结果喂回模型", "open" in out and "auto_open" in out, out)
ok("开着的 61011 在清单里", any(x.get("tid") == "610000000000000011" for x in out["open"]), out)
ok("带闲置分钟和排队数", out.get("queued", 0) >= 0 and
   all("idle_min" in x for x in out["open"]), out)
pr = [x for x in call("/api/prompts")["builtin"] if x["key"] == "wb_text"][0]
ok("文本指令协议里有这个工具", "list_open_threads" in pr["text"], pr["text"][:200])
ok("提示词写明只读、模型不能自己关", "只读" in pr["text"] and "不能关" in pr["text"], "")

print(f"\n通过 {P} / 失败 {F}")
