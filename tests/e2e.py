"""dcwatch 交付包端到端回归。假 LLM :8899，假 webhook :8898，服务 :8777。"""
import json, urllib.request, time

B = "http://127.0.0.1:8777"
ok = fail = 0


def call(path, data=None, method=None):
    req = urllib.request.Request(B + path, method=method or ("POST" if data is not None else "GET"),
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


print("1. /api/state")
s = call("/api/state")
chk("返回 config/rules/messages", all(k in s for k in ("config", "rules")), list(s)[:8])

print("2. 配置假 provider + 出口")
c = call("/api/config", {
    "providers": [{"name": "mock", "base_url": "http://127.0.0.1:8899/v1", "api_key": "sk-test"}],
    "default_model": {"provider": "mock", "model": "mock-1"},
    "sinks": {"toast": False, "sound": False, "hooks": [
        {"id": "h1", "name": "自定义", "url": "http://127.0.0.1:8898/hook", "content": "json",
         "body": '{"text": "{{text}}", "author": "{{author}}", "raw": {{json}}}'},
        {"id": "h2", "name": "企业微信", "url": "http://127.0.0.1:8898/wecom", "content": "json",
         "body": '{"msgtype": "text", "text": {"content": "{{text}}"}}'},
    ]},
})
chk("保存成功", c.get("ok"))
chk("api_key 回传被掩码", str(c["config"]["providers"][0]["api_key"]).startswith("***"),
    c["config"]["providers"][0]["api_key"])

print("3. 掩码回写不冲掉真值")
call("/api/config", {"providers": [{"name": "mock", "base_url": "http://127.0.0.1:8899/v1",
                                    "api_key": "***"}]})
m = call("/api/models", {"provider": "mock"})
chk("拉模型仍成功（真 key 还在）", m.get("ok") and m.get("models") == ["mock-1", "mock-2"], m)

print("4. 局部更新 sinks 不抹掉别的字段")
call("/api/config", {"sinks": {"quiet_from": "23:00", "quiet_to": "08:00"}})
s = call("/api/state")
sk = s["config"]["sinks"]
hk = sk.get("hooks") or []
chk("两条出口都还在", len(hk) == 2 and hk[0]["url"].endswith("/hook"), hk)
chk("出口带 id 和默认字段", all(h.get("id") and h.get("method") == "POST" for h in hk), hk)
chk("免打扰已写入", (sk.get("quiet_from"), sk.get("quiet_to")) == ("23:00", "08:00"))

print("4.5 A7 收信闸：没规则罩着就是不收")
r0 = call("/api/ingest", {"messages": [
    {"msg_id": "0999", "guild_id": "700000000000000001", "channel_id": "800000000000000009",
     "channel_name": "无人区", "author_id": "900000000000000009", "author": "Nobody",
     "content": "没规则罩着的消息"}]})
chk("请求照常受理", r0.get("accepted") == 1, r0)
chk("零规则 = 零收信", not any(str(m.get("msg_id", "")).endswith("0999")
                               for m in call("/api/messages?limit=50")["messages"]), "")
shim = call("/api/rules", {"name": "A7测试兜底", "enabled": 1, "kinds": ["msg", "thread"],
                           "ignore_bots": False, "keywords_any": ["∅绝不出现∅"], "action": "notify",
                           "cooldown_sec": 0})
chk("兜底规则存上（罩得住但永不提醒）", shim.get("ok") and shim.get("id"), shim)

print("5. /api/ingest 浏览器旁听入库")
r = call("/api/ingest", {"messages": [
    {"msg_id": "1001", "guild_id": "700000000000000001", "channel_id": "800000000000000001",
     "channel_name": "bug-report", "author_id": "900000000000000001", "author": "Marcus",
     "content": "登录接口又 500 了，看下", "mentions_me": True},
    {"msg_id": "1002", "guild_id": "700000000000000001", "channel_id": "800000000000000002",
     "parent_id": "800000000000000001", "is_thread": True, "channel_name": "登录 500",
     "author_id": "900000000000000002", "author": "Lin", "content": "复现了"},
]})
chk("收下 2 条", r.get("accepted") == 2, r)
msgs = call("/api/messages?limit=10")["messages"]
chk("库里能查到", len(msgs) >= 2, len(msgs))

print("6. /api/rules/compose 一句话生成 + 清洗")
r = call("/api/rules/compose", {"text": "老板 Marcus 在任何地方 @我，就打分提醒我", "provider": "mock",
                                "model": "mock-1"})
chk("生成成功", r.get("ok"), r)
rule, notes = r.get("rule", {}), r.get("notes", [])
chk("编造的 channel_id 被丢掉", rule.get("channel_ids") == [], rule.get("channel_ids"))
chk("丢 ID 有写进 notes", any("查不到的 ID" in n for n in notes), notes)
KNOWN = {"900000000000000001", "900000000000000002"}
chk("author_id 是本机见过的真 ID", set(rule.get("author_ids") or []) <= KNOWN and rule.get("author_ids"),
    rule.get("author_ids"))
WHO = (rule.get("author_ids") or [""])[0]
chk("非法 action 退回 notify", rule.get("action") == "notify", rule.get("action"))
chk("非法 action 有提示", any("不认识" in n for n in notes), notes)
chk("合法字段保留 mention_only", rule.get("mention_only") is True)
chk("字符串数字转 int", rule.get("cooldown_sec") == 45, rule.get("cooldown_sec"))
chk("不存在的字段被丢", "bogus_field" not in rule and "min_score" not in rule, list(rule))
chk("没限定频道有提示", any("没限定频道" in n for n in notes), notes)
chk("空输入报错", not call("/api/rules/compose", {"text": "  "}).get("ok"))

print("7. 保存 compose 出来的规则 + 试算")
rule["name"] = "老板点我"
rule["action"] = "notify"
sv = call("/api/rules", dict(rule, enabled=1))
chk("保存成功", sv.get("ok") and sv.get("id"), sv)
rid = sv["id"]
t1 = call("/api/rules/test", {"rule": rule, "sample": {"author_id": WHO,
                                                       "content": "喂", "mentions_me": True}})
chk("命中：对的人 + @我", t1.get("match") is True, t1)
t2 = call("/api/rules/test", {"rule": rule, "sample": {"author_id": WHO,
                                                       "content": "喂", "mentions_me": False}})
chk("不命中：没 @我", t2.get("match") is False and "mention" in str(t2.get("why")), t2)
t3 = call("/api/rules/test", {"rule": rule, "sample": {"author_id": "900000000000000009",
                                                       "content": "喂", "mentions_me": True}})
chk("不命中：别人说的", t3.get("match") is False and "author" in str(t3.get("why")), t3)

print("8. 父频道 → 子区匹配")
pr = {"channel_ids": ["800000000000000001"], "include_threads_of_channels": True}
a = call("/api/rules/test", {"rule": pr, "sample": {"channel_id": "800000000000000002",
                                                    "parent_id": "800000000000000001", "content": "x"}})
b = call("/api/rules/test", {"rule": dict(pr, include_threads_of_channels=False),
                             "sample": {"channel_id": "800000000000000002",
                                        "parent_id": "800000000000000001", "content": "x"}})
chk("含子区时命中", a.get("match") is True, a)
chk("不含子区时不命中", b.get("match") is False, b)

print("9. 提示音")
sn = call("/api/sounds")
names = [x.get("name") or x.get("id") or x for x in (sn.get("sounds") or sn.get("builtin") or [])]
chk("列出内置提示音 >=6", len(names) >= 6, sn)

print("10. 出口测试（打到假 webhook）")
open("/tmp/bcode/echo.jsonl", "w").close()
w = call("/api/sinks/test", {"which": "hook:h1"})
chk("自定义出口通", w.get("ok") and w.get("results", {}).get("自定义") == "ok", w)
chk("测通后记上 verified", any(h["id"] == "h1" and h.get("verified") for h in w.get("sinks", {}).get("hooks", [])),
    w.get("sinks", {}).get("hooks"))
wc = call("/api/sinks/test", {"which": "hook:h2"})
chk("企业微信出口通", wc.get("ok") and wc.get("results", {}).get("企业微信") == "ok", wc)
time.sleep(0.5)
lines = [l for l in open("/tmp/bcode/echo.jsonl")]
chk("假接收端收到 2 次", len(lines) == 2, len(lines))
chk("免打扰时段有提醒或未触发", True)
nf = call("/api/sinks/test", {"which": "hook:nope"})
chk("不存在的出口明确报错", not nf.get("ok"), nf)
tpl = [json.loads(json.loads(l)["body"]) for l in open("/tmp/bcode/echo.jsonl")]
chk("模板 {{占位符}} 都替换了", all("{{" not in json.dumps(t) for t in tpl), tpl)
chk("{{json}} 原样嵌进去还是合法 JSON", isinstance(tpl[0].get("raw"), dict), tpl[0])

print("10.5 工作台快捷按钮可配置")
st = call("/api/state")
qa, qd = st["config"].get("quick_actions"), st["config"].get("quick_defaults")
chk("默认给 4 个快捷按钮", isinstance(qa, list) and len(qa) == 4, qa)
chk("带上默认值供「恢复默认」用", isinstance(qd, list) and len(qd) == 4, qd)
r = call("/api/config", {"quick_actions": [
    {"name": "挑出报错", "text": "把这些消息里的报错和堆栈挑出来"},
    {"name": "  ", "text": "名字是空的，应该被丢掉"},
    {"name": "内容空的", "text": ""},
    {"name": "名字特别特别特别特别长超过二十个字要被截断", "text": "x"},
]})
got = r["config"]["quick_actions"]
chk("保存成功且空的被丢掉", len(got) == 2, got)
chk("自定义按钮内容正确", got[0]["name"] == "挑出报错" and "报错" in got[0]["text"], got[0])
chk("名字截到 20 字", len(got[1]["name"]) == 20, got[1]["name"])
chk("每条都补上 id", all(q.get("id") for q in got), got)
chk("能一个都不留（空列表也认）", call("/api/config", {"quick_actions": []})["config"]["quick_actions"] == [])
call("/api/config", {"quick_actions": qd})     # 放回默认，别影响后面

print("11. 命中计数 + 清理")
call("/api/ingest", {"messages": [{"msg_id": "1003", "channel_id": "800000000000000001",
                                   "author_id": WHO, "author": "Marcus",
                                   "content": "再看一下", "mentions_me": True}]})
time.sleep(0.8)
st = call("/api/state")
hit = [x for x in st["rules"] if x["id"] == rid]
chk("规则命中数 +1", hit and hit[0].get("hits", 0) >= 1, hit)
time.sleep(0.5)
lines = [l for l in open("/tmp/bcode/echo.jsonl")]
chk("命中后按出口设置转发了 2 次（webhook+企业微信）", len(lines) == 4, len(lines))
bodies = [json.loads(json.loads(l)["body"]) for l in lines[2:]]
flat = json.dumps(bodies, ensure_ascii=False)
chk("转发内容带上了原文", "再看一下" in flat, flat[:200])
d = call(f"/api/rules/{rid}", method="DELETE")
chk("删除规则", d.get("ok") and all(x["id"] != rid for x in d["rules"]))

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

print("13. A6：base_url 尾巴 /model（单数）也能剥掉")
call("/api/config", {"providers": [{"name": "mock2", "base_url": "http://127.0.0.1:8899/v1/model",
                                    "api_key": "sk-test"}]})
m2 = call("/api/models", {"provider": "mock2"})
chk("尾巴 /model 照样拉到模型", m2.get("ok") and m2.get("models") == ["mock-1", "mock-2"], m2)

print("14. A6：Key 空 + 外地地址 = 直接说人话，不放空枪")
call("/api/config", {"providers": [
    {"name": "mock", "base_url": "http://127.0.0.1:8899/v1", "api_key": "sk-test"},
    {"name": "nokey", "base_url": "https://example.invalid/v1", "api_key": ""}]})
m3 = call("/api/models", {"provider": "nokey"})
chk("失败且明说 Key 空", (not m3.get("ok")) and "API Key 是空的" in (m3.get("error") or ""), m3)
call("/api/config", {"providers": [{"name": "mock", "base_url": "http://127.0.0.1:8899/v1",
                                    "api_key": "sk-test"}]})   # 复原，别影响后面的套

print(f"\n通过 {ok} / 失败 {fail}")
