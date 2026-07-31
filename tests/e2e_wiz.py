"""引导式建规则的回归：多轮反问 → 出规则 → 清洗 → 能保存。
需要 mockllm(:8899) + server(:8777)。别和 e2e.py 同时跑。"""
import re
import json, urllib.request, urllib.error

B = "http://127.0.0.1:8777"
P, F = [], []


def call(path, data=None, method=None):
    req = urllib.request.Request(B + path, method=method or ("POST" if data is not None else "GET"),
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def ck(name, cond, extra=""):
    (P if cond else F).append(name)
    print(("  ok  " if cond else "FAIL  ") + name + (("   " + str(extra)[:200]) if not cond else ""))


def wiz(turns, prov="mock", model="mock-1"):
    return call("/api/rules/wizard", {"messages": turns, "provider": prov, "model": model})


# ---- 准备：配好假模型 + 灌一条消息，让 rule_ctx 有真 ID ----
call("/api/config", {"providers": [{"name": "mock", "base_url": "http://127.0.0.1:8899/v1",
                                    "api_key": "sk-test"}],
                     "default_model": {"provider": "mock", "model": "mock-1"}})
call("/api/messages/clear", {})
call("/api/ingest", {"bridge": "wiz", "account": "小明",
                     "messages": [{"msg_id": "w1", "guild_id": "111111111111111111",
                                   "channel_id": "222222222222222222", "channel_name": "交易",
                                   "author": "Ann", "author_id": "333333333333333333",
                                   "content": "有多余的会员送人"}]})

st, r = call("/api/config", {"providers": "不是数组"})
ck("providers 形状不对给 400 而不是 500", st == 400, (st, r))

# ---- 1. 空对话要挡住 ----
st, r = wiz([])
ck("空对话被挡", st == 200 and not r.get("ok"), r)

# ---- 2. 第一轮必须反问，而不是直接出规则 ----
st, r = wiz([{"role": "user", "content": "我想知道有人送用不完的套餐"}])
ck("第一轮是反问", r.get("stage") == "ask", r)
ck("反问带复述", bool(r.get("understood")), r)
qs = r.get("questions") or []
ck("问题最多 3 个", len(qs) == 3, len(qs))
ck("每个问题带推荐值", all("suggest" in q for q in qs), qs)
ck("问题带 why", any(q.get("why") for q in qs), qs)
ck("带假设说明", bool(r.get("assumed")), r)

# ---- 3. 答完两轮后出规则 ----
turns = [{"role": "user", "content": "我想知道有人送用不完的套餐"},
         {"role": "assistant", "content": json.dumps({"stage": "ask"})},
         {"role": "user", "content": "整个频道，机器人也算"},
         {"role": "assistant", "content": json.dumps({"stage": "ask"})},
         {"role": "user", "content": "就这样"}]
st, r = wiz(turns)
ck("够了就出规则", r.get("stage") == "done", r)
rule = r.get("rule") or {}
ck("规则有名字", bool(rule.get("name")), rule)
ck("模糊意图用 ai_tag", rule.get("action") == "ai_tag", rule.get("action"))
ck("粗筛关键词在", len(rule.get("keywords_any") or []) >= 3, rule.get("keywords_any"))
ck("阈值带上了", rule.get("notify_min_score") == 60, rule.get("notify_min_score"))
ck("字符串数字转 int", rule.get("cooldown_sec") == 5 and isinstance(rule.get("cooldown_sec"), int),
   rule.get("cooldown_sec"))
ck("公告类 ignore_bots=False", rule.get("ignore_bots") is False, rule.get("ignore_bots"))
ck("帖子一起听", rule.get("include_threads_of_channels") is True, rule)

# 清洗
ck("编造的频道 ID 被丢掉", "777777777777777777" not in (rule.get("channel_ids") or []), rule.get("channel_ids"))
ck("丢 ID 有说明", any("丢掉" in n for n in r.get("notes") or []), r.get("notes"))
ck("真实的人 ID 保留", "333333333333333333" in (rule.get("author_ids") or []), rule.get("author_ids"))
ck("非法字段被丢", "bogus" not in rule, list(rule))

# 解释与验证
ck("给了会命中什么", bool(r.get("catches")), r)
ck("给了不会命中什么", bool(r.get("misses")), r)
ck("给了怎么验证", bool(r.get("verify")), r)
ck("提醒生成≠生效", any("不等于生效" in n for n in r.get("notes") or []), r.get("notes"))

# ---- 4. 出来的规则能直接存，且真能用 ----
st, sv = call("/api/rules", dict(rule, enabled=1))
rid = sv.get("id") or (sv.get("rule") or {}).get("id")
ck("向导规则能保存", st == 200 and rid, sv)
if rid:
    st, t = call("/api/rules/test", {"rule": dict(rule, enabled=1),
                                     "sample": {"channel_id": "222222222222222222", "author": "Ann",
                                                "author_id": "333333333333333333",
                                                "content": "有多余的会员送人，有人要吗"}})
    ck("试算能命中", t.get("match") is True, t)
    call("/api/rules/" + str(rid), method="DELETE")

# ---- 5. 弱模型不给 JSON 也不能崩 ----
st, r = wiz([{"role": "user", "content": "GARBAGE 帮我建个规则"}])
ck("非 JSON 回复降级成追问", st == 200 and r.get("ok") and r.get("stage") == "ask", r)
ck("降级标了 loose", r.get("loose") is True, r)
ck("降级把原话端出来", "正则" in json.dumps(r, ensure_ascii=False), r)

# ---- 6. ```json 包裹也能解析 ----
st, r = wiz(turns[:-1] + [{"role": "user", "content": "FENCE 就这样"}])
ck("code fence 能解析", r.get("stage") == "done" and (r.get("rule") or {}).get("name"), r)

# ---- 7. 问太多轮要强制收尾 ----
many = []
for i in range(4):
    many += [{"role": "user", "content": "第%d次回答" % i},
             {"role": "assistant", "content": json.dumps({"stage": "ask"})}]
many.append({"role": "user", "content": "继续"})
st, r = wiz(many)
ck("超过 3 轮强制出规则", r.get("stage") == "done", r)

# ---- 8. 界面没传模型时用默认模型，一个都没配才报错 ----
st, r = wiz([{"role": "user", "content": "随便"}], prov="", model="")
ck("没传模型时用默认模型", r.get("ok") is True, r)
call("/api/config", {"default_model": {"provider": "", "model": ""}})
st, r = wiz([{"role": "user", "content": "随便"}], prov="", model="")
ck("一个模型都没配时给人话", st == 200 and not r.get("ok") and "模型" in (r.get("error") or ""), r)
call("/api/config", {"default_model": {"provider": "mock", "model": "mock-1"}})


# ================= 内置提示词看得见 + 诊断包 =================
import urllib.request as _u
def raw(path):
    try:
        with _u.urlopen(B + path, timeout=30) as r:
            return r.status, r.read().decode("utf-8"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore"), dict(e.headers)

st, r = call("/api/prompts")
ck("提示词接口能读", st == 200 and r.get("ok"), r)
keys = [p["key"] for p in (r.get("builtin") or [])]
ck("两条内置提示词都在", "wizard" in keys and "compose" in keys, keys)
wz = next((p for p in r["builtin"] if p["key"] == "wizard"), {})
ck("给的是原文不是摘要", len(wz.get("text") or "") > 2000, len(wz.get("text") or ""))
ck("说清了在哪改", "WIZARD_SYS" in (wz.get("where") or ""), wz.get("where"))
ck("说了什么时候用/为什么这么写", bool(wz.get("when")) and bool(wz.get("why")), wz)
ck("提示可改的那几条在哪", "模型接入" in (r.get("editable_hint") or ""), r.get("editable_hint"))

# 桥带上扩展诊断
call("/api/ingest", {"bridge": "diagbr", "account": "小明", "browser": "Chrome 131", "ver": "1.6.0",
                     "where": "#交易", "ping": True,
                     "stats": {"parsed": 42, "sent": 7,
                               "skip": {"history": 30, "render": 5, "dup": 0, "notext": 0, "quiet": 0},
                               "recent": [{"what": "有人送会员", "why": "已上报"}],
                               "url": "/channels/1/2", "lis": 50}})
st, d, h = raw("/diagnose.txt")
ck("诊断包能下", st == 200 and len(d) > 500, (st, len(d)))
ck("是附件下载", "attachment" in (h.get("Content-Disposition") or ""), h.get("Content-Disposition"))
ck("文件名带中文也不炸", "filename*=UTF-8" in (h.get("Content-Disposition") or ""), h.get("Content-Disposition"))
for sec in ("[1] 程序", "[2] 出网设置", "[3] 收信来源", "[4] 规则", "[5] 通知与转发",
            "[6] 最近 200 条运行日志", "[7] 最近 30 条消息"):
    ck("诊断包有 " + sec, sec in d, "缺")
ck("带上程序版本", re.search(r"版本[^\n]*\bv?\d+\.\d+\.\d+", d) is not None, d[:200])
ck("列出了桥和它的扩展版本", "小明" in d and "Chrome 131" in d, "缺桥")
ck("带上扩展侧解析/上报实数", "解析 42" in d and "上报 7" in d, "缺 stats")
ck("带上跳过原因分类", "历史 30" in d and "整批渲染 5" in d, "缺 skip")
ck("带上最近记录", "有人送会员" in d, "缺 recent")
ck("列表按人话打印不是 Python 语法", "['" not in d, "有 Python repr")
ck("不泄露 api_key", "sk-test" not in d, "泄露了")
st, d2, _ = raw("/diagnose.txt?text=0")
ck("可以不带消息正文", st == 200 and "（不含正文）" in d2, d2[:200])


# ============ 工作台的系统提示词（v1.6.2）============
# 起因：用户在工作台贴频道链接说「帮我监听」，模型回「我无法访问第三方平台」并推荐他去
# 写 Discord 机器人 / 用 Zapier；说「帮我写规则」被反问「是群规还是游戏规则」。
# 根因是工作台的 system 只有一句话。下面这些断言就是防它退回去。
def msys(prompt, extra=None):
    """发一句话给工作台，把假模型收到的 system 原文捞回来。"""
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8899/__reset",
                                                 data=b"", method="POST"), timeout=10).read()
    body = {"prompt": prompt, "provider": "mock", "model": "mock-1"}
    if extra is not None:
        body["system"] = extra
    st, r = call("/api/ask", body)
    with urllib.request.urlopen("http://127.0.0.1:8899/__calls", timeout=10) as f:
        cs = json.loads(f.read().decode())["calls"]
    return st, r, (cs[-1]["system"] if cs else "")


LINK = "https://discord.com/channels/1134557553011998840/1513056466293096518"
st, r, sy = msys("帮我监听 " + LINK)
ck("工作台调用成功", st == 200 and r.get("ok"), (st, r))
ck("system 说清了它在 dcwatch 里", "dcwatch" in sy and "工作台" in sy, sy[:120])
ck("明令禁止「我无法访问」那套话", "我无法访问" in sy and "绝对不许出现" in sy, "边界没写")
ck("明令禁止推荐 Zapier / 自己搭 bot", "Zapier" in sy and "帮倒忙" in sy, "反面例子没写")
ck("讲明监听是程序做的不是模型做的", "监听是程序做的" in sy, "缺最关键那句")
ck("链接已拆成服务器 ID", "1134557553011998840" in sy, "没拆 guild")
ck("链接已拆成频道 ID", "1513056466293096518" in sy, "没拆 channel")
ck("并且告诉模型别让用户自己找 ID", "别让他自己去找 ID" in sy, "缺")
ck("带上程序版本", re.search(r"dcwatch 版本 v\d+\.\d+\.\d+", sy) is not None, sy[:200])
ck("带上收信方式", "收信方式" in sy, "缺实况")
ck("带上已有规则实况", "他已有的规则" in sy or "还没有任何规则" in sy, "缺规则实况")
ck("带上最近收到消息的频道", "交易=222222222222222222" in sy, "缺频道实况")

st, r, sy2 = msys("帮我写规则")
ck("讲明「规则」= 监听规则不是群规", "不是**群聊管理规则**" in sy2 or "不是**群聊管理规则**" in sy2
   or ("群聊管理规则" in sy2 and "绝对不要反问" in sy2), "缺规则语义")
ck("给出向导这条路", "帮我建条规则" in sy2, "没指路")
# v1.8.0 起工作台自己就能改规则，所以「说了不生效」这条断言反过来了：
# 它必须知道自己有手，同时仍然指得出向导那条路
ck("讲明它自己就能建规则", "你自己就能建" in sy2, "缺")
ck("带上工具用法", "list_rules" in sy2 and "update_rule" in sy2, "缺工具说明")
ck("要求先查真实 id 不许猜", "绝不凭名字猜 id" in sy2, "缺纪律")

st, r, sy3 = msys("看看这个 https://discord.com/channels/@me/999888777666555444")
ck("私信链接认得出", "私信" in sy3 and "999888777666555444" in sy3, sy3[-300:])
ck("并提醒规则默认不听私信", "包含私信 DM" in sy3, "缺 dm 提醒")

st, r, sy4 = msys("这条 https://discord.com/channels/111111111111111111/222222222222222222/333333333333333333 说啥")
ck("消息 ID 也拆出来", "消息 ID 333333333333333333" in sy4, sy4[-300:])

st, r, sy5 = msys("在吗")
ck("没链接时不硬塞链接段", "他这句话里贴了" not in sy5, "误报链接")

# 用户自己追加的要求要附上，但不能顶掉身份
st, r, sy6 = msys("在吗", extra="只用英文回答")
ck("额外要求会附上", "用户自己追加的要求" in sy6 and "只用英文回答" in sy6, sy6[-200:])
ck("附加要求不顶掉身份和边界", "监听是程序做的" in sy6, "身份被顶掉了")

st, r = call("/api/prompts")
keys = {p["key"] for p in r.get("builtin", [])}
ck("提示词清单里能看到工作台那条", "workbench" in keys, keys)
wb = [p for p in r.get("builtin", []) if p["key"] == "workbench"]
ck("工作台提示词有原文和出处", bool(wb) and len(wb[0]["text"]) > 800 and "WORKBENCH_SYS" in wb[0]["where"],
   len(wb[0]["text"]) if wb else 0)
ck("并写清了不写它会发生什么", bool(wb) and "Zapier" in wb[0]["why"], "why 没说后果")

print("\n%d passed, %d failed" % (len(P), len(F)))
if F:
    print("失败：" + " | ".join(F))
