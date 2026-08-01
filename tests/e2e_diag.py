"""v1.7.2 的「一眼结论」回归：app.findings() → /api/state.findings 和诊断包 [0] 段。

这一套对**库和进程内的桥都很敏感**（结论就是照实描述当前状态），所以必须
`rm -f /tmp/p.db*` + 重启 server 之后单独跑，别接在别的套后面。
跑法见 RUN.md。要求 server 在 :8777。
"""
import json, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8777"
P = F = 0


def call(path, body=None, method=None, raw=False):
    req = urllib.request.Request(BASE + path, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read().decode()
            return t if raw else json.loads(t)
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode()[:300]}


def ok(name, cond, extra=""):
    global P, F
    if cond:
        P += 1; print("  ok  ", name)
    else:
        F += 1; print("  FAIL", name, extra)


def fnd():
    return call("/api/state").get("findings")


def has(key, level=None):
    """结论里有没有提到某件事（按现象文字里的关键词找）"""
    for f in fnd() or []:
        if key in f["what"] and (level is None or f["level"] == level):
            return f
    return None


STATS = {"parsed": 22, "sent": 0, "lastSentAt": 0,
         "skip": {"history": 0, "render": 22, "dup": 0, "notext": 8, "quiet": 0},
         "recent": [{"t": 0, "what": "出问题了", "why": "整批渲染（切频道/点开子区/往上滚），当历史跳过"}],
         "url": "/channels/1134557553011998840/1513056466293096518", "lis": 30}


# 扩展版本别写死：EXT_MIN 一 bump，写死的版本就会让「一切正常」那节假红。
# 直接问服务端要求的最低版本，当作「桥上装的是新版扩展」。
EXT_OK = call("/api/state").get("env", {}).get("ext_min") or "9.9.9"


def ping(bridge, **kw):
    kw.setdefault("ver", EXT_OK)
    return call("/api/ingest", dict({"ping": True, "bridge": bridge, "account": "u9527",
                                    "where": "Discord"}, **kw))


print("1. 什么都没配的时候")
f = fnd()
ok("/api/state 带 findings", isinstance(f, list) and f, f)
ok("说了没有浏览器在旁听", has("没有任何浏览器", "bad"), f)
ok("说了一条规则都没有", has("一条规则都没有", "bad"), f)
ok("每条结论都有该做什么", all(x.get("why") for x in f), f)

print("2. 桥的状态")
ping("b-real", ver=EXT_OK, browser="Chrome 150", stats=STATS)
ok("有桥之后不再说没有浏览器", not has("没有任何浏览器"), fnd())
ok("解析到了却一条没上报要说出来", has("一条都没上报", "warn"), fnd())
w = has("一条都没上报")
ok("说清了跳过原因", "整批渲染" in (w or {}).get("why", ""), w)
ok("解析条数没被重复累加", "22 条" in (w or {}).get("what", ""), w)
ok("库里没消息也点出来", has("库里一条消息都没有", "warn"), fnd())

ping("page-direct", ver=EXT_OK, browser="页面直连（扩展需按 F5）", stale_ctx=True, stats=STATS)
ok("真桥+旧脚本 → 提醒按 F5（只是提醒）", has("需要按 F5", "warn"), fnd())
w = has("一条都没上报")
ok("两个身份同一页，条数仍然是 22 不是 44", "22 条" in (w or {}).get("what", ""), w)

print("3. 旧版扩展")
ping("b-old", ver="1.5.0", browser="Chrome 149", stats={"parsed": 1, "sent": 1, "skip": {}})
ok("旧版扩展会被点名", has("旧版 v1.5.0", "warn"), fnd())

print("4. 规则")
rid = call("/api/rules", {"name": "t", "enabled": 0, "keywords_any": ["密钥"],
                          "action": "notify"}).get("id")
ok("有规则但全停用 → 说全停用", has("全是停用状态", "bad"), fnd())
# 改规则是 POST /api/rules 带 id（没有 PUT 路由，写成 PUT 会 405 而且静默不生效）
call("/api/rules", {"id": rid, "name": "t", "enabled": 1, "keywords_any": ["密钥"],
                    "action": "notify"})
ok("开起来之后这两条都没了", not has("一条规则都没有") and not has("全是停用状态"), fnd())

print("5. 有消息但不命中")
call("/api/ingest", {"messages": [{"msg_id": "d1", "channel_id": "700000000000000001",
                                  "channel_name": "c", "author": "Bob", "content": "闲聊两句",
                                  "ts": time.time()}], "account": "u9527"})
time.sleep(0.6)
ok("有消息一条不命中 → 指路试算", has("一条都没命中规则", "warn"), fnd())
ok("库里一条消息都没有 这条自动消失", not has("库里一条消息都没有"), fnd())

print("6. 命中了却没有任何出口")
call("/api/config", {"sinks": {"toast": False, "sound": False, "hooks": []}})
ok("弹窗声音出口全关 → 明说没动静", has("也不会有动静", "warn"), fnd())
call("/api/config", {"sinks": {"toast": True}})
ok("打开弹窗后这条没了", not has("也不会有动静"), fnd())

print("7. 收信开关关掉")
call("/api/source/toggle", {"source": "browser", "on": False})
ok("开关关着是 bad", has("开关是关的", "bad"), fnd())
call("/api/source/toggle", {"source": "browser", "on": True})
ok("打开后恢复", not has("开关是关的"), fnd())

print("8. 一切正常时不许硬凑问题")
call("/api/ingest", {"messages": [{"msg_id": "d2", "channel_id": "700000000000000001",
                                  "channel_name": "c", "author": "Bob",
                                  "content": "这是密钥 ABCD", "ts": time.time()}], "account": "u9527"})
time.sleep(0.8)
ping("b-real", ver=EXT_OK, browser="Chrome 150",
     stats={"parsed": 5, "sent": 2, "skip": {"history": 3}})
ping("b-old", ver=EXT_OK, browser="Chrome 149", stats={"parsed": 1, "sent": 1, "skip": {}})
call("/api/ingest", {"ping": True, "bridge": "page-direct", "stale_ctx": False,
                     "browser": "Chrome 150", "ver": EXT_OK, "account": "u9527"})
f = fnd()
bad = [x for x in f if x["level"] != "ok"]
ok("没有残留的问题条目", not bad, bad)
ok("给一句「没查出明显问题」", f and f[0]["level"] == "ok", f)
ok("并且报出在旁听几个、几条规则", "规则" in f[0]["why"], f)

print("9. 诊断包 [0] 段")
d = call("/diagnose.txt?text=0", raw=True)
ok("有 [0] 一眼结论", "[0] 一眼结论" in d, d[:200])
ok("[0] 在 [1] 之前", d.index("[0] 一眼结论") < d.index("[1] 程序"), "顺序不对")
ok("结论里带符号标记", ("✓" in d or "✗" in d or "! " in d), d[:400])
ok("旧脚本的桥在 [3] 段有注意行", True, "")   # 下面单独造一次
call("/api/ingest", {"ping": True, "bridge": "page-direct2", "stale_ctx": True,
                     "browser": "页面直连（扩展需按 F5）", "ver": EXT_OK, "account": "u9527"})
d = call("/diagnose.txt?text=0", raw=True)
ok("[3] 段标出了「旧脚本直连」", "旧脚本直连" in d, d[d.find("[3]"):d.find("[4]")][:600])
ok("诊断包不泄露密钥", "sk-" not in d, "有 sk-")

# ---------------------------------------------------------------- v1.7.3
# 用户 0731-1118 的现场：开着的规则只听「新帖」，能听消息的那条被停用了。
# 光看条件本身两条都很像对的，所以诊断必须自己把这件事说出来 + 拿真消息试算。
print("10. 只听新帖的规则（v1.7.3）")
for j in call("/api/state")["rules"]:
    call("/api/rules/%s" % j["id"], method="DELETE")
call("/api/messages/clear", {})
GID, CID = "1134557553011998840", "1513056466293096518"
r_off = call("/api/rules", {"name": "频道监听-所有消息", "enabled": 0, "channel_ids": [CID],
                            "include_threads_of_channels": True, "action": "notify"}).get("id")
r_on = call("/api/rules", {"name": "帖子监听-所有消息", "enabled": 1, "thread_ids": [CID],
                           "kinds": ["thread"], "action": "notify"}).get("id")
w = has("只在「有人开新帖」时触发")
ok("开着的规则只听新帖 → 直接点名", bool(w) and w["level"] == "bad", fnd())
ok("并且点出是哪一条", "帖子监听-所有消息" in (w or {}).get("what", ""), w)
ok("并且指到第 0 项那一格", "什么时候触发" in (w or {}).get("why", ""), w)
ok("还提醒有条停用的听消息规则可以直接开", "频道监听-所有消息" in (w or {}).get("why", ""), w)

ping("b-real", ver=EXT_OK, browser="Chrome 150", stats=STATS)
d = call("/diagnose.txt?text=0", raw=True)
seg4 = d[d.index("[4] 规则"):d.index("[4.5]")]
ok("[4] 段印出了 kinds", "什么时候" in seg4, seg4[:400])
ok("只听新帖的那条被标出来", "只听新帖" in seg4, seg4[:600])
ok("听消息的那条不标", seg4.count("只听新帖") == 1, seg4[:600])

print("11. 拿真实消息试算（v1.7.3）")
seg = d[d.index("[4.5]"):d.index("[5] 通知")]
ok("有 [4.5] 段", "拿真实消息试算" in seg, seg[:200])
ok("库是空的时候用扩展 recent 里的正文", "出问题了" in seg, seg[:800])
ok("停用的那条会命中，并注明它是停用的", "会命中" in seg and "停用的" in seg, seg[:900])
ok("开着的那条给出人话原因", "只在「开新帖」时触发" in seg, seg[:900])
ok("正文截断有注脚", "前 40 字" in seg, seg[:900])

# 库里有真消息时优先用库里的，而且能报出「频道 ID 填错」这种最常见的卡点
call("/api/rules/%s" % r_on, method="DELETE")
call("/api/rules", {"id": r_off, "name": "频道监听-所有消息", "enabled": 1,
                    "channel_ids": ["999999999999999999"], "action": "notify"})
# A7：收信闸只收有规则罩着的消息。补一条罩得住但永不提醒的兜底规则，
# 让 v173a 能落库 —— 不然 [4.5] 拿不到真消息试算，正是这一节要验的东西。
call("/api/rules", {"name": "A7测试兜底", "enabled": 1, "kinds": ["msg", "thread"],
                    "ignore_bots": False, "keywords_any": ["∅绝不出现∅"], "action": "notify"})
call("/api/ingest", {"messages": [{"msg_id": "v173a", "guild_id": GID, "channel_id": CID,
                                   "channel_name": "资源区", "author": "Bob",
                                   "content": "有人送 mimo 的 key", "ts": time.time()}],
                     "account": "u9527"})
time.sleep(0.8)
d = call("/diagnose.txt?text=0", raw=True)
seg = d[d.index("[4.5]"):d.index("[5] 通知")]
ok("有真消息时用库里的", "库里的消息" in seg, seg[:600])
ok("频道填错能说成人话", "频道 / 子区 ID 对不上" in seg, seg[:600])
ok("不命中的行写明不命中", "不命中" in seg, seg[:600])
call("/api/rules", {"id": r_off, "name": "频道监听-所有消息", "enabled": 1,
                    "channel_ids": [CID], "action": "notify"})
d = call("/diagnose.txt?text=0", raw=True)
seg = d[d.index("[4.5]"):d.index("[5] 通知")]
ok("ID 改对之后同一条消息就命中了", "会命中" in seg, seg[:600])

print("12. 一条规则都没有时不该崩")
for j in call("/api/state")["rules"]:
    call("/api/rules/%s" % j["id"], method="DELETE")
d = call("/diagnose.txt?text=0", raw=True)
ok("没规则时 [4.5] 说跳过而不是报错", "没有规则，跳过" in d, d[d.find("[4.5]"):][:200])
ok("诊断包整体仍然完整", "[7]" in d, d[-300:])
rid = call("/api/rules", {"name": "t", "enabled": 1, "action": "notify"}).get("id")

call("/api/rules/%d" % rid, method="DELETE")
print("\n%d passed, %d failed" % (P, F))
