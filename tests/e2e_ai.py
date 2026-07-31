"""AI 动作路径回归：ai_tag 打分 + 门槛、ai_extract 入库、ai_summary 攒够才调、
ai_reply 在浏览器模式下要明确报错、工作台问答、每日调用上限、webhook 动作。
前置：mockllm.py(:8899) + echo.py(:8898) + server.py(:8777, 新库)。"""
import json, time, urllib.request

B = "http://127.0.0.1:8777"
M = "http://127.0.0.1:8899"
ok = fail = 0


def call(path, data=None, method=None, base=B):
    req = urllib.request.Request(base + path, method=method or ("POST" if data is not None else "GET"),
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name} {extra}")


def mkmsg(mid, content, **kw):
    m = {"msg_id": mid, "guild_id": "700000000000000001", "channel_id": "800000000000000001",
         "channel_name": "bug-report", "author_id": "900000000000000001", "author": "Marcus",
         "content": content}
    m.update(kw)
    return m


def send(*msgs):
    return call("/api/ingest", {"messages": list(msgs)})


def rules_clear():
    for r in call("/api/state")["rules"]:
        call(f"/api/rules/{r['id']}", method="DELETE")


print("0. 准备：假模型 + 出口 + 清空规则")
call("/api/config", {"providers": [{"name": "mock", "base_url": "http://127.0.0.1:8899/v1",
                                    "api_key": "sk-test"}],
                     "default_model": {"provider": "mock", "model": "mock-1"},
                     "discord": {"mode": "browser", "enabled": False, "token": ""},
                     "sinks": {"toast": False, "sound": False, "min_score": 0, "hooks": [
                         {"id": "h1", "name": "转发", "url": "http://127.0.0.1:8898/hook",
                          "content": "json", "body": '{"text": "{{text}}", "score": "{{score}}"}'}]}})
rules_clear()
call("/api/messages/clear", {})
call("/__reset", {}, base=M)
open("/tmp/bcode/echo.jsonl", "w").close()

print("1. ai_tag：打分入库 + 标签 + 待办")
rid = call("/api/rules", {"name": "打分", "action": "ai_tag", "keywords_any": ["紧急"],
                          "cooldown_sec": 0, "enabled": 1})["id"]
send(mkmsg("7001", "紧急：登录全挂了 @我"))
time.sleep(1.2)
row = call("/api/messages?limit=5")["messages"][0]
chk("分数写进库", row.get("score") == 88, row.get("score"))
chk("标签/理由/待办都在", (row.get("ai") or {}).get("tags") == ["需回复", "排期"]
    and (row["ai"] or {}).get("todo"), row.get("ai"))
chk("命中规则名记上", "打分" in (row.get("matched") or ""), row.get("matched"))
kinds = [c["kind"] for c in call("/__calls", base=M)["calls"]]
chk("只调了一次模型，且是打分任务", kinds == ["tag"], kinds)

print("2. sinks.min_score 门槛：低分不往外发")
call("/api/config", {"sinks": {"min_score": 60}})
call("/__reset", {}, base=M)
open("/tmp/bcode/echo.jsonl", "w").close()
send(mkmsg("7002", "紧急但不重要的事"))      # 假模型给 20 分（内容里没有 @）
time.sleep(1.2)
lines = open("/tmp/bcode/echo.jsonl").read().strip()
chk("20 分的消息被门槛挡住不转发", lines == "", lines[:120])
send(mkmsg("7003", "紧急 @我 看一下"))        # 有 @ → 88 分
time.sleep(1.2)
lines = [l for l in open("/tmp/bcode/echo.jsonl") if l.strip()]
chk("88 分的消息正常转发", len(lines) == 1, len(lines))
body = json.loads(json.loads(lines[0])["body"])          # echo.jsonl 里 body 是字符串，得再解一层
chk("转发里带上了分数", body.get("score") == "88", body)
chk("正文渲染成人能读的样子", "重要度 88" in body.get("text", ""), body.get("text"))
call("/api/config", {"sinks": {"min_score": 0}})

print("3. ai_extract：抽出的 JSON 存进消息")
rules_clear()
call("/api/rules", {"name": "抽字段", "action": "ai_extract", "keywords_any": ["redis"],
                    "cooldown_sec": 0, "enabled": 1})
call("/__reset", {}, base=M)
send(mkmsg("7101", "登录 500，redis connection refused"))
time.sleep(1.2)
row = call("/api/messages?limit=5")["messages"][0]
chk("模型输出前面带废话也能抽出 JSON", (row.get("ai") or {}).get("错误码") == "500", row.get("ai"))
chk("抽取用了 json_mode", call("/__calls", base=M)["calls"][0]["json_mode"] is True)

print("4. ai_summary：攒够 N 条才调一次")
rules_clear()
call("/api/rules", {"name": "摘要", "action": "ai_summary", "summary_every": 3,
                    "keywords_any": ["水群"], "cooldown_sec": 0, "enabled": 1})
call("/__reset", {}, base=M)
send(mkmsg("7201", "水群 1"), mkmsg("7202", "水群 2"))
time.sleep(1.0)
chk("只攒 2 条时不调模型", call("/__calls", base=M)["calls"] == [], call("/__calls", base=M)["calls"])
send(mkmsg("7203", "水群 3"))
time.sleep(1.2)
kinds = [c["kind"] for c in call("/__calls", base=M)["calls"]]
chk("第 3 条触发一次摘要", kinds == ["summary"], kinds)
send(mkmsg("7204", "水群 4"))
time.sleep(1.0)
kinds = [c["kind"] for c in call("/__calls", base=M)["calls"]]
chk("摘要后缓冲清零，第 4 条不再调", kinds == ["summary"], kinds)

print("5. ai_reply / 手动回复：浏览器模式下必须明确报错，不能假装成功")
rules_clear()
r = call("/api/reply", {"channel_id": "800000000000000001", "content": "试着回一句"})
chk("手动回复被拒绝", r.get("ok") is False, r)
chk("错误信息说清要 Token", any(k in str(r.get("error", "")) for k in ("Token", "token", "Bot")), r)

print("6. 工作台问答 /api/ask 带消息上下文")
ids = [m["id"] for m in call("/api/messages?limit=3")["messages"]]
call("/__reset", {}, base=M)
r = call("/api/ask", {"prompt": "这几条里哪些今天必须回", "msg_ids": ids})
chk("拿到回答", r.get("ok") and r.get("text"), r)
c = call("/__calls", base=M)["calls"]
chk("上下文真的传给了模型", c and "Marcus" in c[0]["user"], c[0]["user"][:120] if c else None)

print("7. 每日调用上限：到顶了要拦住并说清楚")
call("/api/config", {"ai_daily_call_cap": 1})
r = call("/api/ask", {"prompt": "再问一次"})
chk("超上限被拦", r.get("ok") is False, r)
chk("错误信息提到上限", "上限" in str(r.get("error", "")), r)
call("/api/config", {"ai_daily_call_cap": 500})
r = call("/api/ask", {"prompt": "放开后能问"})
chk("调回上限后恢复", r.get("ok") is True, r)

print("8. webhook 动作：规则自带地址")
rules_clear()
open("/tmp/bcode/echo.jsonl", "w").close()
call("/api/rules", {"name": "转发规则", "action": "webhook", "keywords_any": ["转我"],
                    "webhook_url": "http://127.0.0.1:8898/rulehook", "cooldown_sec": 0, "enabled": 1})
send(mkmsg("7301", "这条转我一份"))
time.sleep(1.2)
paths = [json.loads(l)["path"] for l in open("/tmp/bcode/echo.jsonl") if l.strip()]
chk("规则里的 webhook 收到了", "/rulehook" in paths, paths)

print("9. 冷却：cooldown_sec 内的第二条不再调模型")
rules_clear()
call("/api/rules", {"name": "带冷却", "action": "ai_tag", "keywords_any": ["冷却"],
                    "cooldown_sec": 60, "enabled": 1})
call("/__reset", {}, base=M)
send(mkmsg("7401", "冷却测试 紧急 @我"))
time.sleep(1.0)
send(mkmsg("7402", "冷却测试 紧急 @我 第二条"))
time.sleep(1.0)
kinds = [c["kind"] for c in call("/__calls", base=M)["calls"]]
chk("60 秒内只调了一次", kinds == ["tag"], kinds)
logs = call("/api/logs").get("logs") or []
chk("日志写明被冷却跳过", any("冷却" in str(l.get("text", "")) for l in logs[:20]),
    [l.get("text") for l in logs[:3]])

print("10. webhook 动作的两种异常：地址留空 / 对方报错，都必须在日志里看得见")
rules_clear()
call("/api/rules", {"name": "空地址", "action": "webhook", "keywords_any": ["空地址"],
                    "webhook_url": "", "cooldown_sec": 0, "enabled": 1})
send(mkmsg("7501", "空地址 这条"))
time.sleep(1.0)
logs = call("/api/logs").get("logs") or []
hit = [l for l in logs[:10] if "没填地址" in str(l.get("text", ""))]
chk("地址留空不再静默，日志有告警", bool(hit), [l.get("text") for l in logs[:3]])
chk("告警里告诉你怎么办", hit and "通知与转发" in hit[0]["text"], hit[0]["text"][:80] if hit else None)
rules_clear()
call("/api/rules", {"name": "对方报错", "action": "webhook", "keywords_any": ["报错端"],
                    "webhook_url": "http://127.0.0.1:8898/failhook", "cooldown_sec": 0, "enabled": 1})
send(mkmsg("7502", "报错端 这条"))
time.sleep(1.2)
logs = call("/api/logs").get("logs") or []
chk("对方返 500 时日志有 error", any("500" in str(l.get("text", "")) and l.get("level") == "error"
                                    for l in logs[:10]), [l.get("text") for l in logs[:3]])
msgs = call("/api/messages?limit=3")["messages"]
chk("发失败不影响消息入库", msgs and msgs[0]["content"].startswith("报错端"), msgs[0] if msgs else None)

print(f"\n通过 {ok} / 失败 {fail}")
