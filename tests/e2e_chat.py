"""A1+C1 回归：AI 工作台聊天持久化 + 多会话。
覆盖 空库 / 新建会话 / ask 不带 sid 自动建会话并顶名 / open 取回消息 / 带 sid 追加 /
多会话各自独立 / rename（含空名 400、超长截 40）/ del（含删当前 cur 清零）/
plain=1 探针不进库 / 调用失败不进库 / open 不存在 404 / 长输入截 4000。
跑法见 RUN.md：mockllm 在 :8899，server 在 :8777，库干净。"""
import json, urllib.request, urllib.error

BASE = "http://127.0.0.1:8777"
MOCK = "http://127.0.0.1:8899"
P = F = 0


def http(url, body=None, method=None, raw=False, timeout=60):
    req = urllib.request.Request(url, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            t = r.read().decode()
            return t if raw else json.loads(t)
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode()[:300]}


def call(path, body=None, **kw):
    return http(BASE + path, body, **kw)


def ok(name, cond, extra=""):
    global P, F
    if cond:
        P += 1; print("  ok  ", name)
    else:
        F += 1; print("  FAIL", name, str(extra)[:400])


def sessions():
    r = call("/api/wb/sessions")
    return r["sessions"], r["cur"]


def ask(prompt, **kw):
    return call("/api/ask", dict({"prompt": prompt}, **kw))


# 工作台走默认假模型（返回固定文本即可，不断言模型说了什么）
call("/api/config", {"providers": [{"name": "mock", "base_url": "http://127.0.0.1:8899/v1",
                                    "api_key": "sk-test"}],
                     "default_model": {"provider": "mock", "model": "mock-1"}})
http(MOCK + "/__reset", {})

print("1. 空库：没有会话，cur=0")
ss, cur = sessions()
ok("接口 ok", isinstance(ss, list))
ok("sessions 是空的", ss == [], ss)
ok("cur=0", cur == 0, cur)

print("2. 新建一个会话")
sid1 = call("/api/wb/session/new", {})["id"]
ss, cur = sessions()
ok("列表多了一条", len(ss) == 1, ss)
ok("名字叫 新会话", ss[0]["name"] == "新会话", ss)
ok("消息数 0", ss[0]["n"] == 0, ss)
ok("cur 指向它", cur == sid1, (cur, sid1))

print("3. ask 不带 sid：自动建会话、名字顶成第一句话")
r = ask("第一句话测试")
ok("ask 成功", r.get("ok"), r)
ss, cur = sessions()
ok("现在有两个会话", len(ss) == 2, ss)
mine = next((s for s in ss if s["id"] == cur), None)
ok("cur 指向新会话", mine is not None and mine["id"] != sid1, (ss, cur))
ok("名字被顶成第一句话", mine and mine["name"] == "第一句话测试", mine)
ok("一问一答 n=2", mine and mine["n"] == 2, mine)

print("4. open 拿回那两条消息")
r = call("/api/wb/session/open", {"id": cur})
ok("两条", len(r.get("msgs") or []) == 2, r)
m = r["msgs"]
ok("第 1 条是用户说的原话", m[0]["r"] == "u" and m[0]["t"] == "第一句话测试", m)
ok("第 2 条是模型回的", m[1]["r"] == "a" and bool(m[1]["t"]), m)
ok("acts 都是空数组", m[0]["acts"] == [] and m[1]["acts"] == [], m)

print("5. 带 sid 再发一条：追加进同一个会话")
r = ask("第二句", sid=cur)
ok("ask 成功", r.get("ok"), r)
ss, _ = sessions()
ok("n=4", next(s for s in ss if s["id"] == cur)["n"] == 4, ss)
r = call("/api/wb/session/open", {"id": cur})
ok("拿回 4 条", len(r["msgs"]) == 4, r)
ok("顺序 u,a,u,a", [x["r"] for x in r["msgs"]] == ["u", "a", "u", "a"], r["msgs"])

print("6. 第二个会话发一条：两个会话各自独立")
sid2 = call("/api/wb/session/new", {})["id"]
r = ask("另一个话题", sid=sid2)
ok("ask 成功", r.get("ok"), r)
ss, cur2 = sessions()
ok("三个会话", len(ss) == 3, ss)
ok("第二个会话 n=2 且顶名", any(s["id"] == sid2 and s["n"] == 2 and s["name"] == "另一个话题"
                               for s in ss), ss)
r = call("/api/wb/session/open", {"id": cur})
ok("第一个会话还是 4 条", len(r["msgs"]) == 4, r)

print("7. rename：能改、空名 400、超长截 40")
r = call("/api/wb/session/rename", {"id": sid2, "name": "bbb"})
ok("改名成功", r.get("ok"), r)
ss, _ = sessions()
ok("列表里看得到 bbb", any(s["name"] == "bbb" for s in ss), ss)
r = call("/api/wb/session/rename", {"id": sid2, "name": "  "})
ok("空名 400", r.get("_status") == 400, r)
r = call("/api/wb/session/rename", {"id": sid2, "name": "长" * 60})
ss, _ = sessions()
ok("名字被截到 40 字", any(s["id"] == sid2 and len(s["name"]) == 40 for s in ss), ss)

print("8. del：删第二个会话（先 open 切成当前，再删，cur 要清零）")
call("/api/wb/session/open", {"id": sid2})
r = call("/api/wb/session/del", {"id": sid2})
ok("删除成功", r.get("ok"), r)
ss, cur3 = sessions()
ok("列表少一条", len(ss) == 2, ss)
r = call("/api/wb/session/open", {"id": sid2})
ok("再 open 它 404", r.get("_status") == 404, r)
ok("删的是当前会话，cur 清零", cur3 == 0, cur3)

print("9. plain=1 探针不进库")
r = ask("测试一次调用", plain=1)
ok("plain 调用成功", r.get("ok"), r)
ss, _ = sessions()
ok("没建新会话", len(ss) == 2, ss)
ok("总消息数没涨", sum(s["n"] for s in ss) == 4, ss)

print("10. 调用失败不进库")
# 注意：provider 名写错会被 server 兜底到第一个 provider，所以失败得靠 mock 真回 500
http(MOCK + "/__script", {"queue": [{"http": {"status": 500, "body": "boom"}}]})
r = ask("这句会失败")
ok("后端报错", not r.get("ok"), r)
ss, _ = sessions()
ok("没建新会话", len(ss) == 2, ss)
ok("总消息数没涨", sum(s["n"] for s in ss) == 4, ss)

print("11. open 不存在的 id")
r = call("/api/wb/session/open", {"id": 999999})
ok("404", r.get("_status") == 404, r)

print("12. 长输入截断到 4000")
r = ask("字" * 5000)
ok("ask 成功", r.get("ok"), r)
ss, cur4 = sessions()
ok("又建了一个会话", len(ss) == 3, ss)
r = call("/api/wb/session/open", {"id": cur4})
ok("库里那条 ≤4000 字", 0 < len(r["msgs"][0]["t"]) <= 4000, len(r["msgs"][0]["t"]))

print(f"\n== e2e_chat: {P} 过 / {F} 挂")
raise SystemExit(1 if F else 0)
