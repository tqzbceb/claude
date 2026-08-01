"""v1.12.0 回归：开服监听（D3）。
覆盖 CRUD / 网址校验 / every_sec 下限钳制 / 首次探测不提醒 / 关→开提醒走转发出口 /
开着再探不重复提醒 / 关方向的提醒默认不开、开了才发 / expect、absent 关键字判定 /
连不上记 err / 删除。
跑法见 RUN.md：echo 在 :8898（/fail 回 500 当「关」，其余 200 当「开」），server 在 :8777，库干净。
背景巡检 watch_loop 也在跑：本套统一用 every_sec=60 + 改完立刻手动 /check，巡检插不进来。"""
import json, os, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8777"
ECHO = "http://127.0.0.1:8898"
JL = "/tmp/bcode/echo.jsonl"
P = F = 0


def http(url, body=None, method=None, timeout=30):
    req = urllib.request.Request(url, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
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


def echo_lines():
    if not os.path.exists(JL):
        return []
    return [json.loads(x) for x in open(JL, encoding="utf-8") if x.strip()]


def hook_n():
    return sum(1 for x in echo_lines() if x["path"].startswith("/wcatch"))


def hook_texts():
    """/wcatch 收到的通知正文（body 是原始请求文本：{"content": "..."}，解开再断言）。"""
    out = []
    for x in echo_lines():
        if not x["path"].startswith("/wcatch"):
            continue
        try:
            out.append(json.loads(x["body"]).get("content", ""))
        except Exception:
            out.append(x["body"])
    return out


def watch(wid=None):
    rows = call("/api/watch")["watch"]
    if wid is None:
        return rows
    return next((w for w in rows if str(w["id"]) == str(wid)), None)


def check(wid):
    r = call(f"/api/watch/{wid}/check", {})
    assert r.get("ok"), r
    return r["watch"]


# 转发出口：通知要落到 echo 上才算数。本机弹窗/提示音关掉，别吵。
call("/api/config", {"sinks": {"toast": False, "sound": False, "hooks": [
    {"name": "记录口", "url": ECHO + "/wcatch", "method": "POST",
     "content": "json", "body": '{"content": "{{text}}"}', "enabled": True}]}})
n0 = hook_n()

print("1. CRUD 与校验")
rows = watch()
ok("一开始是空的", rows == [], rows)
r = call("/api/watch", {"name": "坏", "url": "example.com"})
ok("不带 http:// 被拒绝", r.get("ok") is False and "http" in (r.get("error") or ""), r)
r = call("/api/watch", {"name": "", "url": ECHO + "/fail", "every_sec": 1})
ok("能建", r.get("ok") is True, r)
wid = r["id"]
w = watch(wid)
ok("名字留空用域名凑", w["name"] == "127.0.0.1:8898", w)
ok("every_sec 钳到下限 15", w["every_sec"] == 15, w)
ok("默认开了提醒 / 关了不提醒 / 启用",
   w["notify_open"] == 1 and w["notify_close"] == 0 and w["enabled"] == 1, w)
ok("刚建还没探过", w["state"] == "unknown" and not w["last_check"], w)

print("2. 首次探测只落状态，不提醒")
w = check(wid)
ok("fail 路径探出来是关", w["state"] == "closed" and w["last_status"] == 500, w)
time.sleep(0.3)
ok("首次探测没发通知", hook_n() == n0, {"before": n0, "after": hook_n()})

print("3. 关→开：提醒走转发出口")
r = call("/api/watch", {"id": wid, "url": ECHO + "/open", "every_sec": 60})
ok("改网址成功", r.get("ok") is True, r)
w = check(wid)
ok("翻成开", w["state"] == "open" and w["last_status"] == 200, w)
ok("last_change 落下来了", bool(w["last_change"]), w)
time.sleep(0.3)
new = hook_texts()[n0:]
ok("出口收到一条「开了」", len(new) == 1 and "开了" in new[0] and "/open" in new[0],
   [x[:120] for x in new])

print("4. 开着再探不重复提醒")
w = check(wid)
ok("还是开", w["state"] == "open", w)
time.sleep(0.3)
ok("没有新的出口记录", hook_n() == n0 + 1, hook_n())

print("5. 开→关：默认不提醒；开了开关提醒才发")
w = check(call("/api/watch", {"id": wid, "url": ECHO + "/fail"})["id"])
ok("翻回关", w["state"] == "closed", w)
time.sleep(0.3)
ok("关方向默认没发通知", hook_n() == n0 + 1, hook_n())
call("/api/watch", {"id": wid, "notify_close": True})
check(wid)                                   # 还在关，没变，不发
call("/api/watch", {"id": wid, "url": ECHO + "/open"})
check(wid)                                   # 关→开，发一条（第 2 条）
call("/api/watch", {"id": wid, "url": ECHO + "/fail"})
check(wid)                                   # 开→关，这次该发（第 3 条）
time.sleep(0.3)
bodies = hook_texts()
ok("开了开关提醒后，关也发出来了", len(bodies) == n0 + 3 and "关了" in bodies[-1],
   [b[:100] for b in bodies[n0:]])

print("6. 关键字判定：expect / absent")
r = call("/api/watch", {"name": "判开", "url": ECHO + "/open",
                        "expect": "页面里根本没有的这串字", "every_sec": 60})
wid2 = r["id"]
w = check(wid2)
ok("200 但不含 expect 串 = 关", w["state"] == "closed" and w["last_status"] == 200, w)
call("/api/watch", {"id": wid2, "expect": "", "absent": '"ok"'})
# 上一探是 closed（首次），这探还是 closed：方向没变，不该发通知
before = hook_n()
w = check(wid2)
ok("absent 命中 = 关（哪怕 200）", w["state"] == "closed", w)
call("/api/watch", {"id": wid2, "absent": "根本不存在的字"})
w = check(wid2)
ok("absent 不命中 = 开", w["state"] == "open", w)
time.sleep(0.3)
ok("关→开这条也发了（第 4 条）", hook_n() == n0 + 4, hook_n() - before)

print("7. 连不上 = 关，err 留痕")
r = call("/api/watch", {"name": "死的", "url": "http://127.0.0.1:9/none", "every_sec": 60})
wid3 = r["id"]
w = check(wid3)
ok("连不上探出来是关", w["state"] == "closed" and w["last_status"] == 0, w)
ok("err 写了人话", bool(w["err"]), w)

print("8. 停用与删除")
r = call("/api/watch", {"id": wid2, "enabled": False})
ok("能停用", r["watch"]["enabled"] == 0, r)
call(f"/api/watch/{wid2}", None, method="DELETE")
call(f"/api/watch/{wid}", None, method="DELETE")
call(f"/api/watch/{wid3}", None, method="DELETE")
ok("删干净了", watch() == [], watch())

print(f"\n通过 {P} / 失败 {F}")
raise SystemExit(1 if F else 0)
