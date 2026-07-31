#!/usr/bin/env python3
"""
dcwatch - lightweight Discord watcher + LLM workbench.

One process: Discord gateway listener + rule engine + OpenAI-compatible model client
+ local web GUI. Storage = SQLite. Only dependency: aiohttp.

Run:  python server.py            -> http://127.0.0.1:8777
"""
import asyncio, base64, json, os, re, sqlite3, sys, time, argparse, contextlib, random, shutil, webbrowser
import urllib.parse
import html as html_mod
from pathlib import Path

try:
    import aiohttp
    from aiohttp import web
except ImportError:
    sys.exit("need aiohttp:  pip install aiohttp")

VERSION = "1.7.1"                               # 服务端版本，界面和扩展都能看到
EXT_MIN = "1.7.0"                               # 低于这个版本的扩展要提示用户更新
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
START_TS = time.time()
API = "https://discord.com/api/v10"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")
# GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES | MESSAGE_CONTENT
INTENTS = (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)  # 37377


# ---------- 自己占多少内存（不装 psutil） ----------
def rss_mb():
    try:
        if IS_WIN:
            import ctypes
            from ctypes import wintypes

            class MEM(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            m = MEM()
            m.cb = ctypes.sizeof(m)
            ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(m), m.cb)
            return round(m.WorkingSetSize / 1048576, 1)
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 1)
    except Exception:
        pass
    return 0.0


# ---------- 开机自启动（只有 Windows 有；写当前用户的 Run 键，不需要管理员） ----------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "dcwatch"


def autostart_cmd(port=None):
    port = port or 8777
    if FROZEN:
        return f'"{sys.executable}" --minimized --port {port}'
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")                     # 用 pythonw 才不会弹黑窗
    exe = pyw if IS_WIN and pyw.exists() else py
    return f'"{exe}" "{Path(__file__).resolve()}" --minimized --port {port}'


def autostart_state():
    if not IS_WIN:
        return {"supported": False, "on": False, "cmd": ""}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v = winreg.QueryValueEx(k, AUTOSTART_NAME)[0]
        return {"supported": True, "on": True, "cmd": v}
    except FileNotFoundError:
        return {"supported": True, "on": False, "cmd": ""}
    except Exception as e:
        return {"supported": True, "on": False, "cmd": "", "error": str(e)[:120]}


def autostart_set(on, port=None):
    if not IS_WIN:
        raise RuntimeError("开机自启动只在 Windows 上支持")
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, autostart_cmd(port))
        else:
            with contextlib.suppress(FileNotFoundError):
                winreg.DeleteValue(k, AUTOSTART_NAME)
    return autostart_state()


def hide_console():
    """--minimized：把自己的黑窗口藏起来，后台安静收信。"""
    if not IS_WIN:
        return
    with contextlib.suppress(Exception):
        import ctypes
        wnd = ctypes.windll.kernel32.GetConsoleWindow()
        if wnd:
            ctypes.windll.user32.ShowWindow(wnd, 0)       # SW_HIDE


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
    # 出网设置。proxy 留空＝跟随系统环境变量；填了就强制走它（形如 http://127.0.0.1:7890）
    "net": {"proxy": ""},
    # 出口：命中并达到 min_score 的消息往哪儿送
    "sinks": {
        "browser": True,            # 网页通知（要开着界面）
        "toast": True,              # Windows 系统通知
        "sound": True,              # 提示音
        "sound_name": "builtin:ding",  # 提示音；"" = 系统默认，custom:xx.wav = 自己导入的
        "sound_file": "",           # 老版本遗留：自定义 .wav 绝对路径
        "quiet_from": "", "quiet_to": "",   # 免打扰时段 "23:00"→"08:00"，只静音本机，不影响转发
        "min_score": 0,             # AI 打分低于这个不往外发；没有分数的一律发
        # 转发出口：一条一个 HTTP 请求，谁都能接。见 HOOK_FIELDS
        "hooks": [],
    },
    # 内置提示词：规则里不单独写 prompt 时用这里的。界面上可改、可一键恢复默认。
    "prompts": {},
    # AI 工作台上那排快捷按钮。名字和内容全可改，可删可加可排序；空列表 = 一个按钮都不显示。
    "quick_actions": [
        {"id": "q1", "name": "总结选中消息", "text": "把这些消息压成 3-5 条要点，标出需要我行动的部分。"},
        {"id": "q2", "name": "抽出待办", "text": "从这些消息里抽出待办清单，每条含负责人和截止时间，没写就标未定。"},
        {"id": "q3", "name": "起草回复", "text": "针对最后一条消息起草一条中文回复，语气专业简洁，≤120 字。"},
        {"id": "q4", "name": "翻译成中文", "text": "把这些消息翻译成中文，保留原作者名。"},
    ],
    "models_cache": {},        # 每个服务商拉到过的模型名，纯缓存
    "retention_days": 14,
    "ai_daily_call_cap": 500,
}

DEFAULT_PROMPTS = {
    "ai_tag": ("你是消息分流助手。判断这条 Discord 消息对我是否重要。"
               "只输出 JSON: {\"score\":0-100,\"tags\":[\"...\"],\"reason\":\"一句话\","
               "\"todo\":\"若需我行动则写，否则空\"}"),
    "ai_reply": "你是该频道的助手，用简洁中文回答，不超过 120 字。",
    "ai_summary": "把这段 Discord 对话压成 3-5 条要点中文摘要，标出待办和结论。",
    "ai_extract": "从消息中抽取结构化信息，只输出 JSON。字段自定，找不到就留空。",
    # 工作台的身份和边界在 WORKBENCH_SYS 里（写死，不让改）。这条只是用户想追加的偏好，默认空。
    "ask": "",
}

# ---------- 转发出口 ----------
# 一条出口 = 一个 HTTP 请求。URL / 请求头 / 请求体里写 {{占位符}}，命中时替换成真值。
# 这样任何能收 webhook 的东西都能对接，程序里不写死任何第三方服务。
HOOK_FIELDS = {"id": "", "name": "", "url": "", "method": "POST", "content": "json",
               "headers": "", "body": '{"content": "{{text}}"}',
               "enabled": True, "verified": False}
HOOK_SIG = ("url", "method", "content", "headers", "body")   # 这几项一改，测试结果就作废
HOOK_VARS = ("text", "title", "body", "author", "channel", "server", "content",
             "url", "score", "tags", "todo", "json")


RAW_VARS = ("json",)      # 本身就是一段 JSON，不能再当字符串转义


def render_tpl(tpl: str, vals: dict, quote: bool) -> str:
    """{{name}} → 值。quote=True 时按 JSON 字符串转义，模板整体仍是合法 JSON。"""
    def sub(m):
        k = m.group(1)
        v = vals.get(k, "")
        v = "" if v is None else str(v)
        return json.dumps(v)[1:-1] if quote and k not in RAW_VARS else v
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", sub, tpl)


def norm_hook(h: dict) -> dict:
    out = dict(HOOK_FIELDS)
    for k, d in HOOK_FIELDS.items():
        v = h.get(k, d)
        out[k] = bool(v) if isinstance(d, bool) else str(v if v is not None else d)
    out["method"] = "GET" if out["method"].upper() == "GET" else "POST"
    if out["content"] not in ("json", "form", "text"):
        out["content"] = "json"
    out["name"] = out["name"].strip()[:40] or "出口"
    out["id"] = out["id"] or f"h{random.randrange(16**8):08x}"
    return out


def merge_hooks(new, old):
    """保存出口时：改过关键字段的，测试通过状态作废，得重新测。"""
    by_id = {h.get("id"): h for h in (old or []) if h.get("id")}
    out = []
    for h in new:
        h = norm_hook(h)
        o = by_id.get(h["id"])
        h["verified"] = bool(o and o.get("verified") and all(o.get(k) == h[k] for k in HOOK_SIG))
        out.append(h)
    return out[:20]


def norm_quick(items) -> list:
    """工作台快捷按钮：名字和内容都由用户填，这里只做长度和去空。"""
    out = []
    for i, q in enumerate(items if isinstance(items, list) else []):
        if not isinstance(q, dict):
            continue
        name, text = str(q.get("name") or "").strip()[:20], str(q.get("text") or "").strip()[:2000]
        if not name or not text:
            continue                     # 名字或内容空的直接丢，免得界面上出现空按钮
        out.append({"id": str(q.get("id") or f"q{i + 1}")[:16], "name": name, "text": text})
    return out[:12]                      # 一排放不下太多，12 个够了


def migrate_sinks(s: dict):
    """老版本配置里那几个写死的出口字段，搬进统一的 hooks 列表。"""
    hooks, dc = list(s.get("hooks") or []), s.pop("discord_webhook", "")
    tg, hook = s.pop("telegram", None) or {}, s.pop("webhook", "")
    sc, wc = s.pop("serverchan", ""), s.pop("wecom", "")
    if sc:      # Server酱 SendKey → 微信服务号
        hooks.append({"name": "微信(Server酱)", "content": "form", "body": "title={{title}}&desp={{body}}",
                      "url": f"https://sctapi.ftqq.com/{sc}.send"})
    if wc:      # 企业微信群机器人
        hooks.append({"name": "企业微信", "url": wc,
                      "body": '{"msgtype": "text", "text": {"content": "{{text}}"}}'})
    if dc:
        hooks.append({"name": "Discord 频道", "url": dc, "body": DISCORD_BODY})
    if tg.get("token") and tg.get("chat_id"):
        hooks.append({"name": "Telegram", "body": '{"chat_id": "%s", "text": "{{text}}"}' % tg["chat_id"],
                      "url": f"https://api.telegram.org/bot{tg['token']}/sendMessage"})
    if hook:
        hooks.append({"name": "自定义", "url": hook, "body": "{{json}}"})
    s["hooks"] = [norm_hook(h) for h in hooks]


DISCORD_BODY = ('{"username": "dcwatch",\n'
                ' "embeds": [{"title": "{{title}}", "description": "{{body}}", "color": 13198914}]}')

# 内置提示音（打包进程序），加上用户导入的（放数据目录）
SOUND_LABELS = {"ding": "叮（清脆单音）", "double": "双响（两声短促）", "soft": "柔和（和弦木琴）",
                "rise": "上扬（有事找你）", "low": "低沉（夜里不刺耳）", "knock": "敲击（像敲门）"}
BUILTIN_SOUNDS = BASE / "sounds"
USER_SOUNDS = DATA_DIR / "sounds_custom"   # 和内置目录分开，别混在一起
SOUND_MAX_BYTES = 2 * 1024 * 1024      # 单个提示音最大 2MB
SOUND_MAX_SECONDS = 6                  # 最长 6 秒，前端超了会让你截片段


def sound_path(name):
    """把 sound_name 解析成真实文件；"" 或找不到 → None（用系统默认提示音）。"""
    name = (name or "").strip()
    if not name:
        return None
    kind, _, base = name.partition(":")
    base = Path(base or kind).name                        # 防目录穿越
    if kind == "custom":
        p = USER_SOUNDS / base
    else:
        p = BUILTIN_SOUNDS / (base if base.endswith(".wav") else base + ".wav")
    return p if p.exists() else None


def list_sounds():
    out = [{"id": "", "label": "系统默认提示音", "kind": "system"}]
    for f in sorted(BUILTIN_SOUNDS.glob("*.wav")):
        out.append({"id": f"builtin:{f.stem}", "label": SOUND_LABELS.get(f.stem, f.stem),
                    "kind": "builtin", "bytes": f.stat().st_size})
    for f in sorted(USER_SOUNDS.glob("*.wav")):
        out.append({"id": f"custom:{f.name}", "label": f.stem, "kind": "custom",
                    "bytes": f.stat().st_size})
    return out


def wav_seconds(raw):
    """只用标准库校验这是真 wav，并算出时长。"""
    import io, wave
    with wave.open(io.BytesIO(raw)) as w:
        return w.getnframes() / float(w.getframerate() or 1)

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
  content TEXT, ts REAL, matched TEXT, ai_json TEXT, score INT, unread INT DEFAULT 1,
  account TEXT DEFAULT '', bridge TEXT DEFAULT '',
  -- kind: msg=普通消息 thread=新帖/新子区。scanned=1 是历史回扫抓来的，不触发规则也不提醒
  kind TEXT DEFAULT 'msg', scanned INT DEFAULT 0);
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
        # 老库补列（1.0 的库没有 account/bridge）
        have = {r[1] for r in self.c.execute("PRAGMA table_info(messages)")}
        for col in ("account", "bridge"):
            if col not in have:
                self.c.execute(f"ALTER TABLE messages ADD COLUMN {col} TEXT DEFAULT ''")
        if "kind" not in have:
            self.c.execute("ALTER TABLE messages ADD COLUMN kind TEXT DEFAULT 'msg'")
        if "scanned" not in have:
            self.c.execute("ALTER TABLE messages ADD COLUMN scanned INT DEFAULT 0")
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
    # 触发类型：msg=有人发消息 thread=开了新帖/新子区（论坛频道的新帖也算）
    "kinds": ["msg"],
    "guild_ids": [], "channel_ids": [], "thread_ids": [],
    "include_threads_of_channels": True,   # channel_ids also match its threads (子区)
    "dm": False,
    # ---- WHO ----
    "accounts": [],       # 只听这些 Discord 账号（空＝全部）。多号监控用它分流
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


ACTIONS = ("notify", "ai_tag", "ai_reply", "ai_summary", "ai_extract", "webhook")

COMPOSE_SYS = """你把用户一句中文需求，翻译成 dcwatch 的监听规则 JSON。只输出 JSON，不要解释。

输出格式：{"rule": {...}, "notes": ["给用户看的中文提醒，可空"]}

rule 里只允许这些字段（不确定的就别写，别编）：
  name              规则名，短，中文
  guild_ids         服务器 ID 数组 | channel_ids 频道 ID 数组 | thread_ids 子区 ID 数组
  include_threads_of_channels  true 时 channel_ids 也匹配这些频道下的所有子区
  dm                true = 包含私信
  author_ids        用户 ID 数组 | author_name_contains 昵称包含（字符串）
  ignore_bots       默认 true；用户说"包括机器人"才 false
  mention_only      true = 只在 @我 时才算命中
  keywords_any      含任一即命中 | keywords_all 必须全含 | regex 正则 | min_len 最短字数
  action            只能是 notify / ai_tag / ai_reply / ai_summary / ai_extract / webhook
  prompt            要让模型干的活写这里（中文），留空用内置提示词
  notify_min_score  action=ai_tag 时，低于这个分不提醒（0-100）
  summary_every     action=ai_summary 时，攒够几条做一次摘要
  cooldown_sec / max_per_hour   防刷屏，AI 动作建议 15-60 / 20-40

动作怎么选：
  只想被提醒 → notify
  想让模型判断重要度、打标签、过滤噪音 → ai_tag（同时给 notify_min_score，一般 60）
  想自动替我回复 → ai_reply
  水群频道想定期总结 → ai_summary
  想把消息抽成结构化字段 → ai_extract
  想转发到外部系统 → webhook

铁律：
1. ID 全是 17-20 位纯数字。**只能用下面「已知的频道和人」里给出的 ID**；用户没提供也不在列表里的，
   就把对应字段留空，并在 notes 里写清楚"要限定哪个频道/谁，请把 ID 填进「听哪里/听谁」"。绝对不许编 ID。
2. 用户说"某人"但列表里查不到 → 用 author_name_contains 填昵称，并在 notes 里说明用户 ID 更准。
3. action=ai_reply 时，notes 里必须提醒：这会用他的身份公开发言，建议先观察几天；且浏览器旁听模式发不出去，需要 Bot Token。
"""


WIZARD_SYS = """你是 dcwatch 的「规则向导」。用户通常说不清自己要什么，
你的工作是**先把需求问清楚，再生成规则**——像一个有经验的同事帮他理需求，而不是拿到一句话就闷头输出。

## 只输出下面两种 JSON 之一，前后不要有任何别的文字

信息还不够，要问：
{"stage":"ask",
 "understood":"一句话复述你现在的理解，让用户能纠正你",
 "questions":[
   {"key":"where","q":"要盯哪里？","why":"不限定范围的话，整个服务器的消息都会进来",
    "options":["就那一个帖子/子区","这个频道以及它下面所有帖子","整个服务器"],
    "suggest":"这个频道以及它下面所有帖子","skippable":false}],
 "assumed":["我先按这些假设：忽略机器人发的、不含私信"]}

信息够了，出规则：
{"stage":"done",
 "rule":{...见下方字段表...},
 "catches":["会命中：有人在 #交易 发『有多余的会员送人』"],
 "misses":["不会命中：别人只发一张图没有文字"],
 "notes":["要注意的事"],
 "verify":"教用户怎么自己验一遍"}

## 提问纪律（很重要）
- 一轮最多问 3 个，只问最影响结果的。**总共别超过 3 轮**，够用就出规则；用户催就立刻出。
- 每个问题必须给 options 和 suggest。用户答「都行 / 不确定 / 你决定」时，直接采用 suggest，不要再追问同一件事。
- 说大白话，不要提字段名。不要问用户答不出的东西。
- 需要频道/子区 ID 时：先看下面「已知的频道和人」，有就直接用。没有就把这一项设成
  skippable=true，并在 q 里教他怎么取（在 Discord 里右键那个频道或帖子标题 → 复制频道 ID；
  或者干脆先在那个频道说一句话，dcwatch 见过它就能自动认出来），同时告诉他跳过的后果。
- 用户已经说过的，不要再问一遍。别问跟规则无关的（比如用哪个模型）。

## 你必须自己过一遍的维度（缺哪个才问）
0 触发类型 kinds：`["msg"]` 有人发消息（默认）/ `["thread"]` 有人开新帖或新子区 / 两个都要就都写。
  他说「有新帖就告诉我」「谁发了新贴子」→ 只要 `["thread"]`，这种规则**不需要关键词**，
  开帖本身就是事件；想按标题筛才加 keywords_any（新帖的"内容"就是帖子标题）。
  他说的是帖子**里面**的聊天 → 那还是 `["msg"]` + include_threads_of_channels。
  另外：他要的若是「已经发过的消息里找东西」，规则做不到 —— 直接告诉他去用「抓历史 + 批量提取」，
  别硬造一条规则糊弄他。
1 范围：哪个服务器 / 频道 / 子区（帖子）？要不要连这个频道下面的帖子一起？私信算不算（默认不算）
2 人：任何人 / 只某几个人 / 排除机器人（默认排除）/ 要不要连他自己发的也算
3 触发内容 —— 这里最容易做错：
   · 明确的词（"发版"、"报错"）→ keywords_any 就够
   · **模糊意图**（"有人捐用不完的套餐"、"有人在吵架"、"有价值的消息"）→ 千万别只写死关键词。
     正确做法：keywords_any 放 10~20 个同义词/口语/英文做**粗筛**，action 用 ai_tag，
     把判定标准写进 prompt，再用 notify_min_score 卡阈值（一般 60）。
     理由：死关键词漏得多，全交给 AI 又慢又贵，粗筛 + AI 判定才是准的。
4 强度：要不要 @我 才算？最短字数（滤掉"哈哈""+1""？"）
5 动作：只提醒 / AI 判重要度打分 / 定期摘要 / 抽成结构化字段 / 转发到外部 / 自动回复
6 频率：cooldown_sec、max_per_hour、notify_min_score。会刷屏的地方必须给冷却
7 边界（用户最容易漏，你要主动问）：**什么情况不该提醒？**
   让他举一个"这种就别烦我"的例子，把它变成更严的条件或写进 notes
8 时间：免打扰时段是全局设置、不在规则里，只在 notes 里提一句

## 领域经验（用户想不到，你要主动补上）
- 「捐/送/转让 用不完的套餐、会员、名额、车位」：说法极多——捐、送、白送、出、转、让、
  分享、有富余、多的、闲置、代、蹲、求、拼、车位、give away, spare, unused, free。
  粗筛词要列全，再用 ai_tag 判"是不是真的在无偿给出可用资源"。提醒用户：这种帖子开头常是
  "有人要吗"，别只盯"捐"字。
- 抢名额 / 限量：时效强 → cooldown_sec 给 5，别把 max_per_hour 设太低，宁可多提醒
- 报错 / 求助：min_len 给 8 以上，滤掉"救""？""在吗"
- 老板或客户点名：常见 mention_only=true，但对方也可能不 @ 直接叫名字
  → 建议在 keywords_any 里补上他平时怎么称呼你
- 水群频道：用 ai_summary + summary_every 20，别一条条提醒
- 空投 / 白名单 / 公告：**ignore_bots 必须设成 false**，这类消息基本都是机器人发的——最容易漏的一条
- 只盯一个已有的帖子 → thread_ids 填那个帖子的 ID
  想"这个频道以后新开的帖子都算" → channel_ids + include_threads_of_channels=true
- 多个 Discord 账号在旁听时，可以用 accounts 只听某个号

## rule 允许的字段（不确定就别写，别编）
  name 规则名（短，中文）
  guild_ids / channel_ids / thread_ids   服务器 / 频道 / 子区(帖子) ID 数组
  include_threads_of_channels  true 时 channel_ids 也匹配这些频道下的所有帖子
  dm  true = 包含私信
  accounts  只听这些 Discord 账号名（多号旁听时用）
  author_ids  用户 ID 数组 | author_name_contains  昵称包含
  ignore_bots  默认 true | mention_only  true = 只在 @我 时命中
  keywords_any 含任一即命中 | keywords_all 必须全含 | regex 正则 | min_len 最短字数
  action  只能是 notify / ai_tag / ai_reply / ai_summary / ai_extract / webhook
  prompt  要模型干的活（中文，判定标准写清楚）
  notify_min_score  ai_tag 时低于这个分不提醒（0-100）
  summary_every  ai_summary 时攒够几条做一次
  cooldown_sec / max_per_hour  防刷屏

## 铁律
1. ID 只能用「已知的频道和人」里给的，或用户自己在对话里贴出的 17~20 位数字。**绝对不许编 ID**。
   没有就把字段留空，并在 notes 里写清楚该怎么补。
2. action 只能是上面那六个。
3. done 时必须给 catches 和 misses，让用户能判断你有没有理解错。
4. 生成 ≠ 生效：notes 里要提醒「保存后先用试算验一下，再观察一会儿」。
5. action=ai_reply 必须警告：这会用他本人的身份公开发言；浏览器旁听模式根本发不出去，需要 Bot Token。
"""


WORKBENCH_SYS = """你是 dcwatch 里「AI 工作台」的助手。

## 你在什么地方
dcwatch 是一个**已经装在用户自己电脑上、正在运行**的 Discord 监听程序。用户不是在跟一个通用聊天
机器人说话，他是在自己的监听工具里问话。你看到的这段对话发生在这个程序的界面里。

## 最重要的一条：监听是程序做的，不是你做的
用户说「帮我监听某个频道」，**不是**要你去访问 Discord。是要你告诉他在这个程序里怎么配置。

所以下面这些回答是**错的，绝对不许出现**：
- 「我无法访问第三方平台 / 我没有联网能力 / 我不能实时抓取消息」——
  这话答非所问：能抓消息的是他手上这个程序，不是你。
- 建议他「创建一个 Discord 机器人」「用 Webhook / API 集成」「用 Zapier 或消息导出工具」——
  他要的功能这个程序**已经有了**，把他推去用别的工具是纯粹的帮倒忙。

正确做法：直接说「好，这个频道要盯的话，你需要……」然后给出这个程序里的具体步骤。

## 用户贴 Discord 链接时怎么读
`discord.com/channels/<A>/<B>`：A 是服务器 ID，B 是频道 ID（A 是 `@me` 时是私信）。
末尾还有第三段数字就是具体某条消息。程序已经帮你把链接拆好放在下面的实况里了，直接用。

盯一个频道要满足两件事，缺哪件就先说哪件：
1. **消息得先进得来**：浏览器旁听模式下，dcwatch 只能看到「他自己浏览器里打开着的那个频道」的新消息。
   所以他得装好扩展、并且**让那个频道在 Discord 里开着**。装完扩展必须在 Discord 页面按一次 F5。
   下面实况里会写现在有没有浏览器在旁听。
2. **要有一条规则命中它**：规则里「听哪里」填上那个频道 ID，再写清听什么内容。

## 「帮我写规则」指的是什么
在这里，「规则」永远是 **dcwatch 的监听规则**：什么消息该提醒他。
**不是**群聊管理规则、不是游戏规则、不是服务器公约。绝对不要反问「你指的是群规还是游戏规则」。

一条规则由这几件事组成，你可以据此提问或解释：
听哪里（频道/服务器/私信）、听谁（作者、要不要算机器人）、内容怎么匹配（关键词、正则、@我、
纯 AI 判断）、强度（分数阈值）、命中后做什么（只提醒 / AI 打标签 / 摘要 / 抽取 / 自动回复 / 转发）、
频率（冷却时间）、边界（什么情况**别**提醒）、时间（只在某些时段生效）。

他要建规则时，最好的回答是**指路加追问**：告诉他「监听规则」页顶部有个**「◆ 帮我建条规则」**的
向导，会一步步问清楚再生成；同时你自己也可以先问他一两个关键的（盯哪个频道、什么词、要不要算机器人）。
你在这里说的规则内容不会自动生效 —— 规则必须在「监听规则」页保存，这点要讲明白。

## 两件容易答错的事：新帖，和已经过去的消息
1. **「有人开了新帖就提醒我」** —— 这个程序做得到，别说做不到。规则里第 0 项「什么时候触发」
   有两个勾：「有人发消息」和「有人开了新帖 / 新子区」。只想要开帖提醒就只勾第二个。
   前提是那个**论坛频道的帖子列表**在他浏览器里开着（旁听只看得见开着的页面）。
2. **「把这个帖子里所有人发的密钥都挑出来」** —— 这是**已经过去的消息**，规则管不着
   （规则只管以后新来的）。正确指路是两步：
   a. 在 Discord 那个帖子页面，点右下角 dcwatch 药丸里的**「抓历史」**，它会自动往上翻并把消息存进来；
   b. 到侧栏**「批量提取」**页，选上那个频道、写清要挑什么，点开始。结果能导成 CSV。
   抓历史进来的消息**只入库、不会触发规则也不弹通知**，所以不会突然刷屏。
   批量提取还会核对每条结果是否真的在原文里出现过，没对上的会标红 —— 提醒他别直接用标红的。
判断标准很简单：他要的是「以后」→ 规则；「已经有的」→ 抓历史 + 批量提取。

## 你能做和不能做
能：解读下面给你的消息、总结、抽待办、起草回复、解释这个程序里的功能和设置在哪、帮他想规则怎么写、
把他贴的链接拆成 ID 告诉他填哪里。
不能：上网查东西、代替他点界面上的按钮、直接改他的配置。要发言到 Discord 得他自己点「回复」按钮，
而且浏览器旁听模式发不出去（那需要 Bot Token）。

## 说话方式
中文，简洁，直接给能照着做的步骤。不要列一堆他没问的备选方案。
不知道或者程序里确实没有的功能，就直说没有，别编一个设置项出来。
"""


def parse_discord_links(text):
    """把用户贴的频道/消息链接拆成 ID。工作台里他十次有九次是直接贴链接的。"""
    out = []
    for g, c, m in re.findall(r"discord(?:app)?\.com/channels/(@me|\d{5,25})/(\d{5,25})(?:/(\d{5,25}))?",
                              str(text or "")):
        out.append({"guild": g, "channel": c, "msg": m})
    return out


def loose_json(txt):
    """模型经常给带 ``` 或前后废话的 JSON。尽量捞出来，捞不到就抛。"""
    t = (txt or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        raise ValueError("模型没有输出 JSON")
    return json.loads(m.group(0))


def sanitize_draft(draft, notes, known_ids, model=""):
    """把模型给的规则草稿洗成合法规则。编出来的 ID、不认识的动作一律挡掉。"""
    rule = dict(DEFAULT_RULE)
    for k, v in (draft.items() if isinstance(draft, dict) else []):
        if k not in DEFAULT_RULE or v is None:
            continue
        d = DEFAULT_RULE[k]
        if isinstance(d, list):
            vals = [str(x).strip() for x in (v if isinstance(v, list) else [v]) if str(x).strip()]
            if k.endswith("_ids"):        # 编出来的 ID 一律扔掉，免得规则悄悄匹配不到
                bad = [x for x in vals if not (x.isdigit() and x in known_ids)]
                vals = [x for x in vals if x.isdigit() and x in known_ids]
                if bad:
                    notes.append(f"模型给的 {k} 里有查不到的 ID（{', '.join(bad[:3])}），已丢掉——"
                                 f"这个字段请你自己填，或先让那个频道来一条消息")
            rule[k] = vals
        elif isinstance(d, bool):
            rule[k] = bool(v)
        elif isinstance(d, int):
            with contextlib.suppress(Exception):
                rule[k] = int(v)
        else:
            rule[k] = str(v)
    kinds = [k for k in rule.get("kinds") or [] if k in ("msg", "thread")]
    if not kinds:
        kinds = ["msg"]
    rule["kinds"] = kinds
    if kinds == ["thread"]:
        notes.append("这条只在**有人开新帖/新子区**时触发，帖子里后续聊什么都不管。"
                     "要认出新帖，那个论坛频道的帖子列表得在浏览器里开着")
    if rule["action"] not in ACTIONS:
        notes.append(f"模型给的动作「{rule['action']}」不认识，先按「只提醒我」处理")
        rule["action"] = "notify"
    if rule["action"] == "ai_reply":
        notes.append("自动回复会用你的身份公开发言，建议先默认停用观察几天；"
                     "另外浏览器旁听模式发不出去，需要 Bot Token")
    if not (rule["guild_ids"] or rule["channel_ids"] or rule["thread_ids"] or rule["dm"]):
        notes.append("没限定频道：现在是所有能收到的地方都会触发。要收窄就把频道 ID 填进「听哪里」")
    if rule["action"].startswith("ai") and not model:
        notes.append("还没设默认模型，这条规则要先在「模型接入」里选一个模型")
    return rule, notes


def ext_dir():
    """扩展目录：源码版在 BASE 下；exe 版优先打进包里的，其次 exe 旁边，最后数据目录。"""
    for p in (BASE / "extension", Path(sys.executable).resolve().parent / "extension",
              DATA_DIR / "extension"):
        if p.is_dir():
            return p
    return None


def ext_version():
    """磁盘上那份扩展的版本。下载按钮给出去的就是这一份 —— 必须让人看得见，
    否则从 GitHub 或旧包里拿到老扩展，界面上完全看不出来。"""
    d = ext_dir()
    if not d:
        return ""
    try:
        return str(json.loads((d / "manifest.json").read_text(encoding="utf-8")).get("version") or "")
    except Exception:
        return ""


def cmp_ver(a, b):
    """1.7.0 vs 1.10.0 这种比较不能用字符串比。界面里也有一份同样的实现。"""
    def p(s):
        out = []
        for x in str(s).split(".")[:3]:
            try:
                out.append(int(x))
            except ValueError:
                out.append(0)
        return out + [0] * (3 - len(out))
    x, y = p(a), p(b)
    return (x > y) - (x < y)


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
        migrate_sinks(self.cfg.setdefault("sinks", {}))   # 老配置里的写死出口 → 统一 hooks
        self.bus = Bus()
        self.http: aiohttp.ClientSession | None = None
        self.dc: DiscordListener | None = None
        self.chan_cache: dict[str, dict] = {}
        self.rate: dict[str, list] = {}
        self.sum_buf: dict[str, list] = {}
        self.last_ingest = 0.0        # 浏览器旁听最后一次投递时间（所有桥里最新的那次）
        self.ingest_count = 0
        self.started = now()          # 用于诊断包里的「已跑多久」
        self.bridges = {}             # 每个装了扩展的浏览器 = 一个桥，独立跟踪
        self._inflight = set()        # 正在处理的 msg_id，防两个桥同时报同一条
        self._last_toast = 0.0        # 系统通知防刷屏
        # 本机提醒合并：涌进来一批消息时攒成一条通知 + 一次提示音。
        # 以前提示音一条一条播（PlaySound 是异步的），十几条叠在一起就是一片电音；
        # 通知那边则是 4 秒内直接丢掉多余的，等于漏消息。两个都不对。
        self._pend = []               # [(head, body)] 等着合并发出去的
        self._pend_task = None
        self._pend_sound = False
        self.port = 8777

    def touch_bridge(self, b, n=0, err=""):
        """记下这个桥的身份和心跳。多浏览器、多账号就靠它区分。"""
        bid = str(b.get("bridge") or "anon")[:64]
        br = self.bridges.setdefault(bid, {"id": bid, "count": 0, "first": now()})
        for k, lim in (("ver", 16), ("browser", 40), ("account", 60), ("account_id", 32), ("where", 60)):
            v = str(b.get(k) or "")[:lim]
            if v or k not in br:
                br[k] = v
        br["last"] = now()
        br["count"] += n
        br["err"] = err
        if isinstance(b.get("stats"), dict):      # 扩展侧的实情，导出诊断时要用
            br["stats"] = b["stats"]
        self.last_ingest = now()
        return br

    def bridge_list(self):
        out = []
        for br in sorted(self.bridges.values(), key=lambda x: -x.get("last", 0)):
            out.append(dict(br, fresh=now() - br.get("last", 0) < 90,
                            ago=round(now() - br.get("last", 0), 1)))
        return out

    def log(self, level, text):
        self.db.x("INSERT INTO logs(ts,level,text) VALUES(?,?,?)", (now(), level, str(text)[:800]))
        print(f"[{level}] {text}", flush=True)
        asyncio.create_task(self.bus.push("log", {"ts": now(), "level": level, "text": str(text)[:800]}))

    def rule_ctx(self, extra=""):
        """喂给建规则模型的现场情况：见过哪些频道/人、什么模式、有哪些出口。
        只有这里出现过的 ID 才允许写进规则，别的一律当编的。"""
        chans = self.db.q("""SELECT channel_id id, channel_name nm, parent_id, COUNT(*) n, MAX(ts) t
                             FROM messages GROUP BY channel_id ORDER BY t DESC LIMIT 40""")
        people = self.db.q("""SELECT author_id id, author nm, COUNT(*) n, MAX(ts) t FROM messages
                              WHERE author_id<>'' GROUP BY author_id ORDER BY t DESC LIMIT 40""")
        known = {c["id"] for c in chans} | {p["id"] for p in people}
        known |= {c["parent_id"] for c in chans if c["parent_id"]}
        known |= set(re.findall(r"\d{15,25}", extra or ""))      # 用户自己粘的 ID 也算真的
        L = ["已知的频道和人（只能用这里的 ID，其它一律留空）："]
        L += [f"频道 {c['nm']} = {c['id']}"
              + (f"（是 {c['parent_id']} 的子区/帖子）" if c["parent_id"] else "")
              + f"，收到过 {c['n']} 条" for c in chans] or ["（本机还没收到过任何消息，没有可用 ID）"]
        L += [f"人 {p['nm']} = {p['id']}，发过 {p['n']} 条" for p in people]
        mode = self.cfg["discord"].get("mode", "browser")
        L.append({"browser": "当前是浏览器旁听模式：能收不能发，action 不要用 ai_reply。",
                  "bot": "当前是 Bot Token 模式：能收也能发。",
                  "user": "当前是用户 Token 模式：能收也能发（有风险）。"}.get(mode, ""))
        accs = sorted({b.get("account") for b in self.bridges.values() if b.get("account")})
        if accs:
            L.append("正在旁听的 Discord 账号：" + "、".join(accs))
        hooks = [h.get("name") or h.get("url", "") for h in self.cfg.get("hooks", []) if h.get("enabled")]
        L.append("已配好的转发出口：" + ("、".join(hooks) if hooks else "（没有，action=webhook 要先去「通知与转发」加）"))
        return "\n".join(x for x in L if x), known

    def workbench_ctx(self, user_text=""):
        """工作台的实况：模型得知道这台机器上现在到底是什么状况，
        否则它只能泛泛而谈，甚至反过来劝用户去装别的工具。"""
        L = [f"dcwatch 版本 v{VERSION}。"]
        mode = self.cfg["discord"].get("mode", "browser")
        L.append({"browser": "收信方式：浏览器旁听（装在 Chrome 里的扩展替他读页面）。"
                             "这种模式只能看到他浏览器里**开着的那个频道**的新消息，而且发不出消息。",
                  "bot": "收信方式：Bot Token（能收也能发）。",
                  "user": "收信方式：个人账号 Token（能收也能发，有封号风险）。"}.get(mode, ""))
        brs = [b for b in self.bridges.values() if now() - b.get("last", 0) < 90]
        if brs:
            who = "、".join(sorted({b.get("account") or "未知账号" for b in brs}))
            where = "、".join(sorted({b.get("where") or "" for b in brs if b.get("where")}))
            L.append(f"现在有 {len(brs)} 个浏览器正在旁听（账号：{who}）" + (f"，当前打开的频道：{where}" if where else ""))
        else:
            L.append("**现在没有任何浏览器在旁听** —— 也就是说一条消息都进不来。"
                     "他要盯频道的话，第一件事是装扩展并在 Discord 页面按 F5，不是写规则。")
        rs = self.rules(False)
        if rs:
            L.append("他已有的规则：" + "；".join(
                f"{r.get('name') or '未命名'}（{'开' if r.get('enabled') else '关'}，命中 {r.get('hits', 0)} 次）"
                for r in rs[:12]))
        else:
            L.append("他还没有任何规则 —— 所以就算消息进来了也不会有提醒。")
        chans = self.db.q("""SELECT channel_id id, channel_name nm, COUNT(*) n FROM messages
                             GROUP BY channel_id ORDER BY MAX(ts) DESC LIMIT 12""")
        if chans:
            L.append("最近收到过消息的频道：" + "、".join(f"{c['nm']}={c['id']}（{c['n']} 条）" for c in chans))
        for k in parse_discord_links(user_text):
            if k["guild"] == "@me":
                L.append(f"他这句话里贴了一个**私信**链接：对话 ID {k['channel']}。"
                         "提醒他规则默认不听私信，要在「听哪里」勾上「包含私信 DM」。")
            else:
                L.append(f"他这句话里贴了一个 Discord 链接，已拆好：服务器 ID {k['guild']}，"
                         f"频道 ID {k['channel']}"
                         + (f"，消息 ID {k['msg']}" if k["msg"] else "")
                         + "。直接用这个频道 ID 告诉他填在规则的「听哪里」里，别让他自己去找 ID。")
        return "\n".join(x for x in L if x)

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

    def rq(self, **kw):
        """出网请求的公共参数：显式代理优先，没填就靠 trust_env 认系统代理。"""
        px = ((self.cfg.get("net") or {}).get("proxy") or "").strip()
        if px:
            kw["proxy"] = px
        return kw

    @staticmethod
    def base_candidates(base_url):
        """用户粘什么都尽量认：少了 /v1、多了 /chat/completions、末尾多斜杠，都试一遍。"""
        b = (base_url or "").strip().rstrip("/")
        for tail in ("/chat/completions", "/completions", "/models"):
            if b.endswith(tail):
                b = b[: -len(tail)].rstrip("/")
        out = [b]
        if not b.endswith("/v1"):
            out.append(b + "/v1")
        return [x for x in out if x]

    async def list_models(self, base_url, api_key):
        hdr = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        tried = []
        for base in self.base_candidates(base_url):
            try:
                async with self.http.get(f"{base}/models", headers=hdr,
                                         timeout=aiohttp.ClientTimeout(total=25), **self.rq()) as r:
                    if r.status == 200:
                        j = await r.json(content_type=None)
                        ids = [m.get("id") for m in j.get("data", j if isinstance(j, list) else [])]
                        ids = sorted([i for i in ids if i])
                        if ids:
                            self.models_base = base       # 记下真正能用的那个
                            return ids
                    tried.append(f"{base}/models -> HTTP {r.status} {(await r.text())[:120]}")
            except Exception as e:
                tried.append(f"{base}/models -> {type(e).__name__}: {str(e)[:120]}")
        body = " ｜ ".join(tried)
        base = self.base_candidates(base_url)[0]
        # Ollama native
        try:
            root = base[:-3] if base.endswith("/v1") else base
            async with self.http.get(f"{root}/api/tags", timeout=aiohttp.ClientTimeout(total=10), **self.rq()) as r:
                if r.status == 200:
                    j = await r.json()
                    return sorted(m["name"] for m in j.get("models", []))
        except Exception:
            pass
        px = ((self.cfg.get("net") or {}).get("proxy") or "").strip()
        hint = ""
        if any("TimeoutError" in t or "Cannot connect" in t or "ClientConnector" in t for t in tried):
            hint = ("。连不上对方服务器：国内直连 api.openai.com 一般是通不过的，"
                    f"去「设置」填一个代理{'（当前填的是 ' + px + '）' if px else ''}，"
                    "或者换 DeepSeek / 通义 / 本机 Ollama 这类能直连的")
        elif any("HTTP 401" in t or "HTTP 403" in t for t in tried):
            hint = "。看着像 Key 不对或没权限"
        elif any("HTTP 404" in t for t in tried):
            hint = "。404 一般是 Base URL 写法不对，正确的形如 https://api.deepseek.com/v1"
        raise RuntimeError(f"拉取模型失败{hint}\n试过：{body}")

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
            if not base.endswith("/v1") and getattr(self, "models_base", ""):
                base = self.models_base          # 用拉模型时验证过的那个 base
            async with self.http.post(f"{base}/chat/completions", headers=hdr, json=body, **self.rq(),
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
            async with self.http.get(f"{API}/channels/{cid}", headers=self.dc_headers(), **self.rq()) as r:
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
        async with self.http.post(f"{API}/channels/{channel_id}/messages", **self.rq(),
                                  headers=self.dc_headers(), json=body) as r:
            if r.status not in (200, 201):
                raise RuntimeError(f"send failed {r.status} {(await r.text())[:200]}")
            return await r.json()

    # ---------- rule engine ----------
    def match(self, rule, ev):
        """ev: normalized event dict. returns (ok, reason)"""
        if rule.get("source", "discord") != ev["source"]:
            return False, "source"
        if (ev.get("kind") or "msg") not in (rule.get("kinds") or ["msg"]):
            return False, "kind"
        if rule["ignore_bots"] and ev["is_bot"]:
            return False, "bot"
        acc = rule.get("accounts") or []
        if acc and (ev.get("account") or "") not in acc:
            return False, "account"
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
        """Store + run rules. ev is normalized。
        同一条消息可能被多个浏览器的桥各报一次，这里先挡掉重复：
        否则规则会跑两遍 —— AI 调两次、通知发两遍、命中数加两次。"""
        mid_key = ev.get("msg_id") or ""
        if mid_key:
            if mid_key in self._inflight:
                return None
            if self.db.q("SELECT 1 FROM messages WHERE msg_id=? LIMIT 1", (mid_key,)):
                return None
            self._inflight.add(mid_key)
        try:
            return await self._handle_event(ev)
        finally:
            self._inflight.discard(mid_key)

    async def _handle_event(self, ev):
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
                   is_thread,author_id,author,is_bot,content,ts,matched,ai_json,score,account,bridge,
                   kind,scanned)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ev["source"], ev["msg_id"], ev["guild_id"], ev["channel_id"], ev["channel_name"],
                 ev["parent_id"], int(ev["is_thread"]), ev["author_id"], ev["author"], int(ev["is_bot"]),
                 ev["content"], ev["ts"], ",".join(matched),
                 json.dumps(ai_json, ensure_ascii=False) if ai_json else None, score,
                 ev.get("account") or "", ev.get("bridge") or "",
                 ev.get("kind") or "msg", int(bool(ev.get("scanned")))))
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

    def store_only(self, ev):
        """历史回扫专用：只入库，不跑规则、不提醒、不调 AI。
        抓历史的目的是「事后拿这批内容去问模型」，不是把三个月前的消息重新推一遍。"""
        mid = None
        with contextlib.suppress(sqlite3.IntegrityError):
            mid = self.db.x(
                """INSERT INTO messages(source,msg_id,guild_id,channel_id,channel_name,parent_id,
                   is_thread,author_id,author,is_bot,content,ts,matched,account,bridge,kind,scanned,unread)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'',?,?,?,1,0)""",
                (ev["source"], ev["msg_id"], ev["guild_id"], ev["channel_id"], ev["channel_name"],
                 ev["parent_id"], int(ev["is_thread"]), ev["author_id"], ev["author"], int(ev["is_bot"]),
                 ev["content"], ev["ts"], ev.get("account") or "", ev.get("bridge") or "",
                 ev.get("kind") or "msg"))
        return mid

    def ctx_text(self, ev, n=12):
        rows = self.db.q("SELECT author,content FROM messages WHERE channel_id=? ORDER BY ts DESC LIMIT ?",
                         (ev["channel_id"], n))[::-1]
        return "\n".join(f"{r['author']}: {r['content']}" for r in rows)

    def prompt(self, key):
        """内置提示词：用户在界面改过就用他的，没改过用出厂值。"""
        return (self.cfg.get("prompts") or {}).get(key) or DEFAULT_PROMPTS[key]

    def pick_model(self, rule):
        prov = rule.get("provider") or self.cfg["default_model"]["provider"]
        model = rule.get("model") or self.cfg["default_model"]["model"]
        return prov, model

    async def act_tag(self, rule, ev):
        prov, model = self.pick_model(rule)
        sysmsg = rule["prompt"] or self.prompt("ai_tag")
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
        sysmsg = rule["prompt"] or self.prompt("ai_reply")
        out = await self.chat(prov, model, [
            {"role": "system", "content": sysmsg},
            {"role": "user", "content": f"最近对话:\n{self.ctx_text(ev)}\n\n请回复 {ev['author']} 的最后一条消息。"}],
            max_tokens=500, rule=rule["name"])
        await self.send_message(ev["channel_id"], out, reply_to=ev["msg_id"] if rule["reply_in_thread"] else None)
        self.log("info", f"{rule['name']}: 已在 #{ev['channel_name']} 回复")

    async def act_extract(self, rule, ev):
        prov, model = self.pick_model(rule)
        sysmsg = rule["prompt"] or self.prompt("ai_extract")
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
        sysmsg = rule["prompt"] or self.prompt("ai_summary")
        out = await self.chat(prov, model, [{"role": "system", "content": sysmsg},
                                            {"role": "user", "content": "\n".join(buf)}],
                              max_tokens=700, rule=rule["name"])
        self.sum_buf[key] = []
        self.log("info", f"摘要 #{ev['channel_name']}: {out[:200]}")
        await self.bus.push("summary", {"channel": ev["channel_name"], "text": out, "ts": now()})

    async def act_webhook(self, rule, ev):
        """这条规则专属的 webhook。留空不再悄悄什么都不做——说清楚该去哪儿填。
        （「通知与转发」里的出口是全局的，命中任何规则都会走，不需要这个动作。）"""
        url = (rule.get("webhook_url") or "").strip()
        if not url:
            self.log("warn", f"{rule['name']}: 动作是「转发 Webhook」但规则里没填地址，这条没发出去。"
                             f"要么在规则里填一个，要么把动作改成「只提醒我」——"
                             f"命中的消息本来就会走「通知与转发」里配好的出口")
            return
        async with self.http.post(url, json={"rule": rule["name"], "event": ev}, **self.rq(),
                                  timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status >= 400:            # 失败要看得见，别默默丢掉
                raise RuntimeError(f"webhook 返回 {r.status}: {(await r.text())[:120]}")

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
            # 本机提醒走合并队列（涌入时一条通知 + 一次提示音）；
            # 转发出口不合并 —— 那是给机器看的，每条都得原样送出去。
            self.queue_local(head, body[:180], sound=bool(s.get("sound")),
                             toast=bool(s.get("toast")))
        vals = self.hook_vars(ev, row, head, body, extra, url, text)
        for i, h in enumerate(s.get("hooks") or []):
            if h.get("enabled", True) and h.get("url"):
                jobs[h.get("name") or f"出口{i + 1}"] = self.push_hook(h, vals)
        if not jobs:
            return
        for name, res in zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)):
            if isinstance(res, Exception):
                self.log("error", f"{name} 发送失败: {res}")

    def hook_vars(self, ev, row, head, body, extra, url, text):
        ai = row.get("ai") or {}
        return {"text": text, "title": head, "body": (body + extra).strip(),
                "content": ev.get("content") or "", "author": ev.get("author") or "",
                "channel": ev.get("channel_name") or "", "server": ev.get("guild_name") or "",
                "url": url, "score": "" if row.get("score") is None else row["score"],
                "tags": "、".join(str(t) for t in (ai.get("tags") or [])),
                "todo": ai.get("todo") or "",
                "json": json.dumps({"event": ev, "score": row.get("score"), "ai": row.get("ai"),
                                    "matched": row.get("matched")}, ensure_ascii=False)}

    async def push_hook(self, h, vals):
        """一条出口 = 一个 HTTP 请求。URL / 头 / 体里的 {{占位符}} 换成真值。"""
        h = norm_hook(h)
        url = render_tpl(h["url"], vals, quote=False)
        heads = {}
        for line in h["headers"].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                heads[k.strip()] = render_tpl(v.strip(), vals, quote=False)
        kw = {"headers": heads} if heads else {}
        if h["method"] == "GET":
            return await self._req("GET", url, **kw)
        raw = h["body"]
        if h["content"] == "json":
            txt = render_tpl(raw, vals, quote=True)
            try:
                kw["json"] = json.loads(txt)
            except Exception as e:
                raise RuntimeError(f"请求体不是合法 JSON：{e}")
        elif h["content"] == "form":
            body = render_tpl(raw, vals, quote=False)
            kw["data"] = {k: v for k, _, v in (p.partition("=") for p in body.split("&")) if k}
        else:
            kw["data"] = render_tpl(raw, vals, quote=False).encode()
            heads.setdefault("Content-Type", "text/plain; charset=utf-8")
            kw["headers"] = heads
        return await self._req("POST", url, **kw)

    async def _req(self, method, url, **kw):
        async with self.http.request(method, url, timeout=aiohttp.ClientTimeout(total=15),
                                     **self.rq(), **kw) as r:
            t = await r.text()
            if r.status >= 300:
                raise RuntimeError(f"{r.status} {t[:150]}")
            j = None                        # 有些服务永远回 200，错误藏在 body 里
            with contextlib.suppress(Exception):
                j = json.loads(t)
            if isinstance(j, dict):
                for k in ("errcode", "code", "errCode"):
                    if str(j.get(k, 0)) not in ("0", "200", "None"):
                        raise RuntimeError(f"对方返回 {str(j)[:150]}")
            return t

    NOTIF_WINDOW = 5.0            # 这么多秒内的提醒攒成一条

    def queue_local(self, head, body, sound=True, toast=True):
        """把本机提醒放进合并队列。同一波消息只会响一声、弹一条。"""
        if not (sound or toast):
            return
        self._pend.append((head, body, toast))
        self._pend_sound = self._pend_sound or sound
        if self._pend_task is None or self._pend_task.done():
            self._pend_task = asyncio.create_task(self._flush_local())

    async def _flush_local(self):
        """等一个小窗口，把这段时间攒下的提醒合成一条发出去。"""
        try:
            await asyncio.sleep(self.NOTIF_WINDOW)
            items, sound = self._pend, self._pend_sound
            self._pend, self._pend_sound = [], False
            if not items:
                return
            if sound:
                with contextlib.suppress(Exception):
                    await self.local_sound()          # 一波只响一声
            shown = [x for x in items if x[2]]
            if not shown:
                return
            if len(shown) == 1:
                head, body, _ = shown[0]
            else:
                head = f"{len(shown)} 条新消息命中规则"
                lines = [f"{h}：{b.splitlines()[0][:40]}" if b else h for h, b, _ in shown[:4]]
                if len(shown) > 4:
                    lines.append(f"…还有 {len(shown) - 4} 条，去 dcwatch 收信箱看")
                body = "\n".join(lines)
            with contextlib.suppress(Exception):
                await self.local_toast(head, body, force=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.log("error", f"本机提醒失败: {e}")

    _last_sound = 0.0

    async def local_sound(self, name=None):
        # 最后一道闸：不管谁来调，2 秒内不重复播 —— 重叠播放就是那片电音
        if name is None:
            if now() - self._last_sound < 2:
                return
            self._last_sound = now()
        f = sound_path(name if name is not None else self.cfg["sinks"].get("sound_name", ""))
        if f is None:                                    # 兼容老配置里的绝对路径
            old = (self.cfg["sinks"].get("sound_file") or "").strip()
            f = Path(old) if old and Path(old).exists() else None

        def play():
            if IS_WIN:
                import winsound
                if f:
                    winsound.PlaySound(str(f), winsound.SND_FILENAME | winsound.SND_ASYNC)
                else:
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)   # 系统默认提示音
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
        async with self.app.http.ws_connect(url, heartbeat=None, max_msg_size=0, **self.app.rq()) as ws:
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
def safe_cfg(cfg):
    c = json.loads(json.dumps(cfg))
    if c["discord"].get("token"):
        c["discord"]["token"] = "***" + c["discord"]["token"][-4:]
    for p in c["providers"]:
        if p.get("api_key"):
            p["api_key"] = "***" + p["api_key"][-4:]
    c["prompt_defaults"] = DEFAULT_PROMPTS      # 界面上「恢复默认」要拿它当占位
    c["quick_defaults"] = DEFAULT_CONFIG["quick_actions"]     # 工作台按钮的「恢复默认」
    c["hook_vars"] = list(HOOK_VARS)
    return c


def routes(app: App):
    r = web.RouteTableDef()

    @r.get("/")
    async def index(_):
        # no-store：更新后浏览器必须重新拿 ui.html。
        # 界面里的版本号是用户核对「装的是哪一版」的凭据，被缓存成旧的就等于说谎。
        return web.FileResponse(BASE / "ui.html", headers={"Cache-Control": "no-store"})

    @r.get("/version")
    async def version_page(_):
        # 独立的自证页：不读 ui.html、不用 JS、不查数据库，所以界面坏了、
        # 浏览器缓存了旧界面、甚至配置炸了，这一页仍然说实话。
        # 旧版本没有这个路由 —— 打开是 404 本身就说明「你跑的程序是旧的」。
        eh = ext_version() or ""
        rows = [
            ("这个程序", "v" + VERSION + ("　（exe 打包版）" if FROZEN else "　（直接跑 server.py）"),
             "就是你现在连着的这个端口"),
            ("浏览器扩展至少要", "v" + EXT_MIN,
             "低于这个版本，新帖检测和抓历史不会工作"),
            ("你这份 extension 文件夹", ("v" + eh) if eh else "没找到",
             "从界面「下载浏览器扩展」拿到的就是这一份"),
            ("代码在哪", str(BASE), "启动的是这个文件夹里的 server.py"),
            ("配置在哪", str(DB_PATH),
             "全机共享，所以换文件夹跑也会带着同一份模型和规则" if FROZEN
             else "跟代码放在一起，换文件夹跑＝一份新的空配置"),
        ]
        tr = "".join(
            f'<tr><td>{k}</td><td class="v">{html_mod.escape(str(v))}</td>'
            f'<td class="n">{html_mod.escape(n)}</td></tr>' for k, v, n in rows)
        bad = eh and cmp_ver(eh, EXT_MIN) < 0
        warn = (f'<p class="w">你这份 extension 文件夹是 v{eh}，比程序要求的 v{EXT_MIN} 旧。'
                f'去 chrome://extensions 删掉旧条目，重新「加载已解压的扩展程序」指向新文件夹，'
                f'再回 Discord 页面按 F5。</p>') if bad else ""
        return web.Response(content_type="text/html", headers={"Cache-Control": "no-store"}, text=f"""<!doctype html>
<html lang="zh"><meta charset="utf-8"><title>dcwatch v{VERSION}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{{margin:0;padding:44px 24px;background:#faf9f5;color:#232323;
  font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
 .b{{max-width:760px;margin:0 auto}}
 h1{{font:600 46px/1.1 Georgia,"Songti SC",serif;margin:0 0 6px;letter-spacing:-.5px}}
 h1 span{{color:#c96442}}
 .s{{color:#6b6b6b;margin:0 0 30px}}
 table{{border-collapse:collapse;width:100%}}
 td{{padding:11px 0;border-bottom:1px solid #e8e5dd;vertical-align:top}}
 td:first-child{{color:#6b6b6b;white-space:nowrap;padding-right:20px;width:1%}}
 .v{{font:600 14px/1.5 ui-monospace,Consolas,monospace;word-break:break-all;padding-right:20px}}
 .n{{color:#8a8a8a;font-size:13px}}
 .w{{background:#fdf0e6;border-left:3px solid #c96442;padding:13px 16px;margin:26px 0 0;border-radius:0 6px 6px 0}}
 .t{{margin:30px 0 0;padding:16px 18px;background:#fff;border:1px solid #e8e5dd;border-radius:8px;color:#4a4a4a}}
 a{{color:#c96442}}
</style>
<div class="b">
<h1>dcwatch <span>v{VERSION}</span></h1>
<p class="s">这一页不用界面、不用 JS、不读配置，所以它不会被缓存骗到。</p>
<table>{tr}</table>{warn}
<div class="t"><b>这里的版本和你以为的不一样？</b><br>
说明你启动的是<b>另一个文件夹</b>里的程序 —— 新文件解压到了别处，而你双击的还是旧位置的
「启动.bat」或旧的桌面快捷方式。上面「代码在哪」那一行就是真相：
去那个文件夹，把新版文件覆盖进去，或者直接到新文件夹里双击启动.bat。<br>
exe 也一样：重新打包前的 exe 永远是旧版本。</div>
<p style="margin:26px 0 0"><a href="/">← 回到界面</a>　<a href="/diagnose.txt">导出诊断包</a></p>
</div>""")

    @r.get("/api/state")
    async def state(_):
        st = app.dc.status() if app.dc else {"source": "discord", "state": "stopped"}
        bl = app.bridge_list()
        fresh = any(x["fresh"] for x in bl)
        br = {"source": "browser", "state": "online" if fresh else "stopped",
              "last": app.last_ingest, "count": app.ingest_count,
              "bridges": bl, "live": sum(1 for x in bl if x["fresh"]),
              "accounts": sorted({x["account"] for x in bl if x.get("account") and x["fresh"]})}
        return web.json_response({
            "config": safe_cfg(app.cfg),
            "status": {"discord": st, "browser": br},
            "env": {"win": IS_WIN, "frozen": FROZEN, "data_dir": str(DATA_DIR), "port": app.port,
                    "ver": VERSION, "ext_min": EXT_MIN, "ext_have": ext_version(),
                    "autostart": autostart_state(), "pyver": sys.version.split()[0],
                    # 「我这份配置到底存在哪」必须能在界面上看见：
                    # 不然用户换个文件夹跑，看到旧的模型配置，只能怀疑是密钥泄露了
                    "code_dir": str(BASE), "db_path": str(DB_PATH),
                    "db_exists": Path(DB_PATH).exists(),
                    "shared_data": FROZEN},
            "rules": app.rules(enabled_only=False),
            "stats": {
                "msgs": app.db.q("SELECT COUNT(*) n FROM messages")[0]["n"],
                "matched": app.db.q("SELECT COUNT(*) n FROM messages WHERE matched<>''")[0]["n"],
                "ai_today": app.db.q("SELECT COUNT(*) n FROM aiusage WHERE ts>?", (now() - 86400,))[0]["n"],
                "ai_cap": app.cfg.get("ai_daily_call_cap", 500),
                "rss_mb": rss_mb(),
                "db_mb": round(sum(Path(DB_PATH + s).stat().st_size for s in ("", "-wal", "-shm")
                                   if Path(DB_PATH + s).exists()) / 1048576, 1),
                "uptime": int(now() - START_TS),
            },
        })

    @r.post("/api/config")
    async def setcfg(req):
        patch = await req.json()
        # never overwrite a secret with its masked form
        if "discord" in patch and str(patch["discord"].get("token", "")).startswith("***"):
            patch["discord"]["token"] = app.cfg["discord"]["token"]
        if "providers" in patch:
            if not isinstance(patch["providers"], list):      # 形状不对就别让它 500
                return web.json_response({"ok": False, "error": "providers 必须是数组"}, status=400)
            for p in patch["providers"]:
                if not isinstance(p, dict):
                    return web.json_response({"ok": False, "error": "providers 里每一项必须是对象"}, status=400)
                if str(p.get("api_key", "")).startswith("***"):
                    old = app.provider(p["name"]) or {}
                    p["api_key"] = old.get("api_key", "")
        if isinstance(patch.get("sinks"), dict):     # 局部更新，别把没传的字段抹掉
            merged = dict(app.cfg.get("sinks") or {})
            if isinstance(patch["sinks"].get("hooks"), list):
                patch["sinks"]["hooks"] = merge_hooks(patch["sinks"]["hooks"], merged.get("hooks"))
            merged.update(patch["sinks"])
            patch["sinks"] = merged
        if "quick_actions" in patch:
            patch["quick_actions"] = norm_quick(patch["quick_actions"])
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
            ms = await app.list_models(base, key)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=200)
        # 拉到的列表存进配置，刷新界面/重启程序后还在，不用每次重拉
        cache = dict(app.cfg.get("models_cache") or {})
        cache[b.get("provider", "")] = ms
        app.cfg["models_cache"] = cache
        app.db.set_cfg(app.cfg)
        return web.json_response({"ok": True, "models": ms})

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
              "mentions_me": bool(s.get("mentions_me")), "ts": now(), "msg_id": "0",
              # 试算必须带上触发类型，否则「只在开新帖时」的规则永远算不出命中，
              # 用户会以为规则坏了
              "kind": s.get("kind") or "msg"}
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

    @r.get("/api/prompts")
    async def prompts(_):
        """把写死在程序里的提示词原样交出来。你有权看见 AI 是被怎么指挥的。
        这两条不在界面里改（改坏了向导会直接失效），要改就改 server.py 里的常量。"""
        return web.json_response({"ok": True, "builtin": [
            {"key": "wizard", "name": "规则向导（反问你的那个）",
             "when": "「监听规则」页顶部每问你一轮、每出一次规则，都会带上这段",
             "why": "把「该问什么、什么最容易漏、模糊需求该怎么拆」这些经验写死在这里，"
                    "这样换成便宜的模型也不至于乱答",
             "where": "server.py 里的 WIZARD_SYS", "text": WIZARD_SYS},
            {"key": "compose", "name": "一句话直接出规则（老路径）",
             "when": "向导之外的快速通道，现在界面默认走向导，这条留着兼容",
             "why": "只翻译不反问，适合你已经很清楚要什么的时候",
             "where": "server.py 里的 COMPOSE_SYS", "text": COMPOSE_SYS},
            {"key": "workbench", "name": "AI 工作台（跟你自由对话的那个）",
             "when": "「AI 工作台」里你每发一句话都会带上这段，后面还会附上这台机器的实况"
                     "（收信方式、有没有浏览器在旁听、你有哪些规则、最近哪些频道来过消息、"
                     "以及你这句话里贴的 Discord 链接拆出来的 ID）",
             "why": "没有这段的时候，模型不知道自己在一个监听程序里，会回「我无法访问 Discord」"
                    "然后推荐你去写 Discord 机器人或者用 Zapier —— 而这些功能你手上这个程序本来就有。"
                    "它也会把「帮我写规则」误解成群聊管理规则。",
             "where": "server.py 里的 WORKBENCH_SYS，实况部分在 App.workbench_ctx()", "text": WORKBENCH_SYS},
        ], "editable_hint": "命中之后那几种动作（打分、摘要、抽取、回复）的提示词是可以改的，"
                            "在「模型接入」页最下面，工作台的「额外要求」也在那儿（prompts.ask）。"
                            "这里这三条是骨架，写死在程序里：改坏了向导和工作台会直接失效。"})

    @r.post("/api/rules/wizard")
    async def wizard(req):
        """引导式建规则：模型先反问、补齐用户想不到的地方，确认没疏漏了才出规则。
        无状态——整段对话由界面带上来。"""
        b = await req.json()
        turns = [m for m in (b.get("messages") or [])
                 if isinstance(m, dict) and m.get("role") in ("user", "assistant") and str(m.get("content") or "").strip()]
        if not turns:
            return web.json_response({"ok": False, "error": "先说一句你想监听什么"})
        turns = turns[-24:]                                  # 别把上下文撑爆
        prov = b.get("provider") or app.cfg["default_model"]["provider"]
        model = b.get("model") or app.cfg["default_model"]["model"]
        if not model:
            return web.json_response({"ok": False, "error": "还没选模型：先去「模型接入」填 Key 并选一个模型"})
        said = "\n".join(str(m["content"]) for m in turns if m["role"] == "user")
        ctx, known_ids = app.rule_ctx(said)
        rounds = sum(1 for m in turns if m["role"] == "assistant")
        push = ("\n\n【注意】已经问过 %d 轮了，这一轮**必须**输出 stage=done，缺的信息就用你的推荐值补上，"
                "把不确定的地方写进 notes。" % rounds) if rounds >= 3 else ""
        msgs = ([{"role": "system", "content": WIZARD_SYS},
                 {"role": "user", "content": f"现场情况：\n{ctx}{push}\n\n下面是我们的对话。"}]
                + turns)
        try:
            out = await app.chat(prov, model, msgs, json_mode=True, max_tokens=1600, rule="wizard")
        except Exception as e:
            return web.json_response({"ok": False, "error": f"模型调用失败: {e}"})
        try:
            raw = loose_json(out)
        except Exception:
            # 弱模型不给 JSON 是常态。把它说的话当追问端上去，别让向导死在这。
            txt = re.sub(r"\s+", " ", (out or "")).strip()[:400] or "（模型没说话）"
            return web.json_response({"ok": True, "stage": "ask", "understood": "",
                                      "questions": [{"q": txt, "why": "这个模型没按格式回答，你可以直接回它，"
                                                                     "或者换个强一点的模型再来",
                                                     "options": [], "suggest": "", "skippable": True}],
                                      "assumed": [], "loose": True})
        stage = str(raw.get("stage") or ("done" if raw.get("rule") else "ask")).lower()
        if stage != "done":
            qs = []
            for q in (raw.get("questions") or [])[:3]:
                if isinstance(q, str):
                    q = {"q": q}
                if not isinstance(q, dict) or not str(q.get("q") or "").strip():
                    continue
                qs.append({"key": str(q.get("key") or "")[:24],
                           "q": str(q["q"])[:300], "why": str(q.get("why") or "")[:300],
                           "options": [str(o)[:60] for o in (q.get("options") or [])][:6],
                           "suggest": str(q.get("suggest") or q.get("default") or "")[:60],
                           "skippable": bool(q.get("skippable"))})
            if not qs:      # 说要问却没问出东西 → 别卡住，让用户补一句
                qs = [{"q": "还想补充点什么吗？没有就说「就这样，出规则」", "why": "", "options": [],
                       "suggest": "就这样，出规则", "skippable": True}]
            return web.json_response({"ok": True, "stage": "ask",
                                      "understood": str(raw.get("understood") or "")[:400],
                                      "questions": qs,
                                      "assumed": [str(x)[:200] for x in (raw.get("assumed") or [])][:5]})
        rule, notes = sanitize_draft(raw.get("rule") or {}, list(raw.get("notes") or []), known_ids, model)
        rule["provider"], rule["model"] = b.get("provider") or "", b.get("model") or ""
        if not rule["name"]:
            rule["name"] = (said.strip().splitlines() or ["新规则"])[0][:20]
        notes.append("生成不等于生效：保存后先用下面的「试算」拿真消息验一遍，再观察一会儿。")
        return web.json_response({"ok": True, "stage": "done", "rule": rule,
                                  "catches": [str(x)[:200] for x in (raw.get("catches") or [])][:6],
                                  "misses": [str(x)[:200] for x in (raw.get("misses") or [])][:6],
                                  "verify": str(raw.get("verify") or "")[:400],
                                  "notes": notes[:8]})

    @r.post("/api/rules/compose")
    async def compose(req):
        """一句中文 → 规则草稿。生成完只回给界面，用户确认保存才生效。"""
        b = await req.json()
        text = (b.get("text") or "").strip()
        if not text:
            return web.json_response({"ok": False, "error": "先说一句你想怎么监听"})
        prov = b.get("provider") or app.cfg["default_model"]["provider"]
        model = b.get("model") or app.cfg["default_model"]["model"]
        ctx, known_ids = app.rule_ctx(text)
        try:
            out = await app.chat(prov, model, [{"role": "system", "content": COMPOSE_SYS},
                                               {"role": "user", "content": f"{ctx}\n\n需求：{text}"}],
                                 json_mode=True, max_tokens=900, rule="compose")
            raw = json.loads(re.search(r"\{.*\}", out, re.S).group(0))
        except Exception as e:
            return web.json_response({"ok": False, "error": f"生成失败: {e}"})

        draft = raw.get("rule") or raw
        rule, notes = sanitize_draft(draft, list(raw.get("notes") or []), known_ids, model)
        rule["provider"], rule["model"] = b.get("provider") or "", b.get("model") or ""
        return web.json_response({"ok": True, "rule": rule, "notes": notes[:6]})

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
        # 身份和边界写死在 WORKBENCH_SYS 里，不让改 —— 少了它模型就会说「我无法访问 Discord」
        # 然后推荐用户去装 Zapier。用户在「模型接入」页能改的那句只作为额外要求附在后面。
        text = b.get("prompt", "")
        extra = (b.get("system") or app.prompt("ask") or "").strip()
        sysmsg = WORKBENCH_SYS + "\n\n## 这台机器现在的实况\n" + app.workbench_ctx(text)
        if extra and extra != DEFAULT_PROMPTS["ask"]:
            sysmsg += "\n\n## 用户自己追加的要求（优先照做，但不能违反上面的边界）\n" + extra
        msgs = [{"role": "system", "content": sysmsg}]
        msgs.append({"role": "user", "content": (f"消息上下文:\n{ctx}\n\n" if ctx else "") + text})
        try:
            return web.json_response({"ok": True, "text": await app.chat(prov, model, msgs, max_tokens=1200,
                                                                        rule="manual")})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    BATCH_SYS = """你在批量翻一批 Discord 消息，把用户要的东西挑出来。

只输出 JSON：{"rows":[{"msg_id":"...","value":"挑出来的东西","note":"一句话说明/上下文"}]}
- 一条消息里有多个就拆成多行；没有就**不要**给这条消息编一行出来。
- value 必须是消息里**原样出现**的内容，一个字都不许改、不许补全、不许猜。
- 整批都没有就给 {"rows":[]}。宁可漏，不许编 —— 编出来的东西会让用户白跑一趟。"""

    @r.post("/api/batch")
    async def batch(req):
        """批量提取：把一批消息分组喂给模型，挑出用户要的东西，汇总成表。
        规则引擎是「新消息来了怎么办」，这里是「已经攒下的这堆里有什么」。"""
        b = await req.json()
        want = (b.get("want") or "").strip()
        if not want:
            return web.json_response({"ok": False, "error": "没说要提取什么"}, status=400)
        where, args = ["content<>''"], []
        if b.get("channel_id"):
            where.append("(channel_id=? OR parent_id=?)")
            args += [str(b["channel_id"])] * 2
        if b.get("since"):
            where.append("ts>=?")
            args.append(float(b["since"]))
        if b.get("author_contains"):
            where.append("lower(author) LIKE ?")
            args.append("%" + str(b["author_contains"]).lower() + "%")
        if b.get("only_matched"):
            where.append("matched<>'' AND matched IS NOT NULL AND matched<>'0'")
        limit = max(1, min(int(b.get("limit") or 300), 2000))
        rows = app.db.q("SELECT id,msg_id,author,content,ts,channel_name FROM messages WHERE "
                        + " AND ".join(where) + " ORDER BY ts DESC LIMIT ?", args + [limit])
        rows.reverse()
        if not rows:
            return web.json_response({"ok": True, "rows": [], "scanned": 0, "calls": 0,
                                      "note": "这个范围里一条消息都没有。先去 Discord 页面用「抓历史」把内容抓进来。"})
        prov = b.get("provider") or app.cfg["default_model"]["provider"]
        model = b.get("model") or app.cfg["default_model"]["model"]
        chunk = max(1, min(int(b.get("chunk") or 20), 60))
        groups = [rows[i:i + chunk] for i in range(0, len(rows), chunk)]
        out, errs, byid = [], [], {str(r["msg_id"]): r for r in rows}
        for gi, g in enumerate(groups):
            body = "\n\n".join(f"[{r['msg_id']}] {r['author']}（#{r['channel_name']}）：{r['content']}"
                               for r in g)
            try:
                txt = await app.chat(prov, model, [
                    {"role": "system", "content": BATCH_SYS},
                    {"role": "user", "content": f"要挑出来的是：{want}\n\n消息：\n{body}"}],
                    json_mode=True, max_tokens=1500, rule="batch")
                got = loose_json(txt).get("rows") or []
            except Exception as e:
                errs.append(f"第 {gi + 1} 批失败：{e}")
                continue
            for x in got if isinstance(got, list) else []:
                if not isinstance(x, dict):
                    continue
                mid = str(x.get("msg_id") or "")
                src = byid.get(mid)
                val = str(x.get("value") or "").strip()
                if not val:
                    continue
                # 模型偶尔会把提取物改写或者干脆编一个。原文里找不到的一律标出来，不装作没事
                verified = bool(src) and val in (src["content"] or "")
                out.append({"value": val, "note": str(x.get("note") or "")[:200],
                            "msg_id": mid, "author": src["author"] if src else "",
                            "channel": src["channel_name"] if src else "",
                            "ts": src["ts"] if src else None,
                            "content": (src["content"] if src else "")[:300],
                            "verified": verified})
        app.log("info", f"批量提取「{want[:20]}」：翻了 {len(rows)} 条，{len(groups)} 次调用，挑出 {len(out)} 条")
        return web.json_response({"ok": True, "rows": out, "scanned": len(rows), "calls": len(groups),
                                  "errors": errs,
                                  "unverified": sum(1 for x in out if not x["verified"])})

    @r.post("/api/reply")
    async def reply(req):
        b = await req.json()
        try:
            await app.send_message(b["channel_id"], b["content"], b.get("reply_to"))
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "content-type",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            # Chrome 拦「公网页面 → 本机地址」的请求，预检得明确放行（少了这个扩展就悄悄发不出来）
            "Access-Control-Allow-Private-Network": "true",
            "Access-Control-Max-Age": "600"}      # 别每批消息都预检一次

    @r.options("/api/ingest")
    async def ingest_pre(_):
        return web.Response(headers=CORS)

    @r.post("/api/ingest")
    async def ingest(req):
        """外部来源投递消息（Chrome 扩展旁听网页版 Discord / 任何脚本）。
        走完全相同的规则引擎，所以规则、AI 动作、通知都不用改。"""
        b = await req.json()
        if b.get("ping"):
            br = app.touch_bridge(b)
            return web.json_response({"ok": True, "pong": True, "bridge": br["id"],
                                      "server_ver": VERSION}, headers=CORS)
        if not app.cfg["sources"].get("browser", True):
            app.touch_bridge(b, err="旁听开关是关的，消息被丢弃")
            return web.json_response({"ok": False, "error": "浏览器旁听已关闭"}, headers=CORS)
        items = b.get("messages") or [b]
        # history=1：这一批是「抓历史」抓来的，只入库不提醒（否则一次回扫能弹几百条通知）
        history = bool(b.get("history"))
        n = 0
        for m in items:
            if not m.get("msg_id") or not (m.get("content") or "").strip():
                continue
            cid = str(m.get("channel_id") or "")
            ev = {"source": "discord", "via": "browser",
                  "kind": ("thread" if m.get("kind") == "thread" else "msg"),
                  "scanned": history,
                  "msg_id": "b" + str(m["msg_id"]), "guild_id": str(m.get("guild_id") or ""),
                  "channel_id": cid, "channel_name": m.get("channel_name") or cid,
                  "parent_id": str(m.get("parent_id") or ""), "is_thread": bool(m.get("is_thread")),
                  "author_id": str(m.get("author_id") or ""), "author": m.get("author") or "?",
                  "is_bot": bool(m.get("is_bot")), "content": m["content"],
                  # 抓历史时用消息自己的时间（扩展从 snowflake 解出来），别让三个月前的消息
                  # 全挤在「刚刚」——收信箱和批量提取都按时间排
                  "ts": float(m["ts"]) if str(m.get("ts") or "").replace(".", "", 1).isdigit() else now(),
                  "is_dm": bool(m.get("is_dm")), "mentions_me": bool(m.get("mentions_me")),
                  "url": m.get("url") or "",
                  # 哪个号收到的：单条上带的优先，否则用这一批的（多号监控靠它区分）
                  "account": str(m.get("account") or b.get("account") or ""),
                  "bridge": str(b.get("bridge") or "")}
            if history:
                app.store_only(ev)
            else:
                await app.handle_event(ev)
            n += 1
        app.ingest_count += n
        br = app.touch_bridge(b, n=n)
        return web.json_response({"ok": True, "accepted": n, "bridge": br["id"],
                                  "server_ver": VERSION}, headers=CORS)

    @r.post("/api/sinks/test")
    async def sinktest(req):
        """按一下就知道通不通。which = toast / sound / hook:<id> / all。
        转发出口测通了才会记 verified，界面左下角只列通过的。"""
        b = await req.json()
        which, s = b.get("which", "all"), app.cfg["sinks"]
        head, body = "dcwatch 测试通知", "看到这条，说明这个出口通了。"
        text = f"{head}\n{body}"
        vals = app.hook_vars({"content": body, "author": "dcwatch", "channel_name": "测试",
                              "guild_name": "测试"}, {"score": 99, "ai": {"tags": ["测试"]}},
                             head, body, "", "", text)
        jobs, ids = {}, {}
        if which in ("all", "sound"):
            jobs["提示音"] = app.local_sound()
        if which in ("all", "toast"):
            jobs["系统通知"] = app.local_toast(head, body, force=True)
        for h in s.get("hooks") or []:
            if not h.get("url") or which not in ("all", f"hook:{h['id']}"):
                continue
            nm = h.get("name") or h["id"]
            jobs[nm], ids[nm] = app.push_hook(h, vals), h["id"]
        if not jobs:
            return web.json_response({"ok": False, "error": "这条出口还没填地址"})
        out, changed = {}, False
        for name, res in zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)):
            ok = not isinstance(res, Exception)
            out[name] = "ok" if ok else str(res)[:200]
            if name in ids:
                for h in s["hooks"]:
                    if h["id"] == ids[name] and h.get("verified") != ok:
                        h["verified"], changed = ok, True
        if changed:
            app.save_cfg()
            await app.bus.push("sinks", s)
        if app.in_quiet_hours() and which in ("all", "sound", "toast"):
            out["注意"] = "当前在免打扰时段，实际收信时本机不会响"
        return web.json_response({"ok": True, "results": out, "sinks": s})

    @r.get("/api/autostart")
    async def autoget(_):
        return web.json_response({"ok": True, **autostart_state()})

    @r.post("/api/autostart")
    async def autoset(req):
        b = await req.json()
        try:
            st = autostart_set(bool(b.get("on")), app.port)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)[:200]})
        app.log("info", f"开机自启动：{'已开启' if st['on'] else '已关闭'}")
        return web.json_response({"ok": True, **st})

    @r.get("/extension.zip")
    async def extzip(_):
        """浏览器扩展打成 zip 给你下载：解压 → chrome://extensions → 加载已解压的扩展程序。"""
        # 源码版在 BASE 下；exe 版优先用打进包里的，没有就找 exe 旁边的（build.bat 会拷一份过去）
        src = ext_dir()
        if src is None:
            raise web.HTTPNotFound(text="找不到 extension 目录（应该在 server.py 旁边）")
        v = ext_version() or "unknown"
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(src.rglob("*")):
                if p.is_file():
                    z.write(p, f"dcwatch-extension/{p.relative_to(src)}")
        return web.Response(body=buf.getvalue(), content_type="application/zip",
                            headers={"Content-Disposition":
                                     f'attachment; filename="dcwatch-extension-v{v}.zip"',
                                     "X-Dcwatch-Ext-Version": v})

    @r.post("/api/sinks/toggle")
    async def sinktoggle(req):
        """左下角那排开关：单独启停某条转发出口。"""
        b = await req.json()
        hid, on = str(b.get("id") or ""), b.get("on")
        for h in app.cfg["sinks"].get("hooks") or []:
            if h["id"] == hid:
                h["enabled"] = (not h.get("enabled", True)) if on is None else bool(on)
                app.save_cfg()
                return web.json_response({"ok": True, "hook": h})
        return web.json_response({"ok": False, "error": "没有这条出口"})

    # ---------- 提示音：内置几个 + 自己导入 ----------
    @r.get("/api/sounds")
    async def sounds_list(_):
        return web.json_response({"sounds": list_sounds(),
                                  "current": app.cfg["sinks"].get("sound_name", ""),
                                  "max_seconds": SOUND_MAX_SECONDS,
                                  "max_bytes": SOUND_MAX_BYTES})

    @r.get("/api/sounds/file")
    async def sounds_file(req):
        """给网页里试听用。"""
        f = sound_path(req.query.get("id", ""))
        if not f:
            raise web.HTTPNotFound(text="没有这个提示音")
        return web.Response(body=f.read_bytes(), content_type="audio/wav")

    @r.post("/api/sounds/play")
    async def sounds_play(req):
        """在运行 dcwatch 的这台电脑上真放一遍（和收到消息时一模一样）。"""
        b = await req.json()
        await app.local_sound(b.get("id", ""))
        return web.json_response({"ok": True, "quiet": app.in_quiet_hours()})

    @r.post("/api/sounds/import")
    async def sounds_import(req):
        """网页已经把音频解码、截好片段、转成 16bit wav 了，这里只收字节。"""
        name = re.sub(r"[^\w\u4e00-\u9fff.-]", "_", req.query.get("name", "我的提示音"))[:40] or "sound"
        raw = await req.read()
        if len(raw) > SOUND_MAX_BYTES:
            return web.json_response({"ok": False, "error": f"文件太大（{len(raw)//1024}KB），上限 {SOUND_MAX_BYTES//1024}KB"})
        try:
            sec = wav_seconds(raw)
        except Exception as e:
            return web.json_response({"ok": False, "error": f"这不是能用的 wav：{e}"})
        if sec > SOUND_MAX_SECONDS + 0.5:
            return web.json_response({"ok": False, "error": f"太长了（{sec:.1f} 秒），上限 {SOUND_MAX_SECONDS} 秒"})
        USER_SOUNDS.mkdir(parents=True, exist_ok=True)
        f = USER_SOUNDS / (name if name.endswith(".wav") else name + ".wav")
        i = 2
        while f.exists():
            f = USER_SOUNDS / f"{name.removesuffix('.wav')}-{i}.wav"
            i += 1
        f.write_bytes(raw)
        app.log("info", f"[提示音] 导入 {f.name}（{sec:.1f}s, {len(raw)//1024}KB）")
        return web.json_response({"ok": True, "id": f"custom:{f.name}", "seconds": round(sec, 2),
                                  "sounds": list_sounds()})

    @r.post("/api/sounds/delete")
    async def sounds_delete(req):
        b = await req.json()
        sid = b.get("id", "")
        if not sid.startswith("custom:"):
            return web.json_response({"ok": False, "error": "内置提示音删不掉"})
        f = USER_SOUNDS / Path(sid.split(":", 1)[1]).name
        if f.exists():
            f.unlink()
        if app.cfg["sinks"].get("sound_name") == sid:      # 删掉正在用的就退回内置
            app.cfg["sinks"]["sound_name"] = "builtin:ding"
            app.db.set_cfg(app.cfg)
        return web.json_response({"ok": True, "sounds": list_sounds(),
                                  "current": app.cfg["sinks"].get("sound_name", "")})

    @r.get("/diagnose.txt")
    async def diagnose(req):
        """一键导出诊断包：程序状态 + 扩展状态 + 规则 + 出口 + 日志，全在一个文本文件里。
        出问题时把这个文件发出去就够了，不用别人猜。
        默认带上消息正文的前 40 字（排查关键词命中要用）；不想带就加 ?text=0。"""
        keep_text = req.query.get("text", "1") != "0"
        L, P = [], lambda *a: L.append(" ".join(str(x) for x in a))
        mask = lambda u: re.sub(r"(https?://[^/]+).*", r"\1/…", u or "") or "（空）"
        # 列表别按 Python 语法打印，人要看的
        def V(v):
            if isinstance(v, (list, tuple)):
                return " / ".join(str(x) for x in v) if v else "-"
            if v is True:
                return "是"
            if v is False:
                return "否"
            return "-" if v in ("", None) else str(v)

        P("dcwatch 诊断包")
        P("生成时间", time.strftime("%Y-%m-%d %H:%M:%S"), "（本机时间）")
        P("=" * 62)

        P("\n[1] 程序")
        P("  版本            ", VERSION, " / 要求扩展至少", EXT_MIN)
        P("  磁盘上的扩展版本", ext_version() or "（没找到 extension 文件夹）")
        P("  打包运行(exe)   ", getattr(sys, "frozen", False))
        P("  Python          ", sys.version.split()[0], "|", sys.platform)
        P("  代码目录        ", str(BASE))
        P("  数据目录        ", str(DATA_DIR),
          "（exe 模式：全机共享，换文件夹跑也是同一份配置）" if FROZEN else "（跟代码同目录）")
        P("  数据库          ", DB_PATH, "(", os.path.getsize(DB_PATH) // 1024 if os.path.exists(DB_PATH) else 0, "KB )")
        P("  已跑            ", round((now() - app.started) / 60, 1), "分钟")
        n_msg = (app.db.q("SELECT COUNT(*) c FROM messages") or [{"c": 0}])[0]["c"]
        n_hit = (app.db.q("SELECT COUNT(*) c FROM messages WHERE matched=1") or [{"c": 0}])[0]["c"]
        P("  库里消息        ", n_msg, "条，其中命中过规则", n_hit, "条")

        P("\n[2] 出网设置（模型拉不到、转发失败先看这里）")
        P("  代理            ", app.cfg.get("net", {}).get("proxy") or "（没设，走系统环境变量）")
        P("  默认模型        ", app.cfg["default_model"].get("provider"), "/", app.cfg["default_model"].get("model"))
        for pr in app.cfg.get("providers", []):
            P("  端点            ", pr.get("name"), "→", pr.get("base_url"),
              "| Key", "已填" if pr.get("api_key") else "空")
        used = app.db.q("SELECT COUNT(*) n FROM aiusage WHERE ts>?", (now() - 86400,))[0]["n"]
        P("  近 24 小时调模型", used, "次 / 上限", app.cfg.get("ai_daily_call_cap", 500))
        errs = app.db.q("SELECT text FROM logs WHERE level='error' ORDER BY id DESC LIMIT 5")
        P("  最近 5 条报错    ", "（没有）" if not errs else "")
        for e in errs:
            P("    -", e["text"][:160])

        P("\n[3] 收信来源")
        d = app.cfg["discord"]
        P("  模式            ", d.get("mode"), "| 开关", app.cfg["sources"].get("discord"),
          "| Token", "已填" if d.get("token") else "空")
        P("  Discord 直连状态", (app.dc.state if app.dc else "-"))
        brs = app.bridge_list()
        P("  浏览器旁听      ", len(brs), "个桥，", sum(1 for b in brs if b["fresh"]), "个还活着")
        if not brs:
            P("    （一个都没有 = 扩展没装上，或者装完没在 Discord 页面按 F5）")
        for b in brs:
            P("    -", b.get("account") or "未知账号", "|", b.get("browser") or "?",
              "| 扩展 v" + (b.get("ver") or "?"), "|", "活着" if b["fresh"] else "失联",
              "（", b["ago"], "秒前）| 报过", b.get("count", 0), "条",
              ("| 错误: " + b["err"]) if b.get("err") else "")
            P("      在盯     ", b.get("where") or "（不知道）")
            st = b.get("stats") or {}
            if st:
                sk = st.get("skip") or {}
                P("      扩展侧   ", "解析", st.get("parsed", 0), "条 → 上报", st.get("sent", 0), "条",
                  "| 页面上有", st.get("lis", "?"), "条消息 | 路径", st.get("url", "?"))
                P("      跳过原因 ", "历史", sk.get("history", 0), "· 整批渲染", sk.get("render", 0),
                  "· 重复", sk.get("dup", 0), "· 无正文", sk.get("notext", 0), "· 静默期", sk.get("quiet", 0))
                for rc in (st.get("recent") or [])[:6]:
                    P("        最近   ", (rc.get("what") or "")[:40], "→", rc.get("why") or "")
            else:
                P("      扩展侧   （这个桥还没送来诊断数据，扩展可能是旧版）")

        P("\n[4] 规则（自上而下全部匹配）")
        rules = app.rules(False)
        if not rules:
            P("  一条都没有")
        for j in rules:
            P("  -", ("[开]" if j.get("enabled") else "[停]"), j.get("name") or "(没名字)",
              "| 命中", j.get("hits", 0), "次", "| 动作", j.get("action"))
            P("      听哪里   ", "服务器", V(j.get("guild_ids")), "频道", V(j.get("channel_ids")),
              "子区", V(j.get("thread_ids")),
              "| 含子区" if j.get("include_threads_of_channels") else "", "| 私信" if j.get("dm") else "")
            P("      听谁     ", "用户", V(j.get("author_ids")), "| 昵称含", V(j.get("author_name_contains")),
              "| 忽略机器人", V(j.get("ignore_bots")), "| 只在@我", V(j.get("mention_only")))
            P("      听内容   ", "任一", V(j.get("keywords_any")), "| 全含", V(j.get("keywords_all")),
              "| 正则", V(j.get("regex")), "| 最短", j.get("min_len"))
            P("      阈值/节流", "分数≥", j.get("notify_min_score"), "| 冷却", j.get("cooldown_sec"), "秒",
              "| 每小时≤", j.get("max_per_hour"), "| 攒", j.get("summary_every"))

        P("\n[5] 通知与转发")
        sk = app.cfg.get("sinks", {})
        P("  本机提醒        ", "弹窗", sk.get("toast"), "| 声音", sk.get("sound"), sk.get("sound_name") or "",
          "| 免打扰", sk.get("quiet_from"), "-", sk.get("quiet_to"), "| 分数门槛", sk.get("min_score"))
        for h in sk.get("hooks", []):
            P("  出口            ", ("[开]" if h.get("enabled") else "[停]"), h.get("name") or "(没名字)",
              "→", mask(h.get("url")), "| 测过" if h.get("verified") else "| 没测过")
        if not sk.get("hooks"):
            P("  出口             （一个都没配）")

        P("\n[6] 最近 200 条运行日志（新的在上）")
        lgs = app.db.q("SELECT * FROM logs ORDER BY id DESC LIMIT 200")
        if not lgs:
            P("  （空的。刚启动就是这样；如果跑了一阵还是空的，说明什么动作都没触发过）")
        for lg in lgs:
            P("  ", time.strftime("%m-%d %H:%M:%S", time.localtime(lg["ts"])),
              (lg["level"] or "").upper().ljust(5), lg["text"])

        P("\n[7] 最近 30 条消息" + ("（含正文前 40 字）" if keep_text else "（不含正文）"))
        for m in app.db.q("SELECT * FROM messages ORDER BY id DESC LIMIT 30"):
            P("  ", time.strftime("%m-%d %H:%M:%S", time.localtime(m["ts"])),
              "#" + (m["channel_name"] or "?"), "|", m["author"] or "?",
              "| 命中" if str(m["matched"] or "") not in ("", "0", "None") else "|     ",
              ("| 分 " + str(m["score"])) if m["score"] is not None else "",
              "| 桥 " + (m["bridge"] or "-"), "| 号 " + (m["account"] or "-"))
            if keep_text:
                P("      ", re.sub(r"\s+", " ", m["content"] or "")[:40])

        P("\n" + "=" * 62)
        P("看不出问题就把整个文件发给对方。里面没有 Token、没有 API Key、URL 只留了域名。")
        body = "\n".join(L)
        fn = "dcwatch-诊断-" + time.strftime("%m%d-%H%M") + ".txt"
        return web.Response(body=body.encode("utf-8"), headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": f'attachment; filename="dcwatch-diagnose.txt"; '
                                   f"filename*=UTF-8''{urllib.parse.quote(fn)}"})

    @r.get("/api/logs")
    async def logs(_):
        return web.json_response({"logs": app.db.q("SELECT * FROM logs ORDER BY id DESC LIMIT 200")})

    @r.post("/api/quit")
    async def quit_(_):
        """让「停止.bat」和界面上的退出按钮能干净地关掉程序，不用去任务管理器找进程。"""
        app.log("info", "收到退出请求，正在关闭")
        async def bye():
            await asyncio.sleep(0.3)          # 先把这个响应发出去，再走
            os._exit(0)
        asyncio.create_task(bye())
        return web.json_response({"ok": True})

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
        # 半小时没心跳的桥就不再列出来（浏览器关了/扩展卸了）
        for bid in [k for k, v in app.bridges.items() if now() - v.get("last", 0) > 1800]:
            app.bridges.pop(bid, None)
        await asyncio.sleep(3600)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--open", action="store_true", help="启动后自动打开界面（exe 默认就开）")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--minimized", action="store_true",
                    help="后台安静启动：藏起窗口、不开浏览器（开机自启动用的就是这个）")
    a = ap.parse_args()
    if a.minimized:
        a.no_open = True
        hide_console()

    app = App()
    app.port = a.port
    web_app = web.Application(client_max_size=2 * 1024 * 1024)
    web_app.add_routes(routes(app))

    async def on_start(_):
        app.http = aiohttp.ClientSession(trust_env=True)   # 认 HTTP(S)_PROXY / NO_PROXY
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
        # 双击了第二次：不再起第二个进程（那样只会两份都在收信、通知弹两遍），
        # 直接把已经在跑的那个界面打开 —— 用户想要的本来就是界面。
        u = f"http://127.0.0.1:{a.port}"
        print(f"dcwatch 已经在跑了（端口 {a.port} 被占：{e}）。没有再开第二个。", flush=True)
        print(f"界面就是这个：{u}   想换端口用 --port 8778", flush=True)
        await runner.cleanup()
        if not a.no_open:
            with contextlib.suppress(Exception):
                webbrowser.open(u)
        if FROZEN:
            with contextlib.suppress(Exception):
                input("按回车退出…")
        return
    url = f"http://127.0.0.1:{a.port}"
    # 版本必须是黑窗口的第一行。用户更新完最想确认的就是「我跑的是哪一版」，
    # 而黑窗口是他一定会看到的地方（界面可能被浏览器缓存成旧的，这里不会骗人）。
    print(f"dcwatch v{VERSION}{'（exe）' if FROZEN else ''}   扩展应为 v{EXT_MIN} 以上"
          f"{'，你这份 extension 文件夹是 v' + (ext_version() or '?') if ext_version() else ''}", flush=True)
    print(f"代码: {BASE}", flush=True)
    print(f"配置: {DB_PATH}", flush=True)
    print(f"dcwatch -> {url}     版本核对页：{url}/version", flush=True)
    if (a.open or FROZEN) and not a.no_open:
        with contextlib.suppress(Exception):
            webbrowser.open(url)
    if FROZEN:
        print("这个黑窗口关掉就等于停止监听；要一直收信就让它留着（可以最小化）。", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
