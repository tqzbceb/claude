"""v1.7.0 新增能力的回归：触发类型 kinds / 抓历史只入库 / 批量提取 / 自查用的 env 字段。
跑法见 RUN.md。要求 mockllm 在 :8899、server 在 :8777、库是干净的。"""
import json, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8777"
P = F = 0


def call(path, body=None, method=None, raw=False, headers=False):
    req = urllib.request.Request(BASE + path, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            if headers:                       # 只关心响应头（缓存策略）时别去读大 body
                return dict(r.headers)
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


def mk(rule):
    r = call("/api/rules", dict(rule, enabled=1))
    return r.get("id")


def ev(mid, content="hello", kind="msg", chan="700000000000000001", **kw):
    e = {"msg_id": mid, "channel_id": chan, "channel_name": "forum", "author": "Bob",
         "author_id": "900000000000000009", "content": content, "kind": kind,
         "ts": time.time(), "is_bot": False}
    e.update(kw)
    return e


def ingest(msgs, **kw):
    return call("/api/ingest", dict({"messages": msgs, "account": "tester"}, **kw))


# 单独跑时库可能是全新的：先把假模型配上，否则 /api/batch 一次也调不动模型
call("/api/config", {"providers": [{"name": "mock", "base_url": "http://127.0.0.1:8899/v1",
                                   "api_key": "sk-test"}],
                     "default_model": {"provider": "mock", "model": "mock-1"}})

print("1. 触发类型 kinds：新帖和消息是两条独立的路")
rid = mk({"name": "只要新帖", "kinds": ["thread"], "action": "notify", "cooldown_sec": 0})
t = call("/api/rules/test", {"rule": {"name": "只要新帖", "kinds": ["thread"], "cooldown_sec": 0},
                             "sample": ev("x1", "【新帖】有人送套餐", kind="thread")})
ok("只勾新帖时，新帖命中", t.get("match") is True, t)
t = call("/api/rules/test", {"rule": {"name": "只要新帖", "kinds": ["thread"], "cooldown_sec": 0},
                             "sample": ev("x2", "普通聊天", kind="msg")})
ok("只勾新帖时，普通消息不命中", t.get("match") is False, t)
ok("不命中的原因说的是触发类型", "kind" in str(t.get("why", "")).lower() or "类型" in str(t.get("why", "")), t)

t = call("/api/rules/test", {"rule": {"name": "默认", "cooldown_sec": 0},
                             "sample": ev("x3", "普通聊天", kind="msg")})
ok("规则不写 kinds 时默认只听消息", t.get("match") is True, t)
t = call("/api/rules/test", {"rule": {"name": "默认", "cooldown_sec": 0},
                             "sample": ev("x4", "【新帖】xx", kind="thread")})
ok("规则不写 kinds 时新帖不会误触发", t.get("match") is False, t)
t = call("/api/rules/test", {"rule": {"name": "都要", "kinds": ["msg", "thread"], "cooldown_sec": 0},
                             "sample": ev("x5", "【新帖】xx", kind="thread")})
ok("两个都勾时新帖也命中", t.get("match") is True, t)

print("2. 新帖真的能进来并被记成 thread")
ingest([ev("th1", "【新帖】有人送 Claude 套餐", kind="thread", is_thread=True,
           parent_id="700000000000000001")])
time.sleep(1.2)
msgs = call("/api/messages?limit=50").get("messages", [])
th = [m for m in msgs if m.get("msg_id", "").endswith("th1")]
ok("新帖入库了", len(th) == 1, len(th))
ok("入库时 kind 记成 thread", th and th[0].get("kind") == "thread", th[:1])
ok("命中了「只要新帖」那条规则", th and th[0].get("matched"), th[:1])

print("3. 抓历史：只入库，不触发规则、不提醒")
hits_before = [r for r in call("/api/state")["rules"] if r["id"] == rid][0]["hits"]
ingest([ev("h%d" % i, "历史消息 %d 有人送套餐" % i) for i in range(5)], history=True)
time.sleep(1.2)
st = call("/api/state")
hits_after = [r for r in st["rules"] if r["id"] == rid][0]["hits"]
ok("抓历史没让规则命中数增加", hits_after == hits_before, (hits_before, hits_after))
msgs = call("/api/messages?limit=80").get("messages", [])
hs = [m for m in msgs if m.get("msg_id", "").endswith("h3")]
ok("历史消息还是入库了", len(hs) == 1, len(hs))
ok("历史消息标了 scanned", hs and hs[0].get("scanned") in (1, True), hs[:1])
ok("历史消息没被标成命中", hs and not hs[0].get("matched"), hs[:1])

print("4. 批量提取")
r = call("/api/batch", {"want": ""})
ok("没说要提取什么 → 400", r.get("_status") == 400, r)
r = call("/api/batch", {"want": "所有人发的兑换码", "channel_id": "700000000000000099"})
ok("空范围不报错，给出人话提示", r.get("ok") is True and r.get("rows") == [] and r.get("note"), r)

ingest([ev("k1", "兑换码 ABCD-1111 拿去用"), ev("k2", "我这有个 key: ZZZZ-9999"),
        ev("k3", "今天天气不错")], history=True)
time.sleep(1.2)
r = call("/api/batch", {"want": "所有人发出来的兑换码/密钥", "limit": 50, "chunk": 60})
ok("批量提取跑通", r.get("ok") is True, r)
ok("没有批次失败", not r.get("errors"), r.get("errors"))
ok("翻过的条数有报出来", isinstance(r.get("scanned"), int) and r["scanned"] > 0, r.get("scanned"))
ok("调了模型并报出次数", isinstance(r.get("calls"), int) and r["calls"] >= 1, r.get("calls"))
rows = r.get("rows") or []
ok("挑出了东西", len(rows) >= 1, len(rows))
if rows:
    ok("每条都带 verified 标记", all("verified" in x for x in rows), rows[:1])
    ok("每条都能追溯到原消息", all(x.get("msg_id") for x in rows), rows[:1])
    ok("原文里找不到的会被标出来", "unverified" in r, r.keys())
    real = [x for x in rows if x["verified"]]
    ok("verified 的值确实出现在原文里",
       all(x["value"] in x["content"] for x in real), [x for x in real if x["value"] not in x["content"]][:1])
r2 = call("/api/batch", {"want": "兑换码", "limit": 50, "only_matched": True})
ok("只看命中过的也能跑", r2.get("ok") is True, r2)

print("5. 自查用的 env 字段（「我的密钥是不是泄露了」靠它回答）")
e = call("/api/state")["env"]
for k in ("ver", "code_dir", "db_path", "db_exists", "shared_data", "frozen"):
    ok("env 里有 " + k, k in e, e.keys())
# 别硬编码版本号：每次 bump 都会让这条假红。只要求形状对、且和 /version 页一致。
import re as _re
VER = e.get("ver") or ""
ok("版本号形状是 x.y.z", bool(_re.fullmatch(r"\d+\.\d+\.\d+", VER)), VER)
ok("非 exe 时 shared_data 为假", e.get("shared_data") is False, e.get("shared_data"))
d = call("/diagnose.txt?text=0", raw=True)
ok("诊断包里写了代码目录", "代码目录" in d, d[:200])
ok("诊断包里写了数据目录", "数据目录" in d, d[:200])
ok("诊断包不泄露密钥", "sk-" not in d, "有 sk-")

print("6. /version 自证页（界面坏了/被缓存时唯一可信的版本来源，v1.7.1 新增）")
v = call("/version", raw=True)
ok("/version 打得开且是 HTML", "<!doctype html>" in v.lower(), v[:120])
ok("页面上有程序版本", ("v" + VER) in v, VER)
ok("页面上有代码目录", e["code_dir"] in v, e["code_dir"])
ok("页面上有配置文件路径", e["db_path"] in v, e["db_path"])
ok("写了要求的扩展版本", e["ext_min"] in v, e["ext_min"])
ok("不依赖 JS（页面里没有 script）", "<script" not in v.lower(), "有 script")
ok("不泄露密钥", "sk-" not in v, "有 sk-")
ok("解释了版本对不上该看哪一行", "另一个文件夹" in v, v[-400:])
hv = call("/version", raw=True, headers=True)
ok("/version 不给缓存", "no-store" in str(hv).lower(), hv)
hi = call("/", raw=True, headers=True)
ok("界面本身也不给缓存（覆盖新文件后不会还看到旧界面）", "no-store" in str(hi).lower(), hi)

call("/api/rules/%d" % rid, method="DELETE")
print("\n%d passed, %d failed" % (P, F))
