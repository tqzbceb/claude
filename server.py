#!/usr/bin/env python3
"""
dcwatch - lightweight Discord watcher + LLM workbench.

One process: Discord gateway listener + rule engine + OpenAI-compatible model client
+ local web GUI. Storage = SQLite. Only dependency: aiohttp.

Run:  python server.py            -> http://127.0.0.1:8777
"""
import asyncio, base64, json, os, re, sqlite3, sys, time, argparse, contextlib, random, shutil, webbrowser
from pathlib import Path

try:
    import aiohttp
    from aiohttp import web
except ImportError:
    sys.exit("need aiohttp:  pip install aiohttp")

FROZEN = getattr(sys, "frozen", False)          # True 时 = PyInstaller 打出来的 exe
# ui.html 在 exe 里是打进包的临时解包目录；数据必须写到真实可写目录
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
if FROZEN:
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "dcwatch"
else:
    DATA_DIR = Path(__file__).resolve().parent
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = os.environ.get("DCWATCH_DB", str(DATA_DIR / "dcwatch.db"))
IS_WIN = os.name == "nt"
API = "https://discord.com/api/v10"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")
# GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES | MESSAGE_CONTENT
INTENTS = (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)  # 37377

DEFAULT_CONFIG = {
    # mode: "bot"     = Bot Token 走 Gateway（官方、稳、需要能把 bot 拉进服务器）
    #       "user"    = 个人账号 Token 走 Gateway（只读；违反 Discord ToS，有封号风险）
    #       "browser" = 不填 token，由 Chrome 扩展旁听网页版 Discord，POST 进 /api/ingest
    "discord": {"token": "", "enabled": False, "mode": "browser"},
    "providers": [
        # name, base_url (OpenAI-compatible), api_key
        {"name": "openai", "base_url": "https://api.openai.com/v1", "api_key": ""},
    ],
    "default_model": {"provider": "openai", "model": ""},
    "sources": {"discord": True, "browser": True},   # master switch per source
    # 出口：命中并达到 min_score 的消息往哪儿送
    "sinks": {
        "browser": True,            # 网页通知（要开着界面）
        "toast": True,              # Windows 系统通知
        "sound": True,              # 提示音
        "sound_file": "",           # 自定义 .wav，留空用系统默认提示音
        "quiet_from": "", "quiet_to": "",   # 免打扰时段 "23:00"→"08:00"，只静音本机，不影响转发
        "discord_webhook": "",      # 转发到你自己的 Discord 频道（频道设置→整合→Webhook）
        "telegram": {"token": "", "chat_id": ""},
        "serverchan": "",           # Server酱 SendKey → 推到微信服务号（官方通道，不封号）
        "wecom": "",                # 企业微信群机器人 webhook
        "webhook": "",              # 自定义 JSON POST（飞书/钉钉/n8n/自建）
        "min_score": 0,             # AI 打分低于这个不往外发；没有分数的一律发
    },
    "retention_days": 14,
    "ai_daily_call_cap": 500,
}

# Windows 原生通知：PowerShell 调 WinRT，不需要装任何库。
# 标题/正文用环境变量传进去，避免引号转义问题；AppId 借用 PowerShell 的 AUMID 才会真的弹出来。
TOAST_PS = (
    "$ErrorActionPreference='Stop';"
    "[void][Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime];"
    "[void][Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime];"
    "$t=[System.Security.SecurityElement]::Escape($env:DCW_TITLE);"
    "$b=[System.Security.SecurityElement]::Escape($env:DCW_BODY);"
    "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
    "$x.LoadXml(\"<toast><visual><binding template='ToastGeneric'>"
    "<text>$t</text><text>$b</text></binding></visual></toast>\");"
    "$n=New-Object Windows.UI.Notifications.ToastNotification $x;"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($n)"
)
TOAST_B64 = base64.b64encode(TOAST_PS.encode("utf-16-le")).decode()

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT, msg_id TEXT UNIQUE, guild_id TEXT, channel_id TEXT, channel_name TEXT,
  parent_id TEXT, is_thread INT, author_id TEXT, author TEXT, is_bot INT,
  content TEXT, ts REAL, matched TEXT, ai_json TEXT, score INT, unread INT DEFAULT 1);
CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts DESC);
CREATE TABLE IF NOT EXISTS rules(
  id INTEGER PRIMARY KEY AUTOINCREMENT, json TEXT, enabled INT DEFAULT 1, hits INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS logs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, level TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS aiusage(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, rule TEXT,
  model TEXT, in_tok INT, out_tok INT, ok INT, err TEXT);
"""


class DB:
    def __init__(self, path):
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.commit()

    def q(self, sql, args=()):
        return [dict(r) for r in self.c.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        cur = self.c.execute(sql, args)
        self.c.commit()
        return cur.lastrowid

    def get_cfg(self):
        r = self.c.execute("SELECT v FROM kv WHERE k='config'").fetchone()
        cfg = json.loads(r["v"]) if r else {}
        out = json.loads(json.dumps(DEFAULT_CONFIG))
        out.update(cfg)
        return out

    def set_cfg(self, cfg):
        self.x("INSERT INTO kv(k,v) VALUES('config',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
               (json.dumps(cfg, ensure_ascii=False),))


DEFAULT_RULE = {
    "name": "新规则",
    "source": "discord",
    # ---- WHERE to listen (empty list = don't care) ----
    "guild_ids": [], "channel_ids": [], "thread_ids": [],
    "include_threads_of_channels": True,   # channel_ids also match its threads (子区)
    "dm": False,
    # ---- WHO ----
    "author_ids": [], "author_name_contains": "",
    "ignore_bots": True, "mention_only": False,
    # ---- WHAT ----
    "keywords_any": [], "keywords_all": [], "regex": "", "min_len": 0,
    # ---- ACTION ----
    "action": "notify",   # notify | ai_tag | ai_reply | ai_summary | ai_extract | webhook
    "model": "", "provider": "",
    "prompt": "",
    "reply_in_thread": True,
    "notify_min_score": 0,       # for ai_tag: only alert if score >= this
    "summary_every": 20,         # for ai_summary: run each N matched msgs
    "cooldown_sec": 20,
    "max_per_hour": 30,
    "webhook_url": "",
}


def now():
    return time.time()


class Bus:
    """SSE fan-out to open GUIs."""
    def __init__(self):
        self.subs = set()

    async def push(self, kind, data):
        dead = []
        payload = json.dumps({"kind": kind, "data": data}, ensure_ascii=False)
        for q in list(self.subs):
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            self.subs.discard(q)


class App:
    def __init__(self):
        self.db = DB(DB_PATH)
        self.cfg = self.db.get_cfg()
        self.bus = Bus()
        self.http: aiohttp.ClientSession | None = None
        self.dc: DiscordListener | None = None
        self.chan_cache: dict[str, dict] = {}
        self.rate: dict[str, list] = {}
        self.sum_buf: dict[str, list] = {}
        self.last_ingest = 0.0        # 浏览器旁听最后一次投递时间
        self.ingest_count = 0
        self._last_toast = 0.0        # 系统通知防刷屏
        self.port = 8777

    def log(self, level, text):
        self.db.x("INSERT INTO logs(ts,level,text) VALUES(?,?,?)", (now(), level, str(text)[:800]))
        print(f"[{level}] {text}", flush=True)
        asyncio.create_task(self.bus.push("log", {"ts": now(), "level": level, "text": str(text)[:800]}))

    def save_cfg(self):
        self.db.set_cfg(self.cfg)

    def rules(self, enabled_only=True):
        rows = self.db.q("SELECT * FROM rules" + (" WHERE enabled=1" if enabled_only else ""))
        out = []
        for r in rows:
            d = json.loads(DEFAULT_RULE and json.dumps(DEFAULT_RULE))
            d.update(json.loads(r["json"]))
            d["id"], d["enabled"], d["hits"] = r["id"], r["enabled"], r["hits"]
            out.append(d)
        return out

    # ---------- provider / model ----------
    def provider(self, name):
        for p in self.cfg["providers"]:
            if p["name"] == name:
                return p
        return self.cfg["providers"][0] if self.cfg["providers"] else None

    async def list_models(self, base_url, api_key):
        base = base_url.rstrip("/")
        hdr = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # OpenAI-compatible
        try:
            async with self.http.get(f"{base}/models", headers=hdr, timeout=aiohttp.ClientTimeout(total=25)) as r:
                if r.status == 200:
                    j = await r.json()
                    ids = [m.get("id") for m in j.get("data", j if isinstance(j, list) else [])]
                    ids = sorted([i for i in ids if i])
                    if ids:
                        return ids
                body = (await r.text())[:200]
        except Exception as e:
            body = str(e)[:200]
        # Ollama native
        try:
            root = base[:-3] if base.endswith("/v1") else base
            async with self.http.get(f"{root}/api/tags", timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    j = await r.json()
                    return sorted(m["name"] for m in j.get("models", []))
        except Exception:
            pass
        raise RuntimeError(f"拉取模型失败: {body}")

    async def chat(self, provider_name, model, messages, json_mode=False, max_tokens=800, rule="-"):
        p = self.provider(provider_name) or {}
        base = (p.get("base_url") or "").rstrip("/")
        if not base or not model:
            raise RuntimeError("未配置 provider/model")
        cap = self.cfg.get("ai_daily_call_cap", 500)
        used = self.db.q("SELECT COUNT(*) n FROM aiusage WHERE ts>?", (now() - 86400,))[0]["n"]
        if used >= cap:
            raise RuntimeError(f"已达今日调用上限 {cap}")
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.3}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        hdr = {"Content-Type": "application/json"}
        if p.get("api_key"):
            hdr["Authorization"] = "Bearer " + p["api_key"]
        t0 = now()
        try:
            async with self.http.post(f"{base}/chat/completions", headers=hdr, json=body,
                                      timeout=aiohttp.ClientTimeout(total=120)) as r:
                txt = await r.text()
                if r.status != 200:
                    raise RuntimeError(f"{r.status} {txt[:300]}")
                j = json.loads(txt)
            content = j["choices"][0]["message"].get("content") or ""
            u = j.get("usage") or {}
            self.db.x("INSERT INTO aiusage(ts,rule,model,in_tok,out_tok,ok,err) VALUES(?,?,?,?,?,1,'')",
                      (t0, rule, model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)))
            return content
        except Exception as e:
            self.db.x("INSERT INTO aiusage(ts,rule,model,in_tok,out_tok,ok,err) VALUES(?,?,?,0,0,0,?)",
                      (t0, rule, model, str(e)[:300]))
            raise

    # ---------- discord rest ----------
    def dc_headers(self):
        tok = self.cfg["discord"]["token"]
        prefix = "" if self.cfg["discord"].get("mode") == "user" else "Bot "
        return {"Authorization": prefix + tok, "Content-Type": "application/json"}

    async def channel_info(self, cid):
        if cid in self.chan_cache:
            return self.chan_cache[cid]
        try:
            async with self.http.get(f"{API}/channels/{cid}", headers=self.dc_headers()) as r:
                j = await r.json() if r.status == 200 else {}
        except Exception:
            j = {}
        info = {"name": j.get("name") or cid, "parent_id": j.get("parent_id"),
                "is_thread": j.get("type") in (10, 11, 12)}
        self.chan_cache[cid] = info
        return info

    async def send_message(self, channel_id, content, reply_to=None):
        mode = self.cfg["discord"].get("mode")
        if not self.cfg["discord"]["token"]:
            raise RuntimeError("发消息需要 Bot Token（浏览器旁听模式只能收，不能发）")
        if mode == "user":
            self.log("warn", "正在用个人账号 Token 发消息——这是 selfbot 行为，封号风险最高的一步")
        body = {"content": content[:1900]}
        if reply_to:
            body["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
        async with self.http.post(f"{API}/channels/{channel_id}/messages",
                                  headers=self.dc_headers(), json=body) as r:
            if r.status not in (200, 201):
                raise RuntimeError(f"send failed {r.status} {(await r.text())[:200]}")
            return await r.json()

    # ---------- rule engine ----------
    def match(self, rule, ev):
        """ev: normalized event dict. returns (ok, reason)"""
        if rule.get("source", "discord") != ev["source"]:
            return False, "source"
        if rule["ignore_bots"] and ev["is_bot"]:
            return False, "bot"
        if ev["is_dm"]:
            if not rule["dm"]:
                return False, "dm-off"
        else:
            if rule["dm"] and not (rule["guild_ids"] or rule["channel_ids"] or rule["thread_ids"]):
                return False, "dm-only"
            if rule["guild_ids"] and ev["guild_id"] not in rule["guild_ids"]:
                return False, "guild"
            ch_ok = True
            if rule["channel_ids"]:
                ch_ok = ev["channel_id"] in rule["channel_ids"] or (
                    rule["include_threads_of_channels"] and ev["parent_id"] in rule["channel_ids"])
            th_ok = True
            if rule["thread_ids"]:
                th_ok = ev["channel_id"] in rule["thread_ids"]
            if rule["channel_ids"] and rule["thread_ids"]:
                if not (ch_ok or th_ok):
                    return False, "channel/thread"
            elif not (ch_ok and th_ok):
                return False, "channel/thread"
        if rule["author_ids"] and ev["author_id"] not in rule["author_ids"]:
            return False, "author"
        if rule["author_name_contains"] and rule["author_name_contains"].lower() not in ev["author"].lower():
            return False, "author-name"
        if rule["mention_only"] and not ev["mentions_me"]:
            return False, "mention"
        c = ev["content"] or ""
        if len(c) < int(rule.get("min_len") or 0):
            return False, "len"
        low = c.lower()
        if rule["keywords_any"] and not any(k.lower() in low for k in rule["keywords_any"] if k):
            return False, "kw-any"
        if rule["keywords_all"] and not all(k.lower() in low for k in rule["keywords_all"] if k):
            return False, "kw-all"
        if rule["regex"]:
            try:
                if not re.search(rule["regex"], c, re.I | re.S):
                    return False, "regex"
            except re.error as e:
                return False, f"bad regex: {e}"
        return True, "命中"

    def rate_ok(self, rule):
        key = str(rule.get("id"))
        hist = [t for t in self.rate.get(key, []) if t > now() - 3600]
        if hist and now() - hist[-1] < rule["cooldown_sec"]:
            return False
        if len(hist) >= rule["max_per_hour"]:
            return False
        hist.append(now())
        self.rate[key] = hist
        return True

    async def handle_event(self, ev):
        """Store + run rules. ev is normalized."""
        matched, ai_json, score = [], None, None
        for rule in self.rules():
            ok, _ = self.match(rule, ev)
            if not ok:
                continue
            matched.append(rule["name"])
            self.db.x("UPDATE rules SET hits=hits+1 WHERE id=?", (rule["id"],))
            act = rule["action"]
            if act in ("ai_tag", "ai_reply", "ai_extract", "ai_summary") and not self.rate_ok(rule):
                self.log("warn", f"{rule['name']}: 冷却/限流跳过 AI")
                continue
            try:
                if act == "ai_tag":
                    ai_json = await self.act_tag(rule, ev)
                    score = (ai_json or {}).get("score")
                elif act == "ai_reply":
                    await self.act_reply(rule, ev)
                elif act == "ai_extract":
                    ai_json = await self.act_extract(rule, ev)
                elif act == "ai_summary":
                    await self.act_summary(rule, ev)
                elif act == "webhook":
                    await self.act_webhook(rule, ev)
            except Exception as e:
                self.log("error", f"{rule['name']} 执行失败: {e}")
        mid = None
        with contextlib.suppress(sqlite3.IntegrityError):
            mid = self.db.x(
                """INSERT INTO messages(source,msg_id,guild_id,channel_id,channel_name,parent_id,
                   is_thread,author_id,author,is_bot,content,ts,matched,ai_json,score)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ev["source"], ev["msg_id"], ev["guild_id"], ev["channel_id"], ev["channel_name"],
                 ev["parent_id"], int(ev["is_thread"]), ev["author_id"], ev["author"], int(ev["is_bot"]),
                 ev["content"], ev["ts"], ",".join(matched),
                 json.dumps(ai_json, ensure_ascii=False) if ai_json else None, score))
        row = dict(ev, id=mid, matched=",".join(matched), ai=ai_json, score=score)
        alert = bool(matched)
        if score is not None:
            mins = [r["notify_min_score"] for r in self.rules() if r["name"] in matched]
            alert = score >= min(mins or [0])
        row["alert"] = alert
        await self.bus.push("message", row)
        if alert:
            # 通知/转发不能拖住收信循环：丢后台跑，失败只记日志
            asyncio.create_task(self.notify(ev, row))

    def ctx_text(self, ev, n=12):
        rows = self.db.q("SELECT author,content FROM messages WHERE channel_id=? ORDER BY ts DESC LIMIT ?",
                         (ev["channel_id"], n))[::-1]
        return "\n".join(f"{r['author']}: {r['content']}" for r in rows)

    def pick_model(self, rule):
        prov = rule.get("provider") or self.cfg["default_model"]["provider"]
        model = rule.get("model") or self.cfg["default_model"]["model"]
        return prov, model

    async def act_tag(self, rule, ev):
        prov, model = self.pick_model(rule)
        sysmsg = rule["prompt"] or (
            "你是消息分流助手。判断这条 Discord 消息对我是否重要。"
            "只输出 JSON: {\"score\":0-100,\"tags\":[\"...\"],\"reason\":\"一句话\",\"todo\":\"若需我行动则写，否则空\"}")
        out = await self.chat(prov, model, [
            {"role": "system", "content": sysmsg},
            {"role": "user", "content": f"频道: {ev['channel_name']}\n作者: {ev['author']}\n内容: {ev['content']}"}],
            json_mode=True, max_tokens=300, rule=rule["name"])
        try:
            return json.loads(re.search(r"\{.*\}", out, re.S).group(0))
        except Exception:
            return {"score": 50, "tags": [], "reason": out[:200]}

    async def act_reply(self, rule, ev):
        prov, model = self.pick_model(rule)
        sysmsg = rule["prompt"] or "你是该频道的助手，用简洁中文回答，不超过 120 字。"
        out = await self.chat(prov, model, [
            {"role": "system", "content": sysmsg},
            {"role": "user", "content": f"最近对话:\n{self.ctx_text(ev)}\n\n请回复 {ev['author']} 的最后一条消息。"}],
            max_tokens=500, rule=rule["name"])
        await self.send_message(ev["channel_id"], out, reply_to=ev["msg_id"] if rule["reply_in_thread"] else None)
        self.log("info", f"{rule['name']}: 已在 #{ev['channel_name']} 回复")

    async def act_extract(self, rule, ev):
        prov, model = self.pick_model(rule)
        sysmsg = rule["prompt"] or "从消息中抽取结构化信息，只输出 JSON。字段自定，找不到就留空。"
        out = await self.chat(prov, model, [{"role": "system", "content": sysmsg},
                                            {"role": "user", "content": ev["content"]}],
                              json_mode=True, max_tokens=500, rule=rule["name"])
        try:
            return json.loads(re.search(r"\{.*\}", out, re.S).group(0))
        except Exception:
            return {"raw": out[:300]}

    async def act_summary(self, rule, ev):
        key = f"{rule['id']}:{ev['channel_id']}"
        buf = self.sum_buf.setdefault(key, [])
        buf.append(f"{ev['author']}: {ev['content']}")
        if len(buf) < int(rule["summary_every"] or 20):
            return
        prov, model = self.pick_model(rule)
        sysmsg = rule["prompt"] or "把这段 Discord 对话压成 3-5 条要点中文摘要，标出待办和结论。"
        out = await self.chat(prov, model, [{"role": "system", "content": sysmsg},
                                            {"role": "user", "content": "\n".join(buf)}],
                              max_tokens=700, rule=rule["name"])
        self.sum_buf[key] = []
        self.log("info", f"摘要 #{ev['channel_name']}: {out[:200]}")
        await self.bus.push("summary", {"channel": ev["channel_name"], "text": out, "ts": now()})

    async def act_webhook(self, rule, ev):
        url = rule["webhook_url"] or self.cfg["sinks"].get("webhook")
        if not url:
            return
        with contextlib.suppress(Exception):
            await self.http.post(url, json={"rule": rule["name"], "event": ev},
                                 timeout=aiohttp.ClientTimeout(total=10))

    # ================= 出口：本机通知 + 往外转发 =================
    def in_quiet_hours(self):
        s = self.cfg["sinks"]
        a, b = (s.get("quiet_from") or "").strip(), (s.get("quiet_to") or "").strip()

        def mins(t):
            try:
                h, m = t.split(":")
                return int(h) * 60 + int(m)
            except Exception:
                return None
        lo, hi = mins(a), mins(b)
        if lo is None or hi is None:
            return False
        lt = time.localtime()
        cur = lt.tm_hour * 60 + lt.tm_min
        return lo <= cur < hi if lo <= hi else (cur >= lo or cur < hi)

    def fmt_msg(self, ev, score=None, ai=None):
        where = "#" + (ev.get("channel_name") or "?") + ("（子区）" if ev.get("is_thread") else "")
        head = f"{ev.get('author') or '?'} 在 {where}"
        body = (ev.get("content") or "(无文字)")[:600]
        extra = ""
        if score is not None:
            extra += f"\n重要度 {score}"
        if ai and ai.get("tags"):
            extra += ("　" if extra else "\n") + "、".join(str(t) for t in ai["tags"][:4])
        if ai and ai.get("todo"):
            extra += f"\n待办：{ai['todo']}"
        return head, body, extra, ev.get("url") or ""

    async def notify(self, ev, row):
        """命中后的统一出口。任何一个出口挂了都只记日志，不影响其它出口。"""
        s = self.cfg["sinks"]
        score = row.get("score")
        if score is not None and score < int(s.get("min_score") or 0):
            return
        head, body, extra, url = self.fmt_msg(ev, score, row.get("ai"))
        text = f"{head}\n{body}{extra}" + (f"\n{url}" if url else "")
        jobs = {}
        if not self.in_quiet_hours():
            if s.get("sound"):
                jobs["提示音"] = self.local_sound()
            if s.get("toast"):
                jobs["系统通知"] = self.local_toast(head, body[:180])
        if s.get("discord_webhook"):
            jobs["Discord"] = self.push_discord_webhook(s["discord_webhook"], head, body, extra, url)
        tg = s.get("telegram") or {}
        if tg.get("token") and tg.get("chat_id"):
            jobs["Telegram"] = self.push_telegram(tg, text)
        if s.get("serverchan"):
            jobs["微信(Server酱)"] = self.push_serverchan(s["serverchan"], head, f"{body}{extra}\n\n{url}")
        if s.get("wecom"):
            jobs["企业微信"] = self.push_wecom(s["wecom"], text)
        if s.get("webhook"):
            jobs["自定义"] = self.push_json(s["webhook"], {
                "event": ev, "score": score, "ai": row.get("ai"), "matched": row.get("matched")})
        if not jobs:
            return
        for name, res in zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)):
            if isinstance(res, Exception):
                self.log("error", f"{name} 发送失败: {res}")

    async def _post(self, url, **kw):
        async with self.http.post(url, timeout=aiohttp.ClientTimeout(total=15), **kw) as r:
            t = await r.text()
            if r.status >= 300:
                raise RuntimeError(f"{r.status} {t[:150]}")
            return t

    async def push_discord_webhook(self, url, head, body, extra, link):
        embed = {"title": head[:250], "description": (body + extra)[:3900], "color": 0xC96442}
        if link:
            embed["url"] = link
        await self._post(url, json={"username": "dcwatch", "embeds": [embed]})

    async def push_telegram(self, tg, text):
        await self._post(f"https://api.telegram.org/bot{tg['token'].strip()}/sendMessage",
                         json={"chat_id": tg["chat_id"].strip(), "text": text[:4000],
                               "disable_web_page_preview": True})

    async def push_serverchan(self, key, title, desp):
        key = key.strip()
        m = re.match(r"^sctp(\d+)t", key)      # Server酱³ 的 SendKey 形如 sctp123t...
        url = f"https://{m.group(1)}.push.ft07.com/send" if m else f"https://sctapi.ftqq.com/{key}.send"
        out = await self._post(url, data={"title": title[:100], "desp": desp[:2000]})
        with contextlib.suppress(Exception):
            j = json.loads(out)
            if j.get("code") not in (0, None):
                raise RuntimeError(str(j)[:150])

    async def push_wecom(self, url, text):
        out = await self._post(url, json={"msgtype": "text", "text": {"content": text[:1900]}})
        with contextlib.suppress(Exception):
            j = json.loads(out)
            if j.get("errcode"):
                raise RuntimeError(str(j)[:150])

    async def push_json(self, url, payload):
        await self._post(url, json=payload)

    async def local_sound(self):
        f = (self.cfg["sinks"].get("sound_file") or "").strip()

        def play():
            if IS_WIN:
                import winsound
                if f and Path(f).exists():
                    winsound.PlaySound(f, winsound.SND_FILENAME | winsound.SND_ASYNC)
                else:
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
            else:
                sys.stdout.write("\a")
                sys.stdout.flush()
        await asyncio.get_running_loop().run_in_executor(None, play)

    async def local_toast(self, title, body, force=False):
        """Win10/11 原生通知。不装任何库：走 PowerShell 的 WinRT 接口。"""
        if not force and now() - self._last_toast < 4:
            return                                  # 防刷屏：4 秒内只弹一条
        self._last_toast = now()
        if not IS_WIN:
            self.log("info", f"[通知] {title} — {body[:60]}")   # 非 Windows 只记日志
            return
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if not exe:
            raise RuntimeError("找不到 powershell")
        env = dict(os.environ, DCW_TITLE=str(title)[:120], DCW_BODY=str(body)[:250])
        p = await asyncio.create_subprocess_exec(
            exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", TOAST_B64,
            env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0))
        _, err = await asyncio.wait_for(p.communicate(), timeout=20)
        if p.returncode:
            raise RuntimeError((err or b"").decode("utf-8", "ignore")[:200] or f"powershell exit {p.returncode}")


class DiscordListener:
    """Minimal gateway client: identify, heartbeat, resume, MESSAGE_CREATE."""
    def __init__(self, app: App):
        self.app = app
        self.task = None
        self.ws = None
        self.state = "stopped"
        self.me = {}
        self.seq = None
        self.sid = None
        self.resume_url = None
        self.stop_flag = False

    def start(self):
        if self.task and not self.task.done():
            return
        self.stop_flag = False
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        self.stop_flag = True
        if self.ws:
            with contextlib.suppress(Exception):
                await self.ws.close()
        if self.task:
            self.task.cancel()
            with contextlib.suppress(Exception):
                await self.task
        self.state = "stopped"
        await self.app.bus.push("status", self.status())

    def status(self):
        return {"source": "discord", "state": self.state, "user": self.me.get("username", ""),
                "guilds": len(getattr(self, "guilds", []) or [])}

    async def run(self):
        backoff = 1
        while not self.stop_flag:
            try:
                await self.connect_once()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.state = "error"
                self.app.log("error", f"gateway: {e}")
                await self.app.bus.push("status", self.status())
                await asyncio.sleep(backoff + random.random())
                backoff = min(backoff * 2, 60)

    async def connect_once(self):
        token = self.app.cfg["discord"]["token"].strip()
        if not token:
            raise RuntimeError("未填 Discord Bot Token")
        url = (self.resume_url or "wss://gateway.discord.gg") + "/?v=10&encoding=json"
        self.state = "connecting"
        await self.app.bus.push("status", self.status())
        async with self.app.http.ws_connect(url, heartbeat=None, max_msg_size=0) as ws:
            self.ws = ws
            hello = json.loads(await ws.receive_str())
            interval = hello["d"]["heartbeat_interval"] / 1000
            hb = asyncio.create_task(self.heartbeat(ws, interval))
            if self.sid:
                await ws.send_json({"op": 6, "d": {"token": token, "session_id": self.sid, "seq": self.seq}})
            elif self.app.cfg["discord"].get("mode") == "user":
                # 个人账号 identify：不带 intents，账号能看见的一切都会推过来。
                # 只读用途；本程序在 user 模式下不主动发消息。
                await ws.send_json({"op": 2, "d": {
                    "token": token, "capabilities": 161789, "compress": False,
                    "properties": {"os": "Windows", "browser": "Chrome", "device": "",
                                   "system_locale": "zh-CN", "browser_user_agent": UA,
                                   "browser_version": "127.0.0.0", "os_version": "10",
                                   "referrer": "", "referring_domain": "",
                                   "release_channel": "stable", "client_build_number": 320000,
                                   "client_event_source": None},
                    "presence": {"status": "unknown", "since": 0, "activities": [], "afk": False},
                    "client_state": {"guild_versions": {}}}})
            else:
                await ws.send_json({"op": 2, "d": {"token": token, "intents": INTENTS,
                                                   "properties": {"os": "linux", "browser": "dcwatch",
                                                                  "device": "dcwatch"}}})
            try:
                async for raw in ws:
                    if raw.type is not aiohttp.WSMsgType.TEXT:
                        break
                    p = json.loads(raw.data)
                    if p.get("s"):
                        self.seq = p["s"]
                    op, t, d = p.get("op"), p.get("t"), p.get("d") or {}
                    if op == 0:
                        await self.dispatch(t, d)
                    elif op == 1:
                        await ws.send_json({"op": 1, "d": self.seq})
                    elif op == 7:
                        break
                    elif op == 9:
                        self.sid = None
                        self.resume_url = None
                        await asyncio.sleep(2)
                        break
            finally:
                hb.cancel()
                self.ws = None
            code = ws.close_code
            hint = {4004: "Token 无效或已被重置", 4013: "intents 值不合法",
                    4014: "开发者后台没勾选 MESSAGE CONTENT INTENT（Privileged Intents）",
                    4008: "被限流", 4010: "分片参数错误"}.get(code)
            if hint:
                self.app.log("error", f"Discord 关闭连接 {code}：{hint}")
                if code in (4004, 4013, 4014):   # 重连也没用，停下等人改配置
                    self.stop_flag = True
                    self.state = "error"
                    await self.app.bus.push("status", self.status())

    async def heartbeat(self, ws, interval):
        await asyncio.sleep(interval * random.random())
        while True:
            with contextlib.suppress(Exception):
                await ws.send_json({"op": 1, "d": self.seq})
            await asyncio.sleep(interval)

    async def dispatch(self, t, d):
        app = self.app
        if t == "READY":
            self.me = d.get("user", {})
            self.sid = d.get("session_id")
            self.resume_url = d.get("resume_gateway_url")
            self.guilds = d.get("guilds", [])
            self.state = "online"
            app.log("info", f"已连接 Discord: {self.me.get('username')} ({len(self.guilds)} 服务器)")
            await app.bus.push("status", self.status())
            return
        if t == "RESUMED":
            self.state = "online"
            await app.bus.push("status", self.status())
            return
        if t in ("THREAD_CREATE", "THREAD_UPDATE"):
            app.chan_cache[d["id"]] = {"name": d.get("name") or d["id"],
                                       "parent_id": d.get("parent_id"), "is_thread": True}
            return
        if t not in ("MESSAGE_CREATE", "MESSAGE_UPDATE"):
            return
        if not app.cfg["sources"].get("discord", True):
            return
        au = d.get("author") or {}
        if au.get("id") == self.me.get("id"):
            return
        info = await app.channel_info(d["channel_id"])
        ev = {
            "source": "discord",
            "msg_id": d["id"] + ("u" if t == "MESSAGE_UPDATE" else ""),
            "guild_id": d.get("guild_id") or "",
            "channel_id": d["channel_id"],
            "channel_name": info["name"],
            "parent_id": info.get("parent_id") or "",
            "is_thread": bool(info.get("is_thread")),
            "author_id": au.get("id", ""),
            "author": au.get("global_name") or au.get("username", ""),
            "is_bot": bool(au.get("bot")),
            "content": d.get("content") or "",
            "ts": now(),
            "is_dm": not d.get("guild_id"),
            "mentions_me": any(m.get("id") == self.me.get("id") for m in d.get("mentions", [])),
            "url": f"https://discord.com/channels/{d.get('guild_id','@me')}/{d['channel_id']}/{d['id']}",
        }
        await app.handle_event(ev)


# ============================ HTTP API ============================
SINK_SECRETS = ("discord_webhook", "serverchan", "wecom", "webhook")


def safe_cfg(cfg):
    c = json.loads(json.dumps(cfg))
    if c["discord"].get("token"):
        c["discord"]["token"] = "***" + c["discord"]["token"][-4:]
    for p in c["providers"]:
        if p.get("api_key"):
            p["api_key"] = "***" + p["api_key"][-4:]
    s = c.get("sinks") or {}
    for k in SINK_SECRETS:
        if s.get(k):
            s[k] = "***" + str(s[k])[-6:]
    if (s.get("telegram") or {}).get("token"):
        s["telegram"]["token"] = "***" + s["telegram"]["token"][-4:]
    return c


def unmask_sinks(patch, cur):
    """界面回传的掩码值不能把真值冲掉。"""
    s = patch.get("sinks")
    if not isinstance(s, dict):
        return
    old = cur.get("sinks") or {}
    for k in SINK_SECRETS:
        if str(s.get(k, "")).startswith("***"):
            s[k] = old.get(k, "")
    tg, otg = s.get("telegram") or {}, old.get("telegram") or {}
    if str(tg.get("token", "")).startswith("***"):
        tg["token"] = otg.get("token", "")


def routes(app: App):
    r = web.RouteTableDef()

    @r.get("/")
    async def index(_):
        return web.FileResponse(BASE / "ui.html")

    @r.get("/api/state")
    async def state(_):
        st = app.dc.status() if app.dc else {"source": "discord", "state": "stopped"}
        fresh = now() - app.last_ingest < 90
        br = {"source": "browser", "state": "online" if fresh else "stopped",
              "last": app.last_ingest, "count": app.ingest_count}
        return web.json_response({
            "config": safe_cfg(app.cfg),
            "status": {"discord": st, "browser": br},
            "env": {"win": IS_WIN, "frozen": FROZEN, "data_dir": str(DATA_DIR), "port": app.port},
            "rules": app.rules(enabled_only=False),
            "stats": {
                "msgs": app.db.q("SELECT COUNT(*) n FROM messages")[0]["n"],
                "matched": app.db.q("SELECT COUNT(*) n FROM messages WHERE matched<>''")[0]["n"],
                "ai_today": app.db.q("SELECT COUNT(*) n FROM aiusage WHERE ts>?", (now() - 86400,))[0]["n"],
                "ai_cap": app.cfg.get("ai_daily_call_cap", 500),
            },
        })

    @r.post("/api/config")
    async def setcfg(req):
        patch = await req.json()
        # never overwrite a secret with its masked form
        if "discord" in patch and str(patch["discord"].get("token", "")).startswith("***"):
            patch["discord"]["token"] = app.cfg["discord"]["token"]
        if "providers" in patch:
            for p in patch["providers"]:
                if str(p.get("api_key", "")).startswith("***"):
                    old = app.provider(p["name"]) or {}
                    p["api_key"] = old.get("api_key", "")
        unmask_sinks(patch, app.cfg)
        if isinstance(patch.get("sinks"), dict):     # 局部更新，别把没传的字段抹掉
            merged = dict(app.cfg.get("sinks") or {})
            merged.update(patch["sinks"])
            patch["sinks"] = merged
        app.cfg.update(patch)
        app.save_cfg()
        if "discord" in patch:
            if app.cfg["discord"]["enabled"] and app.cfg["discord"].get("mode") in ("bot", "user"):
                app.dc.sid = None
                await app.dc.stop()
                app.dc.start()
            else:
                await app.dc.stop()
        return web.json_response({"ok": True, "config": safe_cfg(app.cfg)})

    @r.post("/api/models")
    async def models(req):
        b = await req.json()
        p = app.provider(b.get("provider", "")) or {}
        base = b.get("base_url") or p.get("base_url", "")
        key = b.get("api_key") or ""
        if not key or key.startswith("***"):
            key = p.get("api_key", "")
        try:
            return web.json_response({"ok": True, "models": await app.list_models(base, key)})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=200)

    @r.get("/api/messages")
    async def msgs(req):
        lim = int(req.query.get("limit", 200))
        only = req.query.get("matched") == "1"
        sql = "SELECT * FROM messages" + (" WHERE matched<>''" if only else "") + " ORDER BY ts DESC LIMIT ?"
        rows = app.db.q(sql, (lim,))
        for x in rows:
            x["ai"] = json.loads(x["ai_json"]) if x["ai_json"] else None
        return web.json_response({"messages": rows})

    @r.post("/api/messages/clear")
    async def clr(_):
        app.db.x("DELETE FROM messages")
        return web.json_response({"ok": True})

    @r.post("/api/rules")
    async def saverule(req):
        b = await req.json()
        rid = b.pop("id", None)
        enabled = int(b.pop("enabled", 1))
        b.pop("hits", None)
        blob = json.dumps(b, ensure_ascii=False)
        if rid:
            app.db.x("UPDATE rules SET json=?,enabled=? WHERE id=?", (blob, enabled, rid))
        else:
            rid = app.db.x("INSERT INTO rules(json,enabled) VALUES(?,?)", (blob, enabled))
        return web.json_response({"ok": True, "id": rid, "rules": app.rules(False)})

    @r.delete("/api/rules/{rid}")
    async def delrule(req):
        app.db.x("DELETE FROM rules WHERE id=?", (req.match_info["rid"],))
        return web.json_response({"ok": True, "rules": app.rules(False)})

    @r.post("/api/rules/test")
    async def testrule(req):
        b = await req.json()
        rule = dict(DEFAULT_RULE, **b.get("rule", {}))
        s = b.get("sample", {})
        ev = {"source": "discord", "guild_id": s.get("guild_id", ""), "channel_id": s.get("channel_id", ""),
              "parent_id": s.get("parent_id", ""), "channel_name": s.get("channel_name", "test"),
              "is_thread": bool(s.get("parent_id")), "author_id": s.get("author_id", ""),
              "author": s.get("author", "someone"), "is_bot": bool(s.get("is_bot")),
              "content": s.get("content", ""), "is_dm": bool(s.get("is_dm")),
              "mentions_me": bool(s.get("mentions_me")), "ts": now(), "msg_id": "0"}
        ok, why = app.match(rule, ev)
        return web.json_response({"match": ok, "why": why})

    @r.get("/api/discord/tree")
    async def tree(_):
        """List guilds -> text channels -> active threads, so IDs can be picked in the GUI."""
        if not app.cfg["discord"]["token"]:
            return web.json_response({"ok": False, "error": "先填 Bot Token"})
        out = []
        try:
            async with app.http.get(f"{API}/users/@me/guilds", headers=app.dc_headers()) as rr:
                if rr.status != 200:
                    return web.json_response({"ok": False, "error": f"{rr.status} {(await rr.text())[:200]}"})
                guilds = await rr.json()
            for g in guilds[:20]:
                node = {"id": g["id"], "name": g["name"], "channels": []}
                async with app.http.get(f"{API}/guilds/{g['id']}/channels", headers=app.dc_headers()) as rr:
                    chans = await rr.json() if rr.status == 200 else []
                threads = []
                async with app.http.get(f"{API}/guilds/{g['id']}/threads/active", headers=app.dc_headers()) as rr:
                    if rr.status == 200:
                        threads = (await rr.json()).get("threads", [])
                for c in chans:
                    if c.get("type") not in (0, 5, 15):
                        continue
                    node["channels"].append({
                        "id": c["id"], "name": c["name"],
                        "threads": [{"id": t["id"], "name": t["name"]}
                                    for t in threads if t.get("parent_id") == c["id"]]})
                out.append(node)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})
        return web.json_response({"ok": True, "guilds": out})

    @r.post("/api/source/toggle")
    async def toggle(req):
        b = await req.json()
        name, on = b["source"], bool(b["on"])
        app.cfg["sources"][name] = on
        if name == "discord":
            app.cfg["discord"]["enabled"] = on
            if on:
                app.dc.start()
            else:
                await app.dc.stop()
        app.save_cfg()
        return web.json_response({"ok": True, "config": safe_cfg(app.cfg)})

    @r.post("/api/ask")
    async def ask(req):
        b = await req.json()
        prov = b.get("provider") or app.cfg["default_model"]["provider"]
        model = b.get("model") or app.cfg["default_model"]["model"]
        ids = b.get("msg_ids") or []
        ctx = ""
        if ids:
            qm = ",".join("?" * len(ids))
            rows = app.db.q(f"SELECT author,content,channel_name FROM messages WHERE id IN ({qm}) ORDER BY ts", ids)
            ctx = "\n".join(f"[#{x['channel_name']}] {x['author']}: {x['content']}" for x in rows)
        msgs = [{"role": "system", "content": b.get("system") or "你是我的 Discord 消息助理，用中文简洁回答。"}]
        msgs.append({"role": "user", "content": (f"消息上下文:\n{ctx}\n\n" if ctx else "") + b.get("prompt", "")})
        try:
            return web.json_response({"ok": True, "text": await app.chat(prov, model, msgs, max_tokens=1200,
                                                                        rule="manual")})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    @r.post("/api/reply")
    async def reply(req):
        b = await req.json()
        try:
            await app.send_message(b["channel_id"], b["content"], b.get("reply_to"))
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "content-type",
            "Access-Control-Allow-Methods": "POST, OPTIONS"}

    @r.options("/api/ingest")
    async def ingest_pre(_):
        return web.Response(headers=CORS)

    @r.post("/api/ingest")
    async def ingest(req):
        """外部来源投递消息（Chrome 扩展旁听网页版 Discord / 任何脚本）。
        走完全相同的规则引擎，所以规则、AI 动作、通知都不用改。"""
        b = await req.json()
        if b.get("ping"):
            app.last_ingest = now()
            return web.json_response({"ok": True, "pong": True}, headers=CORS)
        if not app.cfg["sources"].get("browser", True):
            return web.json_response({"ok": False, "error": "浏览器旁听已关闭"}, headers=CORS)
        items = b.get("messages") or [b]
        n = 0
        for m in items:
            if not m.get("msg_id") or not (m.get("content") or "").strip():
                continue
            cid = str(m.get("channel_id") or "")
            ev = {"source": "discord", "via": "browser",
                  "msg_id": "b" + str(m["msg_id"]), "guild_id": str(m.get("guild_id") or ""),
                  "channel_id": cid, "channel_name": m.get("channel_name") or cid,
                  "parent_id": str(m.get("parent_id") or ""), "is_thread": bool(m.get("is_thread")),
                  "author_id": str(m.get("author_id") or ""), "author": m.get("author") or "?",
                  "is_bot": bool(m.get("is_bot")), "content": m["content"], "ts": now(),
                  "is_dm": bool(m.get("is_dm")), "mentions_me": bool(m.get("mentions_me")),
                  "url": m.get("url") or ""}
            await app.handle_event(ev)
            n += 1
        app.last_ingest = now()
        app.ingest_count += n
        return web.json_response({"ok": True, "accepted": n}, headers=CORS)

    @r.post("/api/sinks/test")
    async def sinktest(req):
        """按一下就知道哪个出口通了。which=all 或单个出口名。"""
        b = await req.json()
        which = b.get("which", "all")
        s = app.cfg["sinks"]
        head = "dcwatch 测试通知"
        body = "看到/听到这条，说明这个出口通了。"
        text = f"{head}\n{body}"
        jobs = {}
        if which in ("all", "sound"):
            jobs["提示音"] = app.local_sound()
        if which in ("all", "toast"):
            jobs["系统通知"] = app.local_toast(head, body, force=True)
        if which in ("all", "discord_webhook") and s.get("discord_webhook"):
            jobs["Discord"] = app.push_discord_webhook(s["discord_webhook"], head, body, "", "")
        tg = s.get("telegram") or {}
        if which in ("all", "telegram") and tg.get("token") and tg.get("chat_id"):
            jobs["Telegram"] = app.push_telegram(tg, text)
        if which in ("all", "serverchan") and s.get("serverchan"):
            jobs["微信(Server酱)"] = app.push_serverchan(s["serverchan"], head, body)
        if which in ("all", "wecom") and s.get("wecom"):
            jobs["企业微信"] = app.push_wecom(s["wecom"], text)
        if which in ("all", "webhook") and s.get("webhook"):
            jobs["自定义"] = app.push_json(s["webhook"], {"test": True, "text": text})
        if not jobs:
            return web.json_response({"ok": False, "error": "这个出口还没填/没开"})
        out = {}
        for name, res in zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)):
            out[name] = "ok" if not isinstance(res, Exception) else str(res)[:200]
        if app.in_quiet_hours() and which in ("all", "sound", "toast"):
            out["注意"] = "当前在免打扰时段，实际收信时本机不会响"
        return web.json_response({"ok": True, "results": out})

    @r.get("/api/logs")
    async def logs(_):
        return web.json_response({"logs": app.db.q("SELECT * FROM logs ORDER BY id DESC LIMIT 200")})

    @r.get("/api/events")
    async def events(req):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                           "Cache-Control": "no-cache", "Connection": "keep-alive"})
        await resp.prepare(req)
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        app.bus.subs.add(q)
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25)
                    await resp.write(f"data: {payload}\n\n".encode())
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            app.bus.subs.discard(q)
        return resp

    return r


async def housekeeping(app: App):
    while True:
        days = int(app.cfg.get("retention_days", 14))
        app.db.x("DELETE FROM messages WHERE ts < ?", (now() - days * 86400,))
        app.db.x("DELETE FROM logs WHERE ts < ?", (now() - 3 * 86400,))
        await asyncio.sleep(3600)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--open", action="store_true", help="启动后自动打开界面（exe 默认就开）")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()

    app = App()
    app.port = a.port
    web_app = web.Application(client_max_size=2 * 1024 * 1024)
    web_app.add_routes(routes(app))

    async def on_start(_):
        app.http = aiohttp.ClientSession()
        app.dc = DiscordListener(app)
        asyncio.create_task(housekeeping(app))
        if (app.cfg["discord"]["enabled"] and app.cfg["discord"]["token"]
                and app.cfg["discord"].get("mode") in ("bot", "user")):
            app.dc.start()

    async def on_stop(_):
        if app.dc:
            await app.dc.stop()
        if app.http:
            await app.http.close()

    web_app.on_startup.append(on_start)
    web_app.on_cleanup.append(on_stop)
    runner = web.AppRunner(web_app, access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, a.host, a.port).start()
    except OSError as e:
        print(f"端口 {a.port} 起不来（{e}）。dcwatch 可能已经在跑了，"
              f"直接开 http://127.0.0.1:{a.port} ；或者换端口：--port 8778", flush=True)
        await runner.cleanup()
        if FROZEN:
            with contextlib.suppress(Exception):
                input("按回车退出…")
        return
    url = f"http://127.0.0.1:{a.port}"
    print(f"dcwatch -> {url}   (数据: {DB_PATH})", flush=True)
    if (a.open or FROZEN) and not a.no_open:
        with contextlib.suppress(Exception):
            webbrowser.open(url)
    if FROZEN:
        print("这个黑窗口关掉就等于停止监听；要一直收信就让它留着（可以最小化）。", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
