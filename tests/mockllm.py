"""假 OpenAI 兼容端点：按 system prompt 判断这是哪种任务，回对应格式的东西。
用来在没有真模型/真 Discord 的情况下把 dcwatch 的 AI 动作全跑一遍。"""
import json
import re
from aiohttp import web

RULE_REPLY = {
    "rule": {
        "name": "老板 @我",
        "channel_ids": ["999999999999999999"],      # 编造的，应该被丢掉
        "author_ids": ["AUTHOR_PLACEHOLDER"],       # 真 ID，运行时替换
        "mention_only": True,                       # 合法，应保留
        "include_threads_of_channels": True,
        "action": "ai_scoreee",                     # 非法动作，应退回 notify
        "notify_min_score": 60,                     # 合法 int
        "cooldown_sec": "45",                       # 字符串数字，应转 int
        "min_score": 60,                            # 不存在的字段，应丢掉
        "bogus_field": "should be dropped",          # 不存在的字段，应丢掉
    },
    "notes": ["按你说的只在被 @ 时触发"],
}

CALLS = []          # 每次调用记一笔，测试里能查「到底调了几次、什么任务」
# 工作台（ask）分支的脚本队列：测试先 POST /__script 排好每一轮要回什么。
# 每项形如 {"content":"..."} / {"tools":[{"name":"list_rules","args":{}}]} /
#         {"http":{"status":400,"body":"tools is not supported"}}
SCRIPT = []
# AI 复核（aicheck）分支的脚本队列：测试先 POST /__chk 排好每次复核要回什么。
# 每项形如 {"json":{hit:...}}（回合法 JSON）/ {"raw":"一段散文"} /
#         {"http":{"status":500,"body":"boom"}} / {"bad":true}（回坏 JSON，模拟炸线）。
# 队列空了就回默认的「hit:true, confidence:90」—— 复核的默认姿态是放行。
CHKQ = []


def classify(system: str) -> str:
    # AI 复核的提示词里也有「监听」字样，必须排最前面判，别落进 compose/tag
    if "复核员" in system:
        return "aicheck"
    # 工作台的系统提示词里也有「监听规则」四个字，所以它必须排在 compose 前面判
    if "AI 工作台" in system:
        return "ask"
    if "规则向导" in system:
        return "wizard"
    if "监听规则" in system:
        return "compose"
    if "消息分流助手" in system or "score" in system:
        return "tag"
    if "抽取结构化信息" in system:
        return "extract"
    if "要点中文摘要" in system:
        return "summary"
    if "批量翻一批" in system:
        return "batch"
    if "该频道的助手" in system:
        return "reply"
    return "ask"


def gen_batch(user: str) -> str:
    """假提取：把每行 [msg_id] 里形如 ABCD-1234 的串挑出来，原样返回。
    原样很重要 —— 服务端会核对 value 是否真的出现在原文里。"""
    rows = []
    for line in (user or "").splitlines():
        m = re.match(r"\[(\w+)\]", line.strip())
        if not m:
            continue
        for tok in re.findall(r"[A-Za-z0-9]{4}-[A-Za-z0-9]{4}", line):
            rows.append({"msg_id": m.group(1), "value": tok, "note": "看着像兑换码"})
    return json.dumps({"rows": rows}, ensure_ascii=False)


async def models(_):
    return web.json_response({"data": [{"id": "mock-1"}, {"id": "mock-2"}]})


def tc(items):
    """把 [{"name":..,"args":{..}}] 变成 OpenAI 那种 tool_calls 结构。"""
    return [{"id": f"call_{i}", "type": "function",
             "function": {"name": it["name"],
                          "arguments": json.dumps(it.get("args") or {}, ensure_ascii=False)}}
            for i, it in enumerate(items)]


async def sse(msg, usage=True):
    """流式输出一个 message：正文切成小块，tool_calls 按 index 分片发。"""
    out = []
    for i in range(0, len(msg.get("content") or ""), 7):
        out.append({"choices": [{"delta": {"content": msg["content"][i:i + 7]}}]})
    for i, c in enumerate(msg.get("tool_calls") or []):
        out.append({"choices": [{"delta": {"tool_calls": [
            {"index": i, "id": c["id"], "type": "function",
             "function": {"name": c["function"]["name"], "arguments": ""}}]}}]})
        a = c["function"]["arguments"]
        for j in range(0, max(len(a), 1), 9):        # 参数分片，考验服务端会不会拼
            out.append({"choices": [{"delta": {"tool_calls": [
                {"index": i, "function": {"arguments": a[j:j + 9]}}]}}]})
    body = "".join("data: " + json.dumps(o, ensure_ascii=False) + "\n\n" for o in out) + "data: [DONE]\n\n"
    return web.Response(body=body.encode(), content_type="text/event-stream")


async def chat(req):
    b = await req.json()
    system = "\n".join(m["content"] for m in b["messages"] if m["role"] == "system")
    user = "\n".join(m["content"] for m in b["messages"] if m["role"] == "user")
    kind = classify(system)
    CALLS.append({"kind": kind, "user": user[:400], "json_mode": bool(b.get("response_format")),
                  "system": system[:6000], "stream": bool(b.get("stream")),
                  "tools": [t["function"]["name"] for t in (b.get("tools") or [])],
                  # 工具执行结果是 role=tool 发回来的，测试要断言「真喂回去了」就得看这里
                  "tool_out": "\n".join(str(m.get("content") or "") for m in b["messages"]
                                        if m["role"] == "tool")[:4000],
                  "roles": [m["role"] for m in b["messages"]]})

    if kind == "aicheck":
        step = CHKQ.pop(0) if CHKQ else {"json": {"hit": True, "confidence": 90, "kind": "key",
                                                  "extracted": [], "need_human": False,
                                                  "reason": "默认放行"}}
        if step.get("http"):
            return web.Response(status=step["http"].get("status", 500),
                                text=step["http"].get("body", "boom"))
        if step.get("bad"):                    # 合法 HTTP 但体不是 chat 形状：模拟炸线/超时那类
            return web.json_response({"choices": []})
        out = step.get("raw") or json.dumps(step.get("json"), ensure_ascii=False)
        if b.get("stream"):
            return await sse({"role": "assistant", "content": out})
        return web.json_response({"choices": [{"message": {"content": out}}],
                                  "usage": {"prompt_tokens": 10, "completion_tokens": 20}})

    if kind == "ask" and SCRIPT:
        step = SCRIPT.pop(0)
        if step.get("http"):
            return web.Response(status=step["http"].get("status", 400),
                                text=step["http"].get("body", "nope"))
        msg = {"role": "assistant", "content": step.get("content") or ""}
        if step.get("tools"):
            if b.get("tools"):
                msg["tool_calls"] = tc(step["tools"])
            else:                        # 没给 tools 的那轮，退回文本指令写法
                msg["content"] = (msg["content"] + "\n```dcwatch\n" + json.dumps(
                    {"tool": step["tools"][0]["name"], "args": step["tools"][0].get("args") or {}},
                    ensure_ascii=False) + "\n```").strip()
        if b.get("stream"):
            return await sse(msg)
        return web.json_response({"choices": [{"message": msg}]},
                                 headers={"X-Mock": "script"})

    if kind == "wizard":
        # 界面把整段对话带上来了，所以假模型能自己数轮数，模拟「问两轮再出规则」
        turns = [m for m in b["messages"] if m["role"] == "assistant"]
        done = len(turns) >= 2 or "就这样" in user or "出规则" in user
        if "GARBAGE" in user:                       # 弱模型不给 JSON
            out = "我觉得你应该盯着那个频道，要不要我帮你写个正则？"
        elif not done:
            out = json.dumps({"stage": "ask", "understood": "你想盯有人送套餐的帖子",
                              "questions": [{"key": "where", "q": "盯哪里？", "why": "不限范围会全收",
                                             "options": ["就这个帖子", "整个频道"], "suggest": "整个频道"},
                                            {"key": "who", "q": "机器人发的算吗？", "why": "公告多是机器人",
                                             "options": ["算", "不算"], "suggest": "不算"},
                                            {"key": "x", "q": "第三个"}, {"key": "y", "q": "第四个超额"}],
                              "assumed": ["先假设排除机器人"]}, ensure_ascii=False)
        else:
            ids = re.findall(r"人 \S+ = (\d{15,25})", user)
            rule = {"name": "有人送套餐", "keywords_any": ["捐", "送", "白送", "spare"],
                    "action": "ai_tag", "prompt": "判断是不是真在无偿给出可用资源",
                    "notify_min_score": 60, "cooldown_sec": "5", "ignore_bots": False,
                    "include_threads_of_channels": True,
                    "channel_ids": ["777777777777777777"],       # 编的，应被丢掉
                    "author_ids": [ids[0]] if ids else [],
                    "bogus": 1}
            out = json.dumps({"stage": "done", "rule": rule,
                              "catches": ["有人在频道里说『多的会员送人』"],
                              "misses": ["只发图没文字的"],
                              "notes": ["这类帖子开头常是「有人要吗」"],
                              "verify": "去那个频道自己发一句『有多余的送人』看看"}, ensure_ascii=False)
            if "FENCE" in user:                    # 模型爱套 ```json
                out = "```json\n" + out + "\n```"
    elif kind == "compose":
        ids = re.findall(r"人 \S+ = (\d{15,25})", user)
        r = json.loads(json.dumps(RULE_REPLY))
        r["rule"]["author_ids"] = [ids[0]] if ids else []
        out = json.dumps(r, ensure_ascii=False)
    elif kind == "tag":
        # 分数跟着内容变，方便测 min_score 门槛
        score = 88 if "@" in user else 20
        out = json.dumps({"score": score, "tags": ["需回复", "排期"], "reason": "上级要求改时间",
                          "todo": "确认 10:00 是否可行"}, ensure_ascii=False)
    elif kind == "extract":
        out = "这里有点前言，然后是 JSON：" + json.dumps(
            {"错误码": "500", "服务": "登录", "组件": "redis"}, ensure_ascii=False)
    elif kind == "batch":
        out = gen_batch(user)
    elif kind == "summary":
        out = "1. 登录接口 500\n2. 疑似 redis 连接被拒\n待办：让运维看连接池"
    elif kind == "reply":
        out = "收到，我看一下就回你。"
    else:
        out = "这几条里只有 Marcus 那条今天必须回。"
    if b.get("stream"):
        return await sse({"role": "assistant", "content": out})
    return web.json_response({"choices": [{"message": {"content": out}}],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 20}})


async def calls(_):
    return web.json_response({"calls": CALLS})


async def reset(_):
    CALLS.clear()
    SCRIPT.clear()
    CHKQ.clear()
    return web.json_response({"ok": True})


async def chk(req):
    b = await req.json()
    CHKQ.clear()
    CHKQ.extend(b.get("queue") or [])
    return web.json_response({"ok": True, "n": len(CHKQ)})


async def script(req):
    b = await req.json()
    SCRIPT.clear()
    SCRIPT.extend(b.get("queue") or [])
    return web.json_response({"ok": True, "n": len(SCRIPT)})


app = web.Application()
app.router.add_get("/v1/models", models)
app.router.add_post("/v1/chat/completions", chat)
app.router.add_get("/__calls", calls)
app.router.add_post("/__reset", reset)
app.router.add_post("/__script", script)
app.router.add_post("/__chk", chk)
web.run_app(app, host="127.0.0.1", port=8899, print=None)
