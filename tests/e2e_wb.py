"""v1.8.0 回归：AI 工作台能不能真的动手改规则。
覆盖 函数调用 / 文本指令降级 / 不支持 tools 自动退回 / 流式 SSE /
「允许模型直接改规则」这个勾真的能关掉两条路 / 编 ID 不许写进规则 / 删除要求明确 /
env.can_send 与 env.ai。
跑法见 RUN.md：mockllm 在 :8899，server 在 :8777，库干净。"""
import json, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8777"
MOCK = "http://127.0.0.1:8899"
P = F = 0


def http(url, body=None, method=None, raw=False, stream=False, timeout=60):
    req = urllib.request.Request(url, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            t = r.read().decode()
            return t if (raw or stream) else json.loads(t)
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


def script(queue):
    http(MOCK + "/__script", {"queue": queue})


def calls():
    return http(MOCK + "/__calls")["calls"]


def reset():
    http(MOCK + "/__reset", {})


def rules():
    return {str(r["id"]): r for r in call("/api/state")["rules"]}


def mk(rule, enabled=1):
    return str(call("/api/rules", dict(rule, enabled=enabled)).get("id"))


def ask(prompt, **kw):
    return call("/api/ask", dict({"prompt": prompt}, **kw))


def ai(stream=None, tools=None):
    cur = call("/api/state")["env"]["ai"]
    body = {"stream": cur["stream"] if stream is None else stream,
            "tools": cur["tools"] if tools is None else tools}
    call("/api/config", {"ai": body})


call("/api/config", {"providers": [{"name": "mock", "base_url": "http://127.0.0.1:8899/v1",
                                    "api_key": "sk-test"}],
                     "default_model": {"provider": "mock", "model": "mock-1"}})
for r in call("/api/state")["rules"]:                    # 从干净的规则表开始
    call(f"/api/rules/{r['id']}", None, method="DELETE")
ai(stream=True, tools=True)

print("1. 模型自己动手改规则（函数调用）")
rid = mk({"name": "所有消息", "channel_ids": ["700000000000000001"], "min_len": 5,
          "action": "notify", "cooldown_sec": 0})
reset()
script([{"tools": [{"name": "list_rules"}]},
        {"tools": [{"name": "update_rule", "args": {"id": rid, "patch": {"min_len": 0}}}]},
        {"content": "改好了，现在表情包也会提醒你。"}])
r = ask("把「所有消息」那条改成连表情包也提醒")
ok("请求成功", r.get("ok") is True, r)
ok("回的是模型最后那句话", "改好了" in (r.get("text") or ""), r)
ok("规则真的改了", rules()[rid]["min_len"] == 0, rules().get(rid))
ok("告诉界面配置动了", r.get("changed") is True, r)
ok("顺手把新规则带回来，界面不用再拉一次", any(str(x["id"]) == rid for x in (r.get("rules") or [])), r)
acts = r.get("acts") or []
ok("干过的事都列出来了（读一次 + 写一次）", len(acts) == 2, acts)
ok("读的那次不算改动", acts[0].get("wrote") is False and acts[0].get("ok") is True, acts)
ok("写的那次标成改动", acts[1].get("wrote") is True, acts)
ok("写的那句是人话不是函数名", "规则" in (acts[1].get("human") or ""), acts)
c = calls()
ok("三轮都带上了工具清单", all("update_rule" in x["tools"] for x in c[-3:]), [x["tools"] for x in c])
ok("工具执行结果以 tool 角色回喂给模型", "tool" in c[-1]["roles"], c[-1]["roles"])

print("2. 改之前先查 id，不许凭空编")
reset()
script([{"tools": [{"name": "update_rule", "args": {"id": "99999", "patch": {"min_len": 3}}}]},
        {"content": "没找到那条规则，你是说「所有消息」吗？"}])
r = ask("把 xx 规则改一下")
a = (r.get("acts") or [{}])[0]
ok("id 不存在时明确失败", a.get("ok") is False, r)
ok("失败原因带上现有规则，好让它自己纠正", "没有 id" in (a.get("err") or ""), a)
ok("失败不影响这轮继续说话", "没找到" in (r.get("text") or ""), r)
ok("失败不算改动", r.get("changed") is False, r)

print("3. 建 / 停 / 试算 / 删")
reset()
script([{"tools": [{"name": "create_rule", "args": {
            "rule": {"name": "有人发 key", "keywords_any": ["key", "密钥"],
                     "channel_ids": ["700000000000000001"], "action": "notify"}, "enabled": True}}],
         "content": ""},
        {"content": "建好了。"}])
r = ask("加一条：有人发 key 就提醒我")
new = [x for x in call("/api/state")["rules"] if x["name"] == "有人发 key"]
ok("新规则建出来了", len(new) == 1, [x["name"] for x in call("/api/state")["rules"]])
ok("默认是启用的", bool(new and new[0]["enabled"]), new)
ok("建规则算改动", r.get("changed") is True, r)
nid = str(new[0]["id"]) if new else "0"

reset()
script([{"tools": [{"name": "test_rule", "args": {"id": nid, "content": "有人发 key 了",
                                                  "channel_id": "700000000000000001"}}]},
        {"content": "会命中。"}])
r = ask("这条现在会命中吗")
ok("试算这一步不算改动", r.get("changed") is False, r)
ok("试算说了人话", "试算" in ((r.get("acts") or [{}])[0].get("human") or ""), r.get("acts"))

reset()
script([{"tools": [{"name": "set_rule_enabled", "args": {"id": nid, "enabled": False}}]},
        {"content": "已经停了。"}])
r = ask("先把它停掉")
ok("停用真的落库了", rules()[nid]["enabled"] in (0, False), rules()[nid])
ok("停用这句话是人话", "停用" in ((r.get("acts") or [{}])[0].get("human") or ""), r.get("acts"))

reset()
script([{"tools": [{"name": "delete_rule", "args": {"id": nid}}]},
        {"content": "删了。"}])
r = ask("这条不要了，删掉")
ok("删除真的删了", nid not in rules(), list(rules()))
ok("删除会说明不可逆", "不可逆" in ((r.get("acts") or [{}])[0].get("human") or ""), r.get("acts"))

print("4. 只读工具：状态 / 频道 / 搜消息")
call("/api/ingest", {"account": "tester", "messages": [
    {"msg_id": "wb1", "channel_id": "700000000000000001", "channel_name": "资源区",
     "author": "Bob", "author_id": "900000000000000009", "content": "谁有 mimo 的 key",
     "ts": time.time(), "is_bot": False}]})
reset()
script([{"tools": [{"name": "search_messages", "args": {"query": "mimo"}}]},
        {"content": "Bob 问过 mimo 的 key。"}])
r = ask("有人提过 mimo 吗")
fed = calls()[-1]["tool_out"]
ok("搜到的消息真的喂回给了模型", "mimo" in fed, fed[:200])
reset()
script([{"tools": [{"name": "get_status"}]}, {"content": "桥是活的。"}])
r = ask("怎么没提醒我")
ok("状态里带上了自查结论", "findings" in calls()[-1]["tool_out"], calls()[-1]["tool_out"][:200])
reset()
script([{"tools": [{"name": "list_channels"}]}, {"content": "有资源区。"}])
r = ask("我都有哪些频道")
ok("频道清单带真实 ID", "700000000000000001" in calls()[-1]["tool_out"], calls()[-1]["tool_out"][:200])

print("5. 模型编的频道 ID 不许写进规则")
reset()
script([{"tools": [{"name": "create_rule", "args": {"rule": {
            "name": "编的", "channel_ids": ["123123123123123123"], "action": "notify"}}}]},
        {"content": "建好了。"}])
r = ask("随便建一条")
got = [x for x in call("/api/state")["rules"] if x["name"] == "编的"]
ok("编的 ID 被洗掉", bool(got) and got[0]["channel_ids"] == [], got)
ok("并且明确告诉模型丢了什么", "编的" in calls()[-1]["tool_out"] and "notes" in calls()[-1]["tool_out"],
   calls()[-1]["tool_out"][:300])
if got:
    call(f"/api/rules/{got[0]['id']}", None, method="DELETE")

print("6. 接口不支持 tools：自动退回文本指令，用户不该看见报错")
reset()
script([{"http": {"status": 400, "body": "tools is not supported by this model"}},
        {"tools": [{"name": "update_rule", "args": {"id": rid, "patch": {"min_len": 7}}}],
         "content": "好，我改一下。"},
        {"content": "已经改成 7 了。"}])
r = ask("把最短字数改成 7")
ok("退回之后照样改成了", rules()[rid]["min_len"] == 7, rules()[rid])
ok("用户看到的是正常回答，不是 400", r.get("ok") is True and "已经改成" in (r.get("text") or ""), r)
ok("界面上说明了换成文本指令模式", any("文本指令" in (a.get("human") or "") for a in (r.get("acts") or [])),
   r.get("acts"))
c = calls()
ok("第二轮起不再白带 tools", c[1]["tools"] == [] and c[2]["tools"] == [], [x["tools"] for x in c])
ok("换成文本指令的提示词发下去了", "```dcwatch" in c[1]["system"], c[1]["system"][-300:])
ok("最终回答里没有残留代码块", "```" not in (r.get("text") or ""), r.get("text"))
lg = " ".join(x["text"] for x in call("/api/logs")["logs"])
ok("日志里记了这个模型不支持函数调用", "不支持函数调用" in lg, lg[-200:])
reset()
script([{"tools": [{"name": "list_rules"}]}, {"content": "看过了。"}])
ask("再看一眼")
ok("记住了，下一轮不再重试 tools", calls()[0]["tools"] == [], calls()[0]["tools"])

print("7. 「允许模型直接改规则」关掉之后，两条路都必须断掉")
ai(tools=False)
before = rules()[rid]["min_len"]
reset()
script([{"tools": [{"name": "update_rule", "args": {"id": rid, "patch": {"min_len": 1}}}],
         "content": "我来改。"}])
r = ask("把最短字数改成 1")
ok("规则没有被改动", rules()[rid]["min_len"] == before, rules()[rid])
ok("也没有报告任何动作", not (r.get("acts") or []) and r.get("changed") is False, r)
c = calls()
ok("只调了一轮，不再连环调用", len(c) == 1, len(c))
ok("没给它 tools", c[0]["tools"] == [], c[0]["tools"])
ok("提示词明确说了这轮不能动手", "不能动手" in c[0]["system"], c[0]["system"][-300:])
ok("并且要求它别输出 dcwatch 代码块", "不要输出 ```dcwatch" in c[0]["system"], "")
ai(tools=True)
reset()
script([{"tools": [{"name": "list_rules"}]}, {"content": "又能改了。"}])
r = ask("看看规则")
ok("勾回来之后又能动手了", len(r.get("acts") or []) == 1, r.get("acts"))

print("8. 流式：一个字一个字来，工具动作也要中途推出去")
reset()
script([{"tools": [{"name": "update_rule", "args": {"id": rid, "patch": {"min_len": 2}}}],
         "content": "我先改一下"},
        {"content": "改完了，现在最短 2 个字。"}])
raw = call("/api/ask/stream", {"prompt": "改成 2"}, stream=True)
evs = [json.loads(x[5:]) for x in raw.split("\n\n") if x.strip().startswith("data:")]
ts = [e["t"] for e in evs]
ok("开头有 start", ts[0] == "start", ts[:3])
ok("中间有正文碎片", ts.count("delta") > 3, ts)
ok("动作是当场推出去的，不是最后才给", "act" in ts and ts.index("act") < ts.index("done"), ts)
ok("最后一条是 done", ts[-1] == "done", ts[-3:])
done = evs[-1]
ok("done 里带完整回答", "改完了" in (done.get("text") or ""), done)
ok("done 里带改动后的规则", any(str(x["id"]) == rid for x in (done.get("rules") or [])), done.get("rules"))
ok("流式也真的把规则改了", rules()[rid]["min_len"] == 2, rules()[rid])
ok("服务端确实请求了 stream", calls()[0]["stream"] is True, calls()[0])
ok("参数分片能拼回来（改对了字段就说明拼对了）", rules()[rid]["min_len"] == 2, "")

print("9. 流式里出错要用 error 事件说清楚，不能干等")
reset()
script([{"http": {"status": 500, "body": "boom"}}])
raw = call("/api/ask/stream", {"prompt": "会炸"}, stream=True)
evs = [json.loads(x[5:]) for x in raw.split("\n\n") if x.strip().startswith("data:")]
ok("给了 error 事件", any(e["t"] == "error" for e in evs), [e["t"] for e in evs])
ok("error 里有原始状态码", any("500" in str(e.get("error", "")) for e in evs), evs[-1])

print("10. 「测试一次调用」不该让它去动手")
reset()
script([{"content": "连接正常"}])
r = ask("用一句中文回复：连接正常", plain=1)
ok("plain 模式拿到回答", "连接正常" in (r.get("text") or ""), r)
ok("plain 模式不带工具", calls()[0]["tools"] == [], calls()[0]["tools"])
ok("plain 模式只调一次", len(calls()) == 1, len(calls()))

print("11. 多轮：前几轮要带上去，不然「那就改吧」接不上")
reset()
script([{"content": "要我改吗？"}])
r = ask("那就改吧", history=[{"role": "user", "content": "min_len 是啥"},
                            {"role": "assistant", "content": "最短字数，你现在是 2"}])
c = calls()[0]
ok("角色顺序是 system→user→assistant→user", c["roles"] == ["system", "user", "assistant", "user"], c["roles"])
ok("最后一句才是这次问的", c["user"].strip().endswith("那就改吧"), c["user"][-80:])

print("12. env：能不能发消息、两个开关，界面靠它决定画不画输入框")
e = call("/api/state")["env"]
ok("旁听模式明确不能发", e.get("can_send") is False, e.get("can_send"))
ok("env 里给出两个 AI 开关", set(e.get("ai") or {}) >= {"stream", "tools"}, e.get("ai"))
ai(stream=False)
ok("关掉流式能存下来", call("/api/state")["env"]["ai"]["stream"] is False, "")
ai(stream=True)

print("13. 提示词页要能看到「手」这段，不然用户不知道它凭什么能改我的规则")
pr = call("/api/prompts")
keys = [x["key"] for x in pr["builtin"]]
ok("多了 workbench_tools 一条", "workbench_tools" in keys, keys)
tp = [x for x in pr["builtin"] if x["key"] == "workbench_tools"][0]
ok("原文里有工具清单", "list_rules" in tp["text"] and "update_rule" in tp["text"], tp["text"][:200])
ok("也写明了关掉那个勾之后换成什么", "不能动手" in tp["text"], tp["text"][-200:])
ok("说明了在哪儿关", "允许模型直接改规则" in tp["why"] + tp["text"], tp["why"])

print(f"\n通过 {P} / 失败 {F}")
