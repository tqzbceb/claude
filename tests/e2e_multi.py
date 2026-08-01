#!/usr/bin/env python3
"""多浏览器 / 多账号 / 跨桥去重 的回归。跑法同 e2e.py（单独跑，会清库清规则）。"""
import json, time, urllib.request

B = "http://127.0.0.1:8777"
ok = bad = 0


def call(p, data=None, method=None):
    r = urllib.request.Request(B + p, method=method or ("POST" if data is not None else "GET"),
                               data=json.dumps(data).encode() if data is not None else None,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=20) as f:
        return json.loads(f.read().decode())


def t(name, cond, extra=""):
    global ok, bad
    if cond:
        ok += 1; print("  ok  ", name)
    else:
        bad += 1; print("  FAIL", name, extra)


def sec(s): print(s)


def ingest(bridge, account, msgs, **kw):
    b = {"bridge": bridge, "account": account, "ver": "1.4.0", "browser": "Chrome 140",
         "messages": msgs}
    b.update(kw)
    return call("/api/ingest", b)


def bid_(mid):
    return "b" + mid          # 浏览器来源入库时会加 b 前缀，避免和 Token 直连的 id 撞


def msg(mid, content="随便一条", author="Zoe", ch="general"):
    return {"msg_id": mid, "guild_id": "g1", "channel_id": "c1", "channel_name": ch,
            "author_id": "u1", "author": author, "content": content, "ts": time.time(),
            "is_bot": False, "is_dm": False, "mentions_me": False}


# 干净起点
call("/api/messages/clear", {})
for r in call("/api/state")["rules"]:
    call(f"/api/rules/{r['id']}", method="DELETE")

# A7：收信闸落地后，没规则罩着的消息一律不收。本套件只关心桥/账号/去重，
# 放一条「罩得住一切但永远不提醒」的兜底规则，让消息照常见库。
call("/api/rules", {"name": "A7测试兜底", "enabled": 1, "kinds": ["msg", "thread"],
                    "ignore_bots": False, "keywords_any": ["∅绝不出现∅"], "action": "notify",
                    "cooldown_sec": 0})

sec("1. 心跳带身份：桥被单独列出来")
call("/api/ingest", {"ping": True, "bridge": "brA", "account": "alice#1",
                     "account_id": "111", "ver": "1.4.0", "browser": "Chrome 140",
                     "where": "general"})
st = call("/api/state")["status"]["browser"]
bl = {x["id"]: x for x in st["bridges"]}
t("桥出现在列表里", "brA" in bl, st)
t("认出账号", bl.get("brA", {}).get("account") == "alice#1")
t("认出浏览器", bl.get("brA", {}).get("browser") == "Chrome 140")
t("认出扩展版本", bl.get("brA", {}).get("ver") == "1.4.0")
t("认出在盯哪个频道", bl.get("brA", {}).get("where") == "general")
t("标成新鲜", bl.get("brA", {}).get("fresh") is True)
t("ping 回包带服务端版本", "server_ver" in call("/api/ingest", {"ping": True, "bridge": "brA"}))

sec("2. 第二个浏览器 / 第二个账号：分开列，不互相覆盖")
call("/api/ingest", {"ping": True, "bridge": "brB", "account": "bob#2", "ver": "1.4.0",
                     "browser": "Edge", "where": "dev"})
st = call("/api/state")["status"]["browser"]
bl = {x["id"]: x for x in st["bridges"]}
t("两个桥都在", len(bl) >= 2, list(bl))
t("A 的账号没被 B 冲掉", bl["brA"]["account"] == "alice#1")
t("B 的账号独立", bl["brB"]["account"] == "bob#2")
t("两个都算 live", bl["brA"]["fresh"] and bl["brB"]["fresh"])
t("账号列表给界面用", {"alice#1", "bob#2"} <= set(st["accounts"]), st["accounts"])

sec("3. 旧版扩展也能被看见（界面据此提示更新）")
call("/api/ingest", {"ping": True, "bridge": "brOld", "account": "carol#3", "ver": "1.2.0"})
bl = {x["id"]: x for x in call("/api/state")["status"]["browser"]["bridges"]}
t("报出旧版本号", bl["brOld"]["ver"] == "1.2.0")

sec("4. 跨桥去重：同一条消息两个浏览器各报一次，只算一条")
r1 = ingest("brA", "alice#1", [msg("dup1", "@我 看下这个")])
r2 = ingest("brB", "bob#2", [msg("dup1", "@我 看下这个")])
t("两次都接受了请求", r1["ok"] and r2["ok"])
ms = [m for m in call("/api/messages?limit=50")["messages"] if m["msg_id"] == bid_("dup1")]
t("库里只有一条", len(ms) == 1, len(ms))
t("记下了是哪个号收到的", ms and ms[0].get("account") == "alice#1", ms[0] if ms else None)

sec("5. 规则命中不会因为两个浏览器而翻倍")
rule = {"name": "多桥去重测试", "keywords_any": ["翻倍"], "action": "notify",
        "ignore_bots": False, "cooldown_sec": 0}
rid = [r for r in call("/api/rules", dict(rule, enabled=1))["rules"] if r["name"] == "多桥去重测试"][0]["id"]
ingest("brA", "alice#1", [msg("dup2", "这条会不会翻倍")])
ingest("brB", "bob#2", [msg("dup2", "这条会不会翻倍")])
hits = [r for r in call("/api/state")["rules"] if r["id"] == rid][0]["hits"]
t("命中只加了 1", hits == 1, hits)

sec("6. 规则按账号分流：accounts 只放 alice")
r2 = {"name": "只听alice", "keywords_any": ["分流"], "action": "notify",
      "accounts": ["alice#1"], "ignore_bots": False, "cooldown_sec": 0}
rid2 = [r for r in call("/api/rules", dict(r2, enabled=1))["rules"] if r["name"] == "只听alice"][0]["id"]
t("accounts 存下来了",
  [r for r in call("/api/state")["rules"] if r["id"] == rid2][0].get("accounts") == ["alice#1"])
ingest("brA", "alice#1", [msg("acc1", "分流测试 A")])
ingest("brB", "bob#2", [msg("acc2", "分流测试 B")])
time.sleep(0.4)
ms = {m["msg_id"].lstrip("b"): m for m in call("/api/messages?limit=50")["messages"]}
t("alice 的命中", "只听alice" in (ms.get("acc1", {}).get("matched") or ""), ms.get("acc1"))
t("bob 的不命中", "只听alice" not in (ms.get("acc2", {}).get("matched") or ""), ms.get("acc2"))
hits2 = [r for r in call("/api/state")["rules"] if r["id"] == rid2][0]["hits"]
t("命中数只算 alice 那条", hits2 == 1, hits2)

sec("7. 试算也讲得清 account 不匹配")
res = call("/api/rules/test", {"rule": dict(r2, accounts=["someone-else"]),
                               "sample": {"content": "分流测试", "author": "Zoe",
                                          "account": "alice#1", "is_bot": False}})
t("试算返回 account 原因", res.get("why") == "account" or res.get("ok") is False, res)

sec("8. 单条消息自带 account 优先于整批")
ingest("brA", "alice#1", [dict(msg("mix1", "混号"), account="dave#9")])
ms = {m["msg_id"].lstrip("b"): m for m in call("/api/messages?limit=50")["messages"]}
t("用的是单条上的账号", ms.get("mix1", {}).get("account") == "dave#9", ms.get("mix1"))

sec("9. 旁听开关关掉时，桥仍然可见并带错误说明")
call("/api/source/toggle", {"source": "browser", "on": False})
r = ingest("brA", "alice#1", [msg("off1", "关了还发")])
t("请求被拒", r.get("ok") is False)
bl = {x["id"]: x for x in call("/api/state")["status"]["browser"]["bridges"]}
t("桥还在（不然界面会说没装扩展）", "brA" in bl)
t("带上原因", "丢弃" in (bl["brA"].get("err") or ""), bl["brA"].get("err"))
call("/api/source/toggle", {"source": "browser", "on": True})

for rid_ in (rid, rid2):
    call(f"/api/rules/{rid_}", method="DELETE")
print(f"\n通过 {ok} / 失败 {bad}")
