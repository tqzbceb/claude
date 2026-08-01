"""B1 · AI 复核专项回归（PLAN_B1 §3.3）：脚本命中之后模型的第二道闸。
要钉住的 13 条（对应下面 §1–§12）：
  1. 关着复核时行为跟以前一模一样（回归保护）
  2. 复核 hit:true 放行 → 通知发了，aicheck 有 passed=1
  3. hit:false → 没通知，日志有「AI 复核判『不是』」，passed=0
  4. confidence 正好等于门槛 → 放行（边界是 >=）
  5. 模型 500 / 坏 JSON / 回散文 → 照旧通知，正文带 [AI 复核失败]（fail open）
  6. 正文含 [附件 xx.txt] / /下载 → 一次模型都没调（aiusage 不涨），直接【需要你人工看】
  7. ai_check_human=False 时同样的输入 → 不通知
  8. extracted 出现在通知正文、也出现在 hook 的 {{text}} / {{extracted}} 里
  9. ai_check_ctx=3 时喂给模型的 user 里真的有前面那几条（看 mock 回显）
 10. match() 没中的消息，开着复核也不会调模型
 11. 规则包导出/导入带得走这五个字段
 12. 诊断包 [4.6] 段印出最近战果、[4] 段印出五个字段
 13. 每日调用上限已满 → 不放行也不阻断，fail open 且日志/留痕说清原因
跑法见 RUN.md：mockllm :8899（本套模型一律用 "mock-chk"，别用 mock-1 ——
App.no_tools 是进程内内存，跟 e2e_wb 共用一个名字会互相污染），
server :8777，库干净（runall.sh 会备好）。"""
import json, sqlite3, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8777"
MOCK = "http://127.0.0.1:8899"
DB = "/tmp/p.db"
ECHO = "/tmp/bcode/echo.jsonl"
P = F = 0

CH1 = "800000000000000101"        # 主频道（规则 A/B/C 挂这）
CH2 = "800000000000000102"        # 上下文频道（规则 D 挂这）
G = "700000000000000001"


def http(url, body=None, method=None, raw=False):
    req = urllib.request.Request(url, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            t = r.read().decode()
            return t if raw else json.loads(t)
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode()[:400]}


def call(path, body=None, **kw):
    return http(BASE + path, body, **kw)


def ok(name, cond, extra=""):
    global P, F
    if cond:
        P += 1; print("  ok  ", name)
    else:
        F += 1; print("  FAIL", name, str(extra)[:400])


def db(sql, args=()):
    c = sqlite3.connect(DB)
    rows = c.execute(sql, args).fetchall()
    c.close()
    return rows


def aicheck_rows():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute("SELECT * FROM aicheck ORDER BY id")]
    c.close()
    return rows


def aiusage_n():
    return db("SELECT COUNT(*) FROM aiusage")[0][0]


def hooks():
    """echo.jsonl 每行是 {path, body(字符串)}；body 是我们模板渲出来的 JSON。"""
    try:
        return [json.loads(json.loads(l)["body"]) for l in open(ECHO, encoding="utf-8")]
    except FileNotFoundError:
        return []


def clear_echo():
    open(ECHO, "w").close()


def chk_calls():
    r = http(MOCK + "/__calls")
    return [c for c in r.get("calls", []) if c.get("kind") == "aicheck"]


def setchk(queue):
    return http(MOCK + "/__chk", {"queue": queue})


def logs_text():
    return json.dumps(call("/api/logs").get("logs", []), ensure_ascii=False)


def msg(mid, content, ch=CH1, author="发key的人"):
    return {"msg_id": mid, "guild_id": G, "channel_id": ch, "channel_name": "发key区",
            "author_id": "900000000000000009", "author": author, "content": content}


def ingest(*msgs):
    r = call("/api/ingest", {"bridge": "chk", "account": "测试", "messages": list(msgs)})
    assert r.get("accepted") == len(msgs), r
    time.sleep(1.2)                 # 复核要走一次 mock HTTP，等它落定


def hitj(conf=90, extracted=None, hit=True, human=False, reason="真在发 key", kind="key"):
    return {"json": {"hit": hit, "confidence": conf, "kind": kind,
                     "extracted": extracted or [], "need_human": human, "reason": reason}}


# ================= 准备：假模型（mock-chk，别用 mock-1）+ 出口 =================
call("/api/config", {
    "providers": [{"name": "mock", "base_url": "http://127.0.0.1:8899/v1", "api_key": "sk-test"}],
    "default_model": {"provider": "mock", "model": "mock-chk"},
    "sinks": {"toast": False, "sound": False, "hooks": [
        {"id": "h1", "name": "自定义", "url": "http://127.0.0.1:8898/hook", "content": "json",
         "body": '{"t":"{{text}}","ex":"{{extracted}}","h":"{{need_human}}"}'}]},
})
call("/api/messages/clear", {})
http(MOCK + "/__reset", {})

# 规则 A：不开复核的老规则（回归保护对照组）
r = call("/api/rules", {"name": "老规则没复核", "enabled": 1, "channel_ids": [CH1],
                        "keywords_any": ["老关键词"], "action": "notify"})
RA = r["id"]
# 规则 B：开复核的主测规则（五个字段显式填全，§11 要导出去数）
r = call("/api/rules", {"name": "盯key要复核", "enabled": 1, "channel_ids": [CH1],
                        "keywords_any": ["sklive"], "action": "notify",
                        "ai_check": True, "ai_check_prompt": "", "ai_check_min": 60,
                        "ai_check_ctx": 3, "ai_check_human": True})
RB = r["id"]

print("§1 关着复核时行为跟以前一模一样")
clear_echo()
ingest(msg("a1", "这是一条带老关键词的普通消息"))
ok("没开复核也照常通知", len(hooks()) == 1 and "老关键词" in hooks()[0].get("t", ""), hooks())
ok("aicheck 表一行都没有", len(aicheck_rows()) == 0, aicheck_rows())
ok("模型一次都没被叫", len(chk_calls()) == 0, chk_calls())
st = call("/api/state")
ra = [x for x in st["rules"] if x["id"] == RA][0]
ok("老规则命中数 +1", ra.get("hits", 0) >= 1, ra.get("hits"))
ok("默认 ai_check 是关的", ra.get("ai_check") is False, ra.get("ai_check"))
ok("复核默认值洗出来了（min60/ctx3/human开）",
   ra.get("ai_check_min") == 60 and ra.get("ai_check_ctx") == 3 and ra.get("ai_check_human") is True,
   {k: ra.get(k) for k in ("ai_check_min", "ai_check_ctx", "ai_check_human")})

print("§2 复核 hit:true → 放行，还原的 key 进了所有出口")
clear_echo()
setchk([hitj(90, ["sk-live-99887766"])])
ingest(msg("b1", "白送一个 sklive: sk-live-99887766 别说是我给的"))
hk = hooks()
ok("通知发了", len(hk) == 1, hk)
ok("正文里有原文", "sk-live-99887766" in (hk[0].get("t", "") if hk else ""), hk)
ok("extra 拼了「模型还原出来的」", "模型还原出来的" in hk[0].get("t", ""), hk[0])
ok("{{extracted}} 占位符也带上了", "sk-live-99887766" in hk[0].get("ex", ""), hk[0])
ok("{{need_human}} 是空", hk[0].get("h", "x") == "", hk[0])
rows = aicheck_rows()
ok("aicheck 留痕 passed=1 / conf=90 / hit=1",
   len(rows) == 1 and rows[0]["passed"] == 1 and rows[0]["conf"] == 90 and rows[0]["hit"] == 1, rows)
ok("留痕里 extracted 也在", "sk-live-99887766" in (rows[0]["extracted"] or ""), rows[0])
calls = chk_calls()
ok("模型被叫了一次，且要求了 JSON 模式",
   len(calls) == 1 and calls[0].get("json_mode") is True, calls)

print("§3 复核 hit:false → 压掉，但有留痕有日志")
clear_echo()
setchk([hitj(95, hit=False, reason="只是闲聊")])
ingest(msg("b2", "sklive 今天还有人要吗"))
ok("没通知", len(hooks()) == 0, hooks())
rows = aicheck_rows()
ok("aicheck 留了 passed=0", len(rows) == 2 and rows[-1]["passed"] == 0 and rows[-1]["hit"] == 0, rows[-1])
ok("日志写了「AI 复核判不是」+ 消息 ID", "AI 复核判「不是」" in logs_text() and "b2" in logs_text(), "")

print("§4 门槛边界：confidence 正好等于 ai_check_min 放行，少 1 分压掉")
clear_echo()
setchk([hitj(60), hitj(59)])
ingest(msg("b3", "sklive 边界 60"))
ingest(msg("b4", "sklive 边界 59"))
hk = hooks()
ok("正好 60 分的那条放行了", len(hk) == 1 and "边界 60" in hk[0].get("t", ""), hk)
rows = aicheck_rows()
ok("60 → passed=1", rows[-2]["conf"] == 60 and rows[-2]["passed"] == 1, rows[-2])
ok("59 → passed=0", rows[-1]["conf"] == 59 and rows[-1]["passed"] == 0, rows[-1])

print("§5 模型炸了三种 → fail open 照旧通知，正文标 [AI 复核失败]")
clear_echo()
setchk([{"http": {"status": 500, "body": "boom"}},
        {"bad": True},
        {"raw": "我觉得这条消息挺重要的，你自己看看吧"}])
ingest(msg("b5", "sklive 模型 500"))
ingest(msg("b6", "sklive 坏 JSON"))
ingest(msg("b7", "sklive 回散文"))
hk = hooks()
ok("三条都照旧通知了", len(hk) == 3, hk)
ok("三条都带 [AI 复核失败]", all("[AI 复核失败]" in h.get("t", "") for h in hk), hk)
rows = aicheck_rows()[-3:]
ok("三条留痕 err 非空且 passed=1（fail open）",
   all(r["err"] and r["passed"] == 1 for r in rows), rows)
ok("散文那条 err 说「没按格式答」", "没按格式答" in rows[2]["err"], rows[2]["err"])
ok("warn 日志说「复核没做成，按放行处理」", "复核没做成" in logs_text() and "按放行处理" in logs_text(), "")

print("§6 正文含 [附件 xx.txt] / /下载 → 一次模型都没调，直接【需要你人工看】")
clear_echo()
u0, c0 = aiusage_n(), len(chk_calls())
setchk([])                          # 队列清空：下面两条根本不该走到模型
ingest(msg("b8", "发 sklive 了 [附件 sklive.txt] 自己下"))
ingest(msg("b9", "要 sklive 的去 /下载 那里拿"))
hk = hooks()
ok("两条都通知了", len(hk) == 2, hk)
ok("标题带【需要你人工看】", all("【需要你人工看】" in h.get("t", "") for h in hk), hk)
ok("{{need_human}} 是 1", all(h.get("h") == "1" for h in hk), hk)
ok("aiusage 一次没涨（没调模型）", aiusage_n() == u0, (u0, aiusage_n()))
ok("mock 那边也没收到复核调用", len(chk_calls()) == c0, (c0, len(chk_calls())))
rows = aicheck_rows()[-2:]
ok("留痕 human=1 / kind=unreadable / passed=1",
   all(r["human"] == 1 and r["kind"] == "unreadable" and r["passed"] == 1 for r in rows), rows)

print("§7 ai_check_human=False 时同样的输入 → 不通知")
r = call("/api/rules", {"name": "看不到别烦我", "enabled": 1, "channel_ids": [CH1],
                        "keywords_any": ["兑换码"], "action": "notify",
                        "ai_check": True, "ai_check_human": False})
RC = r["id"]
clear_echo()
ingest(msg("c1", "兑换码在图里 [图片]"))
ok("没通知", len(hooks()) == 0, hooks())
rows = aicheck_rows()
ok("留痕 passed=0 human=1", rows[-1]["passed"] == 0 and rows[-1]["human"] == 1, rows[-1])
ok("日志说清是因为关了「看不到也提醒」", "看不到也提醒" in logs_text(), "")
call(f"/api/rules/{RC}", method="DELETE")

print("§8 ai_check_ctx=3：喂给模型的 user 里真的有前面那几条")
call("/api/rules", {"name": "连起来读", "enabled": 1, "channel_ids": [CH2],
                    "keywords_any": ["真key"], "action": "notify",
                    "ai_check": True, "ai_check_ctx": 3})
clear_echo()
http(MOCK + "/__reset", {})
ingest(msg("d1", "上文一：今天服务器维护", ch=CH2),
       msg("d2", "上文二：谁有多的名额", ch=CH2),
       msg("d3", "上文三：别急我马上发", ch=CH2))
n0 = len(chk_calls())               # 上面三条不含「真key」，不该叫模型
setchk([hitj(88, reason="连起来看是真的")])
ingest(msg("d4", "上文说的那个真key是 ABCD-1234", ch=CH2))
calls = chk_calls()
ok("前文三条没触发复核（match 没中不调模型）", n0 == 0, n0)
ok("触发这条真叫了模型", len(calls) == 1, calls)
u = calls[0].get("user", "") if calls else ""
ok("user 里有「同频道前面几条」", "同频道前面几条" in u, u)
ok("上文一和上文三都在", "上文一" in u and "上文三" in u, u)
ok("要判断的这条排在最后", u.rstrip().endswith("要判断的这条：上文说的那个真key是 ABCD-1234"), u)

print("§9 match() 没中的消息，开着复核也不会调模型")
clear_echo()
c0 = len(chk_calls())
ingest(msg("e1", "今天天气真不错大家聊会天吧"))
ok("没通知", len(hooks()) == 0, hooks())
ok("模型没被叫", len(chk_calls()) == c0, (c0, len(chk_calls())))
ok("aicheck 表也没多行", all(r["msg_id"] != "e1" for r in aicheck_rows()), "")

print("§10 规则包导出/导入带得走五个字段")
pack = call("/api/rules/export")
rb = [x for x in pack["rules"] if x.get("name") == "盯key要复核"][0]
ok("导出带 ai_check 开关", rb.get("ai_check") is True, rb.get("ai_check"))
ok("导出带门槛/上下文/人工看/提示词四个字段",
   rb.get("ai_check_min") == 60 and rb.get("ai_check_ctx") == 3
   and rb.get("ai_check_human") is True and "ai_check_prompt" in rb, rb)
for x in call("/api/state")["rules"]:
    call(f"/api/rules/{x['id']}", method="DELETE")
p = call("/api/rules/import", {"data": pack, "dry_run": False})
ok("导入成功且没把五个字段当生字段", p.get("ok") and "不认识的字段" not in json.dumps(p, ensure_ascii=False), p)
rb2 = [x for x in call("/api/state")["rules"] if x.get("name") == "盯key要复核"][0]
ok("导回来的五个字段原样在",
   rb2.get("ai_check") is True and rb2.get("ai_check_min") == 60 and rb2.get("ai_check_ctx") == 3
   and rb2.get("ai_check_human") is True and "ai_check_prompt" in rb2, rb2)

print("§11 诊断包：[4] 段印五个字段，[4.6] 段印最近战果")
txt = call("/diagnose.txt", raw=True)
ok("[4] 段有 AI 复核行", "AI 复核" in txt, "")
ok("[4] 段印了门槛和上下文", "门槛" in txt and "上下文" in txt, "")
ok("[4] 段印了「看不到也提醒」", "看不到也提醒" in txt, "")
ok("[4.6] 段在", "[4.6]" in txt, "")
ok("[4.6] 段放行和压掉都印出来了", "放行" in txt and "压掉" in txt, "")

print("§12 每日调用上限已满 → 不放行也不阻断，fail open 且说清原因")
call("/api/config", {"ai_daily_call_cap": aiusage_n()})     # 把上限压到当前用量，下一次必撞
clear_echo()
c0 = len(chk_calls())
setchk([hitj(90)])
ingest(msg("f1", "sklive 撞上限了"))
hk = hooks()
ok("照样通知（fail open）", len(hk) == 1, hk)
ok("正文带 [AI 复核失败]", hk and "[AI 复核失败]" in hk[0].get("t", ""), hk)
ok("模型其实没被叫（上限在调用前就拦了）", len(chk_calls()) == c0, (c0, len(chk_calls())))
rows = aicheck_rows()
ok("留痕 err 写明「已达今日调用上限」", "已达今日调用上限" in (rows[-1]["err"] or ""), rows[-1]["err"])
ok("留痕 passed=1（没阻断）", rows[-1]["passed"] == 1, rows[-1])
call("/api/config", {"ai_daily_call_cap": 500})             # 恢复原样，别污染后面的套

print(f"\n===== 通过 {P} / 失败 {F}")
raise SystemExit(1 if F else 0)
