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

VERSION = "1.11.6"                              # 服务端版本，界面和扩展都能看到
EXT_MIN = "1.11.6"                               # 低于这个版本的扩展要提示用户更新
# ---- E2：宽容读响应体 ----------------------------------------------------
# 有些中转站（youzi.today 这类）返回的 Content-Length 跟实际字节数不符，或者压缩
# 编码不规范。aiohttp 默认很严格，一发现对不上就抛 ClientPayloadError，并且把
# 「已经收到的那部分数据」一起扔掉 —— 于是别的软件能拉到模型，只有我们拉不到。
# 这里逐块收，半路断了就用手上已有的部分，交给 loads_loose 去抢救。
async def read_tolerant(r):
    """返回 (原始字节, 是否被截断)。任何读取异常都不往上抛。"""
    buf = bytearray()
    cut = False
    try:
        async for chunk in r.content.iter_any():
            buf += chunk
            if len(buf) > 8 * 1024 * 1024:      # 模型列表不可能这么大，防跑飞
                break
    except Exception:
        cut = True
    return bytes(buf), cut


def loads_loose(raw):
    """宽容解析 JSON：去 BOM、掐掉前后垃圾、被截断也尽量 raw_decode 抢救出来。"""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    t = raw.strip().lstrip("\ufeff")
    if not t:
        raise ValueError("对方返回了空响应")
    try:
        return json.loads(t)
    except Exception:
        pass
    i = min([x for x in (t.find("{"), t.find("[")) if x >= 0], default=-1)
    if i > 0:
        t = t[i:]
    try:
        return json.loads(t)
    except Exception:
        pass
    try:                                        # 尾巴被截断：抢救最长的合法前缀
        return json.JSONDecoder().raw_decode(t)[0]
    except Exception:
        raise ValueError("对方返回的不是完整 JSON（前 80 字：%s）" % t[:80].replace("\n", " "))


def ids_from_text(raw):
    """最后一招：连 JSON 都拼不回来时，直接从文本里把 "id":"xxx" 捞出来。
    截断的模型列表用这个几乎总能救回来 —— 只是可能少最后一两个。"""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    out, seen = [], set()
    for m in re.finditer(r'"id"\s*:\s*"([^"\\]{1,120})"', raw):
        v = m.group(1)
        if v not in seen:
            seen.add(v); out.append(v)
    return out


# 扩展上报的跳过原因，给人看的说法（诊断结论里要拼成一句话）
NICE_SKIP = {"history": "历史消息（时间戳太旧）", "render": "整批渲染（切频道/往上滚）",
             "dup": "重复", "notext": "空消息（连图片贴纸都没有）", "quiet": "刚打开页面的头几秒"}
# match() 返回的卡点代码 → 人话。诊断包的「拿真实消息试算」段和界面试算都用它。
# 光给一个 "kind" / "channel/thread" 这种代码，用户看了也不知道该改哪个输入框。
NICE_WHY = {
    "source": "消息来源对不上（规则听的不是 Discord）",
    "kind": "触发类型对不上：这条规则只在「开新帖」时触发，普通聊天消息永远不算",
    "bot": "发的人是机器人，而规则勾了「忽略机器人」",
    "account": "不是规则指定的那个 Discord 账号",
    "dm-off": "这是私信，规则没勾「包含私信 DM」",
    "dm-only": "规则只听私信，这条不是私信",
    "guild": "服务器 ID 对不上（「听哪里」的服务器那一栏）",
    "channel/thread": "频道 / 子区 ID 对不上（「听哪里」的频道、子区两栏）",
    "author": "发的人不在「听谁」的用户 ID 名单里",
    "author-name": "昵称里没有「昵称含」要求的字",
    "mention": "规则要求「只在 @我 时」，这条没有 @ 你",
    "len": "正文字数没到「最短」要求",
    "kw-any": "正文里一个「任一关键词」都没出现",
    "kw-all": "「全含关键词」没有全部出现",
    "regex": "正则没匹配上",
    "命中": "会命中",
}
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
    # 自动点开新帖（C2）。**默认全关**：论坛一天开 50 个帖，全自动点开就是 50 个标签页。
    #   auto_open          总开关。关着＝完全是老行为，扩展一个标签页都不会开
    #   max_tabs           同时最多让程序开着几个（超了就不再开新的）
    #   per_hour           每小时最多开几个（防论坛刷帖把浏览器打爆）
    #   only_rule_channels 只开「有启用规则在盯的父频道」下面的新帖
    #   close_idle_min     帖子闲置这么久就让扩展关掉那个标签页；0 = 不自动关
    "browser": {"auto_open": False, "max_tabs": 6, "per_hour": 8,
                "only_rule_channels": True, "close_idle_min": 30},
    # AI 相关开关。
    #   stream = 边想边显示（不然模型思考十几秒，界面上只有一个「…」，看着像卡死）
    #   tools  = 允许模型自己动手改规则（关掉就退回只会讲步骤的老样子）
    #   post   = 提示词后处理模式，见 AI_POST。弱模型不按格式回答时把它调严
    #   params = 采样参数（温度 / top_p / 惩罚 / 附加 JSON 透传），见 DEFAULT_PARAMS
    "ai": {"stream": True, "tools": True, "post": "off", "params": {}},
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
    # 骨架提示词的覆盖值（向导 / 出规则 / 工作台 / 工作台的手）。空 = 用程序里的出厂版。
    # 键只能是 BUILTIN_SYS 里那几个。整包导出导入走 /api/preset/*，见 PRESET_SCHEMA。
    "sys_prompts": {},
    # AI 工作台上那排快捷按钮。名字和内容全可改，可删可加可排序；空列表 = 一个按钮都不显示。
    "quick_actions": [
        {"id": "q1", "name": "总结选中消息", "text": "把这些消息压成 3-5 条要点，标出需要我行动的部分。"},
        {"id": "q2", "name": "抽出待办", "text": "从这些消息里抽出待办清单，每条含负责人和截止时间，没写就标未定。"},
        {"id": "q3", "name": "起草回复", "text": "针对最后一条消息起草一条中文回复，语气专业简洁，≤120 字。"},
        {"id": "q4", "name": "翻译成中文", "text": "把这些消息翻译成中文，保留原作者名。"},
    ],
    # 「批量提取」存下来的模板。一条 = 一次提取的全套参数（要提取什么、从哪儿找、读多少条）。
    # 能整包导出 / 导入，走跟规则包一样的预览闸。见 DEFAULT_TPL。
    "extract_templates": [],
    "models_cache": {},        # 每个服务商拉到过的模型名，纯缓存
    "retention_days": 14,
    "ai_daily_call_cap": 500,
}

DEFAULT_PROMPTS = {
    # 为什么写这么长：用户拿 DeepSeek v4 pro 都常收到「模型不太会按格式回答」。
    # 一句话的提示词对便宜模型不够 —— 字段含义、打分标尺、两个正反例（few-shot）
    # 三件都给齐，格式违规率才降得下来。改短了就会退回老样子。
    "ai_tag": (
        "你是 dcwatch 的消息分流助手。判断这条 Discord 消息对使用者是否重要。\n"
        "\n"
        "## 输出格式（硬要求）\n"
        "只输出一个 JSON 对象，不要 markdown 代码块、不要解释、不要前后缀。字段固定四个：\n"
        '{"score":0-100 的整数,"tags":["短标签",...],"reason":"一句话中文说明为什么给这个分",'
        '"todo":"需要使用者动手才写，否则空字符串"}\n'
        "\n"
        "## 打分标尺\n"
        "0-29 无关闲聊、表情、+1、单独一个链接没有说明\n"
        "30-59 有点关系但不用马上看\n"
        "60-84 值得提醒：有人在发放/转让可用资源、点名找他、有明确时效\n"
        "85-100 必须立刻看：限量名额、马上过期、直接 @ 他要答复\n"
        "\n"
        "## 例子（照这个格式答）\n"
        "消息：`有多余的 claude 车位一个，要的私`\n"
        '{"score":88,"tags":["送资源","车位"],"reason":"有人无偿转让可用车位，时效强",'
        '"todo":"私聊对方要车位"}\n'
        "消息：`哈哈哈哈笑死`\n"
        '{"score":5,"tags":["闲聊"],"reason":"纯闲聊，没有可行动内容","todo":""}\n'
        "\n"
        "## 拿不准怎么办\n"
        "看不清（内容在图片/附件里、被截断、疑似有意打乱字符）→ 别猜：score 给 60，"
        'tags 里加 "需人工看"，reason 写清你看不到什么。宁可让他自己看一眼，也不要漏。'),
    # B1 · AI 复核（规则的第二道闸）。用户盯的是白嫖 API key 的社群，发 key 的人会主动
    # 反侦察：中间插字符、后面加杂物再下一条说「删掉后面才是真的」、或者干脆把 key 塞附件里。
    # 正则必然筛不住这三种，所以命中宽条件之后交模型看一眼。
    # 最容易做错的是第三种：模型看不到附件时会**编一个 key**。所以下面给它一条体面的退路
    # （need_human=true），并且结尾三句硬话反复强调「看不到就说看不到，绝对不许编」。
    "ai_check": (
        "你是 dcwatch 的复核员。dcwatch 是一个 Discord 监听程序，使用者盯的是发放资源的社群，"
        "他关心的是：API key / 密钥 / 邀请码 / 兑换码 / 车位 / 名额 / 新开的资源。\n"
        "脚本已经用关键词粗筛过一遍了，现在请你判断这条消息**是不是真的那回事**，"
        "并且把被藏起来的密钥还原出来。\n"
        "\n"
        "## 输出格式（硬要求）\n"
        "只输出一个 JSON 对象。不要 markdown 代码块、不要 ```、不要解释、不要前后缀。字段固定六个：\n"
        '{"hit":true 或 false,"confidence":0-100 的整数,'
        '"kind":"key" 或 "invite" 或 "quota" 或 "resource" 或 "other" 或 "unreadable",'
        '"extracted":["还原出来的真密钥，可以多个，没有就空数组"],'
        '"need_human":true 或 false,"reason":"一句话中文，30 字内"}\n'
        "\n"
        "## 发的人常用的三种反侦察手段（重点看这些）\n"
        "① 密钥中间插了字符（星号、空格、全角括号、「去掉这个」之类的字）——"
        "还原成真密钥放进 extracted。\n"
        "② 密钥后面加了一坨杂物，紧接着的下一条消息说「上面那个删掉后面 xxx 才是真的」——"
        "给你的内容里带了这条消息前面几条，**要连起来读**，交出净化后的密钥。\n"
        "③ 密钥根本不在文字里（在附件 / 图片 / 用 /下载 命令发的 txt 里）——"
        "这时候 kind 填 unreadable、need_human 填 true、extracted 留空数组，程序会提醒使用者自己去看。\n"
        "\n"
        "## 例子（照这个格式答）\n"
        "消息：`新号池 sk-ab*c1**23 手慢无`\n"
        '{"hit":true,"confidence":90,"kind":"key","extracted":["sk-abc123"],'
        '"need_human":false,"reason":"key 中间插了星号，已还原"}\n'
        "消息：`sk-abc123ZZZZ`，下一条：`上面那个删掉最后四位才是真的`\n"
        '{"hit":true,"confidence":85,"kind":"key","extracted":["sk-abc123"],'
        '"need_human":false,"reason":"按下一条的说明去掉了尾部"}\n'
        "消息：`/下载 key.txt`\n"
        '{"hit":true,"confidence":50,"kind":"unreadable","extracted":[],'
        '"need_human":true,"reason":"key 在附件里，我看不到"}\n'
        "消息：`今天天气不错啊各位`\n"
        '{"hit":false,"confidence":95,"kind":"other","extracted":[],'
        '"need_human":false,"reason":"纯闲聊，没有任何资源"}\n'
        "\n"
        "## 三句硬话\n"
        "1. 看不到就说看不到（need_human=true），**绝对不许编一个密钥出来**——"
        "编的密钥会让使用者白跑一趟，比漏掉还糟。\n"
        "2. 不确定就给中间分（40-60），别硬凑 0 或 100。\n"
        "3. extracted 里只放你**真的在文本里看见过**的字符（去掉插进来的杂物算还原，"
        "补全缺失的位数不算）——不许补全、不许猜、不许拿例子里的 sk-abc123 顶。"),
    "ai_reply": "你是该频道的助手，用简洁中文回答，不超过 120 字。",
    "ai_summary": "把这段 Discord 对话压成 3-5 条要点中文摘要，标出待办和结论。",
    "ai_extract": "从消息中抽取结构化信息，只输出 JSON。字段自定，找不到就留空。",
    # 工作台的身份和边界在 WORKBENCH_SYS 里（可在界面改，默认是出厂版）。
    # 这条只是用户想追加的偏好，默认空。
    "ask": "",
}

# ---------- 采样参数（B4：用户原话「这才是让模型老实的根本」） ----------
# 只有这几个是白名单，别的想传就写进 extra（一段 JSON，原样并进请求体）。
# 值为 None / "" 表示「不传这个字段」，让服务商用自己的默认 —— 有些兼容接口
# 见到 presence_penalty 就 400，所以默认全空，只有温度给了个稳的 0.3。
DEFAULT_PARAMS = {
    "temperature": 0.3,        # 越低越死板越听话。判分/出规则这种活就该低
    "top_p": None,
    "max_tokens": None,        # 空 = 按用途各自的默认（判分 300、出规则 1600…）
    "presence_penalty": None,
    "frequency_penalty": None,
    "extra": "",               # 例如 {"reasoning_effort":"low"}，透传给服务商
}
PARAM_RANGE = {"temperature": (0.0, 2.0), "top_p": (0.0, 1.0), "max_tokens": (16, 32000),
               "presence_penalty": (-2.0, 2.0), "frequency_penalty": (-2.0, 2.0)}

# ---------- 提示词后处理（B2：抄酒馆 SillyTavern 的 prompt post-processing） ----------
AI_POST = {
    "off": "原样发（默认）。system 一条 + 历史照原样，兼容性最好的服务商都吃这套",
    "merge": "合并同角色：多条 system 并成开头一条，相邻的同角色消息并成一条，"
             "保证 user / assistant 严格交替、最后一条一定是 user",
    "strict": "严格：在「合并同角色」之上，把 system 也折进第一条 user 里 —— "
              "只剩 user/assistant 交替。给那些不认 system、或者认了也不当真的模型用",
}

# ---------- 转发出口 ----------
# 一条出口 = 一个 HTTP 请求。URL / 请求头 / 请求体里写 {{占位符}}，命中时替换成真值。
# 这样任何能收 webhook 的东西都能对接，程序里不写死任何第三方服务。
HOOK_FIELDS = {"id": "", "name": "", "url": "", "method": "POST", "content": "json",
               "headers": "", "body": '{"content": "{{text}}"}',
               "enabled": True, "verified": False}
HOOK_SIG = ("url", "method", "content", "headers", "body")   # 这几项一改，测试结果就作废
HOOK_VARS = ("text", "title", "body", "author", "channel", "server", "content",
             "url", "score", "tags", "todo", "json",
             # AI 复核（B1）：还原出来的密钥 / 要不要人工看。加了字段就得让用户用得上（硬规矩 5）
             "extracted", "need_human")


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
CREATE TABLE IF NOT EXISTS wb_sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT DEFAULT '', created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS wb_msgs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, sid INTEGER, r TEXT, t TEXT, acts TEXT, ts REAL);
-- AI 复核的流水（B1）。存在的意义是「为什么没提醒我」能查得到：
-- 复核判「不是」而被压掉的消息，用户自己是发现不了的，必须留痕。passed=最终有没有放行通知。
CREATE TABLE IF NOT EXISTS aicheck(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, rule TEXT, msg_id TEXT,
  hit INT, conf INT, kind TEXT, human INT, passed INT, extracted TEXT, reason TEXT, err TEXT);
CREATE INDEX IF NOT EXISTS idx_chk_ts ON aicheck(ts DESC);
-- 自动点开新帖（C2）。要持久化：重启后不能忘了自己开过哪些，否则会重复开一遍。
-- wanted=排队时间 opened_at=扩展回报开成功的时间 closed_at=已关 tries=试过几次 err=最后一次失败原因
-- bridge=哪个浏览器开的（多浏览器时关闭指令要发回给同一个）
CREATE TABLE IF NOT EXISTS threads_open(
  tid TEXT PRIMARY KEY, url TEXT, name TEXT, parent_id TEXT, guild_id TEXT,
  wanted REAL, opened_at REAL, closed_at REAL, tries INT DEFAULT 0, last_msg REAL,
  err TEXT DEFAULT '', bridge TEXT DEFAULT '');
-- 开服监听（D3）：定时探一个 URL，由「关」翻「开」时走通知管线（本机弹窗+声音+全部转发出口）。
-- 判定开/关：absent 命中正文→关；expect 设了→命中才开；都没设→HTTP<400 算开。
-- state: unknown(还没探过)/open/closed。last_status 最近一次 HTTP 码（0=没连上）。
-- 首次探测只落状态不提醒 —— 不然每加一个目标就先收一条「开了」，看着像误报。
CREATE TABLE IF NOT EXISTS watch(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, url TEXT, every_sec INT DEFAULT 60,
  expect TEXT DEFAULT '', absent TEXT DEFAULT '',
  enabled INT DEFAULT 1, notify_open INT DEFAULT 1, notify_close INT DEFAULT 0,
  state TEXT DEFAULT 'unknown', last_check REAL, last_change REAL,
  last_status INT DEFAULT 0, err TEXT DEFAULT '');
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
    # ---- AI 复核（B1）：命中之后再让模型看一眼，宽进严出 ----
    # 铁律：复核只减不增（match() 不中的消息永远不进模型，否则额度分分钟见底），
    # 模型挂了 fail open（照旧通知 + 标记），被压掉的必须留痕（日志 + aicheck 表 + 诊断包）。
    "ai_check": False,          # 开关：这条规则命中后，交模型复核一遍
    "ai_check_prompt": "",      # 留空＝用 DEFAULT_PROMPTS["ai_check"]
    "ai_check_min": 60,         # confidence 低于这个不通知（0 = 只要 hit 就通知）
    "ai_check_ctx": 3,          # 连同这条一起给模型看的前几条同频道消息（跨消息场景靠它）
    "ai_check_human": True,     # 模型说「我看不到」时也通知，正文标「需要你人工看」
}

# 正文里出现这些，说明真内容不在文字里 —— **跳过模型直接判 need_human**。
# 为什么不走模型：对着 `[附件 key.txt]` 这六个字，模型除了瞎猜没有别的可做，
# 而它瞎猜的结果就是编一个 key 出来（B1 最怕的失败模式）。省一次调用，还更准。
UNREADABLE_HINTS = ("[附件", "[图片", "[贴纸", "[文件", "/下载", ".txt", ".json", ".zip",
                    "[attachment", "[image")


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

## 一件不属于规则的事（撞上了直接指路，别硬编成规则）
他要的是「盯**某个服务/服务器开没开**、开放登记没有」时，那是侧栏**「开服监听」**页的活
（填网址，程序定时探，关翻开就提醒，能加很多个），**不是**监听规则。这种情况在 understood 里
说清「这个得用『开服监听』页，不是规则」，然后问他是不是还想顺带盯那个服务器的公告频道。

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

他要建规则时：**你自己就能建**（见下面的工具）。条件不清楚就先问一两个关键的（盯哪个频道、
什么词、要不要算机器人），别一次问八个。他要是想一步步来，「监听规则」页顶部还有个
「◆ 帮我建条规则」的向导。

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

## 这个程序有哪些功能（他问「你能不能……」时先在这张表里找，别凭印象说做不到）
按界面左边侧栏从上到下，每条后面是「去哪儿配」：

- **收信箱** —— 进来的消息都在这儿；点开任一条能看到作者 ID、频道 ID，可以「按这条消息新建规则」。
  注意：**没有任何规则罩得住的消息根本不入库**（这是他自己要的：写什么规则就收什么信）。
  所以「收信箱空的」通常不是坏了，是没规则或规则没框住那个频道。
- **监听规则** —— 新消息 / 新帖的提醒规则。你自己就能建改（见下面的工具）。
- **AI 复核（规则里的一个折叠区）** —— 脚本粗筛命中之后再叫模型看一眼，专门对付发密钥的人
  反侦察：中间插字符、后面加杂物再说「删掉后面才是真的」、把密钥塞进附件。看不到附件时会
  发一条「需要你人工看」的提醒，不会瞎编。
- **批量提取** —— 对**已经在库里的**历史消息批量挑东西（密钥、名额之类），结果能导 CSV。
- **AI 工作台** —— 就是你现在待的地方，可以多开会话，聊天记录存在本机、刷新不丢。
- **通知与转发** —— 本机弹窗 / 提示音 / 免打扰时段 / 分数门槛；转发出口可以配多个
  （Discord webhook、Telegram、Server酱、企业微信、任意 HTTP）。
- **开服监听** —— **盯「某个服务开没开」**：填一个网址（服务的登记页、官网、Discord 邀请链接都行），
  程序按他设的间隔去探，**由「关」翻「开」的那一刻**走全套提醒（弹窗+声音+全部转发出口）。
  可以加**任意多个**目标，每个目标能单独设「包含这段文字才算开」/「包含这段文字就算关」。
  → **所以「能不能监听某个服务器/服务开没开、开放登记没有」的答案是：能，去侧栏「开服监听」页加一个目标。**
  这跟规则是两条独立的路：规则听的是频道里的消息，开服监听探的是网址状态。
- **自动点开新帖（在「设置」里，默认关着）** —— 论坛里的新帖不点开是读不到帖内消息的，
  开了之后程序会替他在**新开的最小化浏览器窗口**里点开新帖，有同时开几个/每小时几个的上限，
  闲置一段时间自动关掉腾位置。
- **模型接入** —— 多个模型服务、每个服务自己拉模型列表、采样参数（温度/top_p）、
  提示词抽屉（包括你现在这段身份提示词，他能改也能恢复出厂）、格式后处理三档。
- **运行日志 / 导出诊断** —— 一份 txt 说清「为什么它没提醒我」，排查时先要这个。
- **设置** —— 收信方式（浏览器旁听 / Bot Token / 个人 Token）、代理、开机自启、抓历史。

功能表之外的问题，就说「这个我不确定，去 XX 页看看有没有」——**不许直接断言这个程序做不到**。
断言做不到又恰好是有的功能，是这里最严重的错误（他就是因为这个骂过一次）。

## 你能做和不能做
能：**直接读写监听规则**（建、改、开关、删、试算，见下面的工具）、解读给你的消息、总结、抽待办、
起草回复、解释这个程序里的功能、把他贴的链接拆成 ID。
不能：上网查东西、改模型 Key / 通知出口 / 开服监听目标这些设置（那些得他自己去对应的页面，
你只能告诉他去哪一页、填什么）、替他在 Discord 发言。
注意「不能」说的是**你这只手伸不到**，不是**程序做不到** —— 两件事别混起来答。

## 一句话原则
**能自己动手就别写教程。** 用户来问你，是因为他不想自己一格一格找。
「打开 X 页 → 点 Y → 找到 Z 勾 → 保存」这种回答，在这里是最差的回答。

## 说话方式
中文，简洁。动完手就用一两句话说清「我改了什么、现在会怎样」，不要复述整条规则。
不知道或者程序里确实没有的功能，就直说没有，别编一个设置项出来。

## 关于表情包、图片、贴纸
扩展会把纯图片/贴纸/表情的消息也报上来，正文写成 `[图片]`、`[贴纸 xx]`、`:emoji:` 这样；
夹在文字中间的表情也会原地保留（`看 :kekw: 这个`），Discord 给表情码加的 `~1` 尾巴会被剥掉。
所以「连表情包也提醒我」是做得到的，条件是：那条规则不能有关键词/正则（纯表情没有文字，
关键词永远匹配不上），min_len 要是 0。

## 这段提示词的维护约定（给以后改它的人看）
上面那张功能表是模型判断「能不能做」的唯一依据。**程序加了新功能就必须往表里补一行**，
不然模型会照着旧表回答「做不到」——这件事真的发生过（开服监听做完了，模型还在说做不到）。
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
    clamp_rule(rule)
    if rule.get("ai_check") and not model:
        notes.append("这条开了 AI 复核，也要先在「模型接入」里选一个模型，否则复核会一直失败"
                     "（失败时按放行处理，不会吞消息）")
    return rule, notes


# ======================================================================
# 规则的导入 / 导出：让「拿去直接用」的规则包能在两台机器之间搬
# ======================================================================
RULES_SCHEMA = "dcwatch.rules/1"      # 认这个名字才当规则包；将来字段不兼容了就 /2

# 导入时给规则盖的本机戳。**不在 DEFAULT_RULE 里，所以不会被导出** —— 跟 hits 一样是本机的账，
# 别人拿到我的规则包不该看见「我是从哪一版导进来的」。存在的意义只有一个：
# 排查「填好了怎么不响」时，诊断包 [4] 段能说清这条规则是他自己填的还是从别人的包导进来的。
IMPORT_MARKS = ("imported_at", "imported_from")


def rule_for_export(r):
    """一条库里的规则 → 导出用的干净字典。id / hits 是本机的账，不跟着走。"""
    out = {k: r.get(k, v) for k, v in DEFAULT_RULE.items()}
    out["enabled"] = 1 if r.get("enabled", 1) else 0
    return out


def sanitize_import_rule(draft):
    """把导入的一条规则洗成合法规则，返回 (规则, 这条的提示)。

    跟 sanitize_draft 的区别：**不校验 ID 存不存在**。导出方的频道 ID 在导入方的
    消息库里当然查不到（一条消息都还没收到），按 known_ids 过滤会把整条规则洗空，
    用户看到的是「导进来了但什么都不听」——比报错更难查。这里只要求 ID 是纯数字。
    """
    notes, rule = [], dict(DEFAULT_RULE)
    if not isinstance(draft, dict):
        return None, ["这一项不是规则对象，已跳过"]
    unknown = [k for k in draft if k not in DEFAULT_RULE
               and k not in ("id", "enabled", "hits") + IMPORT_MARKS]
    for k, v in draft.items():
        if k not in DEFAULT_RULE or v is None:
            continue
        d = DEFAULT_RULE[k]
        if isinstance(d, list):
            vals = [str(x).strip() for x in (v if isinstance(v, list) else [v]) if str(x).strip()]
            if k.endswith("_ids"):
                bad = [x for x in vals if not x.isdigit()]
                vals = [x for x in vals if x.isdigit()]
                if bad:
                    notes.append(f"{k} 里有不像 ID 的值（{', '.join(bad[:3])}），已丢掉")
            rule[k] = vals
        elif isinstance(d, bool):
            rule[k] = bool(v)
        elif isinstance(d, int):
            with contextlib.suppress(Exception):
                rule[k] = int(v)
        else:
            rule[k] = str(v)
    if unknown:
        notes.append(f"不认识的字段已丢掉：{', '.join(unknown[:5])}")
    rule["name"] = (rule["name"] or "").strip() or "导入的规则"
    kinds = [k for k in rule.get("kinds") or [] if k in ("msg", "thread")]
    rule["kinds"] = kinds or ["msg"]
    if rule["action"] not in ACTIONS:
        notes.append(f"动作「{rule['action']}」不认识，按「只提醒我」处理")
        rule["action"] = "notify"
    clamp_rule(rule)
    return rule, notes


def diff_rule(old, new):
    """两条规则的差异，译成人话给导入预览用（只列真变了的字段）。"""
    def show(v):
        if isinstance(v, bool):
            return "开" if v else "关"
        if isinstance(v, list):
            return "、".join(str(x) for x in v) if v else "空"
        return str(v) if str(v) != "" else "空"
    out = []
    for k in DEFAULT_RULE:
        a, b = old.get(k, DEFAULT_RULE[k]), new.get(k, DEFAULT_RULE[k])
        if a != b:
            out.append(f"{RULE_LABELS.get(k, k)}：{show(a)} → {show(b)}")
    return out


RULE_LABELS = {
    "name": "规则名", "kinds": "触发类型", "guild_ids": "服务器", "channel_ids": "频道",
    "thread_ids": "子区", "include_threads_of_channels": "连子区一起", "dm": "私信",
    "accounts": "账号", "author_ids": "只听这些人", "author_name_contains": "昵称包含",
    "ignore_bots": "忽略机器人", "mention_only": "只在@我", "keywords_any": "含任一关键词",
    "keywords_all": "必须全含", "regex": "正则", "min_len": "最短字数", "action": "动作",
    "model": "模型", "provider": "接入点", "prompt": "提示词", "reply_in_thread": "回帖内回复",
    "notify_min_score": "低于此分不提醒", "summary_every": "每 N 条摘要",
    "cooldown_sec": "冷却秒数", "max_per_hour": "每小时上限", "webhook_url": "转发地址",
    "source": "来源",
    "ai_check": "AI 复核", "ai_check_prompt": "复核提示词", "ai_check_min": "复核门槛",
    "ai_check_ctx": "带前几条上下文", "ai_check_human": "看不到也提醒",
}

# 规则里几个整数字段的合理区间。越界不静悄悄夹住就会出现「我设了 3 条上下文，
# 它却一条都不带」这种查不出原因的行为（跟 norm_params 同一条道理）。
RULE_CLAMP = {"ai_check_min": (0, 100), "ai_check_ctx": (0, 12)}


def clamp_rule(rule):
    """把规则里越界的整数夹回区间。原地改并返回，方便串在洗规则的末尾。"""
    for k, (lo, hi) in RULE_CLAMP.items():
        with contextlib.suppress(Exception):
            rule[k] = max(lo, min(hi, int(rule.get(k, DEFAULT_RULE[k]))))
    return rule


# ======================================================================
# 批量提取的模板：把「要提取什么 + 从哪儿找 + 读多少条」存下来，并且能整包搬走
#
# 为什么要有：批量提取以前是个每次手打的输入框。同一件事（"把所有兑换码挑出来"）
# 他每周要重打一遍，换台机器、或者想把用法分享给别人，都只能靠复制粘贴。
# 走的是跟规则包**完全一样**的那套：schema 头 + dry_run 预览闸 + 认名字不认 id。
# ======================================================================
EXTRACT_SCHEMA = "dcwatch.extract/1"      # 认这个名字才当模板包；字段不兼容了就 /2

DEFAULT_TPL = {
    "name": "",               # 模板名，也是导入时的重名判定依据
    "want": "",               # 要提取什么（人话）
    "channel_id": "",         # 只在这个频道找，空 = 全部
    "limit": 500,             # 最多读几条
    "only_matched": False,    # 只看规则命中过的
    "author_contains": "",    # 昵称包含
    "note": "",               # 给自己看的备注
}
TPL_LABELS = {"name": "模板名", "want": "要提取什么", "channel_id": "只在这个频道找",
              "limit": "最多读几条", "only_matched": "只看规则命中过的",
              "author_contains": "只看昵称含", "note": "备注"}

# 导入时盖的本机戳。跟规则那边同样的道理：**不在 DEFAULT_TPL 里，所以不会被导出去**。
# 塞进 DEFAULT_TPL 就等于把「我是从谁那儿导来的」发给下一个人（e2e_imp.py 第 13 节钉过）。
TPL_MARKS = ("imported_at", "imported_from")


def norm_tpl(t: dict, i: int = 0) -> dict:
    """一条模板洗成合法形状。名字或 want 空的返回 None —— 界面上不该出现空模板。"""
    if not isinstance(t, dict):
        return None
    out = dict(DEFAULT_TPL)
    for k, d in DEFAULT_TPL.items():
        v = t.get(k, d)
        if isinstance(d, bool):
            out[k] = bool(v)
        elif isinstance(d, int):
            with contextlib.suppress(Exception):
                out[k] = int(v)
        else:
            out[k] = str(v if v is not None else d).strip()
    out["name"] = out["name"][:40]
    out["want"] = out["want"][:2000]
    out["note"] = out["note"][:200]
    out["author_contains"] = out["author_contains"][:80]
    # 频道 ID 只认纯数字。**不校验本机有没有这个频道** —— 导出方的频道 ID 在导入方
    # 库里当然查不到（一条消息都还没收到），照 known_ids 洗会把模板洗成"全部消息"，
    # 用户看到的是"导进来了但范围不对"，比报错更难查。
    if not out["channel_id"].isdigit():
        out["channel_id"] = ""
    out["limit"] = max(1, min(out["limit"] or 500, 2000))
    if not out["name"] or not out["want"]:
        return None
    out["id"] = str(t.get("id") or f"t{i + 1}{random.randrange(16 ** 4):04x}")[:16]
    for k in TPL_MARKS:                    # 本机戳原样留着，只有导出时才摘掉
        if t.get(k):
            out[k] = str(t[k])[:40]
    return out


def norm_tpls(items) -> list:
    out, seen = [], set()
    for i, t in enumerate(items if isinstance(items, list) else []):
        n = norm_tpl(t, i)
        if not n or n["id"] in seen:
            continue
        seen.add(n["id"])
        out.append(n)
    return out[:40]


def tpl_for_export(t: dict) -> dict:
    """一条模板 → 导出用的干净字典。id 和本机戳是本机的账，不跟着走。"""
    return {k: t.get(k, v) for k, v in DEFAULT_TPL.items()}


def diff_tpl(old: dict, new: dict) -> list:
    """两条模板的差异，译成人话给导入预览用（只列真变了的）。"""
    def show(v):
        if isinstance(v, bool):
            return "是" if v else "否"
        s = str(v)
        return (s[:60] + "…") if len(s) > 60 else (s or "空")
    out = []
    for k in DEFAULT_TPL:
        a, b = old.get(k, DEFAULT_TPL[k]), new.get(k, DEFAULT_TPL[k])
        if a != b:
            out.append(f"{TPL_LABELS.get(k, k)}：{show(a)} → {show(b)}")
    return out


def sanitize_import_tpl(draft):
    """导入的一条模板 → (模板, 这条的提示)。看不懂的返回 (None, 提示)。"""
    if not isinstance(draft, dict):
        return None, ["这一项不是模板对象，已跳过"]
    notes = []
    unknown = [k for k in draft if k not in DEFAULT_TPL and k not in ("id",) + TPL_MARKS]
    if unknown:
        notes.append(f"不认识的字段已丢掉：{', '.join(unknown[:5])}")
    raw_ch = str(draft.get("channel_id") or "").strip()
    t = norm_tpl({k: v for k, v in draft.items() if k not in TPL_MARKS})
    if t is None:
        return None, notes + ["这一项没有名字或者没写要提取什么，已跳过"]
    if raw_ch and not t["channel_id"]:
        notes.append(f"「只在这个频道找」里那个值不像频道 ID（{raw_ch[:20]}），已改成不限频道")
    elif t["channel_id"]:
        notes.append(f"限定了频道 {t['channel_id']} —— 那是对方机器上的频道，"
                     "你这边不一定收得到；不对就把它清空")
    return t, notes


# ======================================================================
# 工作台的「手」：让模型自己动手改规则，而不是教用户去点第几个勾
# ======================================================================
RULE_FIELDS_DOC = """规则字段（只写你确定要改的，别的别动）：
  name  规则名（中文短句）
  kinds  ["msg"]=有人发消息，["thread"]=有人开新帖/新子区，两个都要就都写
  guild_ids / channel_ids / thread_ids  只听这些 ID（空数组=不限）
  include_threads_of_channels  true 时 channel_ids 连它下面的子区/帖子一起算
  dm  true=连私信一起听（默认 false，所以私信默认不提醒）
  accounts  只听这些 Discord 账号（多号旁听时分流用）
  author_ids  只听这些人 / author_name_contains  昵称包含
  ignore_bots  忽略机器人发的（默认 true；空投、开奖这类公告是机器人发的，要 false）
  mention_only  true=只有 @我 才算
  keywords_any 含任一即命中 / keywords_all 必须全含 / regex 正则
  min_len  最短字数。>0 会把纯图片、贴纸、表情包挡在外面；想连表情包都收就设 0
  action  notify(只提醒) / ai_tag(打分) / ai_reply(自动回复) / ai_summary(摘要) /
          ai_extract(抽取) / webhook(转发)
  prompt  要让模型干的活（action 是 ai_* 时才有意义）
  notify_min_score  action=ai_tag 时低于这个分不提醒（0-100）
  cooldown_sec 冷却秒数 / max_per_hour 每小时最多提醒几次"""

WB_TOOLS = [
    {"type": "function", "function": {
        "name": "list_rules",
        "description": "列出用户现在所有的监听规则：id、名字、开关、命中次数和全部条件。"
                       "要改规则前先调它拿 id，别猜。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "update_rule",
        "description": "修改一条已有规则并立刻生效。只在 patch 里写要改的字段，没写的保持原样。\n"
                       + RULE_FIELDS_DOC,
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "规则 id，从 list_rules 拿"},
            "patch": {"type": "object", "description": "要改的字段，键名同上"}},
            "required": ["id", "patch"]}}},
    {"type": "function", "function": {
        "name": "create_rule",
        "description": "新建一条监听规则并保存。用户明确要「加一条」时才用；改现有的用 update_rule。\n"
                       + RULE_FIELDS_DOC,
        "parameters": {"type": "object", "properties": {
            "rule": {"type": "object", "description": "规则字段，见上"},
            "enabled": {"type": "boolean", "description": "是否直接启用，默认 true"}},
            "required": ["rule"]}}},
    {"type": "function", "function": {
        "name": "set_rule_enabled",
        "description": "启用或停用一条规则。",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string"}, "enabled": {"type": "boolean"}},
            "required": ["id", "enabled"]}}},
    {"type": "function", "function": {
        "name": "delete_rule",
        "description": "删除一条规则。这是不可逆的，用户没有明确说「删掉」就别用——"
                       "多数时候他要的是停用（set_rule_enabled）。",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}},
                       "required": ["id"]}}},
    {"type": "function", "function": {
        "name": "test_rule",
        "description": "拿一条假消息试算某规则会不会命中，并告诉你卡在哪个条件上。改完规则应该试一次。",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "要试算的规则 id"},
            "content": {"type": "string", "description": "假消息正文，比如 [图片] 或 有人发key了"},
            "channel_id": {"type": "string"}, "guild_id": {"type": "string"},
            "author": {"type": "string"},
            "author_id": {"type": "string", "description": "发消息人的用户 ID；试算盯人规则时填，留空=用规则「只听这些人」的第一个"},
            "is_bot": {"type": "boolean"},
            "is_dm": {"type": "boolean"},
            "kind": {"type": "string", "description": "msg 或 thread"}},
            "required": ["id"]}}},
    {"type": "function", "function": {
        "name": "list_channels",
        "description": "最近收到过消息的频道和人，带真实 ID。填 ID 前先查这个，不许编 ID。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "search_messages",
        "description": "在已经收到的消息里搜。回答「有人发过 xx 吗」用它。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "关键词，留空=最近的消息"},
            "channel_id": {"type": "string"},
            "limit": {"type": "integer", "description": "最多几条，默认 20，上限 100"}}}}},
    {"type": "function", "function": {
        "name": "get_status",
        "description": "这台机器现在的状况：收信方式、有没有浏览器在旁听、程序自查出来的问题。"
                       "用户说「怎么没提醒我」时先调它。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "export_rules",
        "description": "把规则打包成「能搬到另一台机器」的规则包（跟界面「导出规则」按钮同一份）。"
                       "用户说「我的规则给我看看 / 备份一下 / 发给朋友 / 换台电脑」时用它。"
                       "转发地址会打码，所以要给别人的完整文件得让他点界面上的「导出规则」按钮。"
                       "只想看看现有条件用 list_rules 就够，别拿这个包回头当新规则写回去。",
        "parameters": {"type": "object", "properties": {
            "ids": {"type": "array", "items": {"type": "string"},
                    "description": "只导这几条的 id（从 list_rules 拿）；留空=全部"}}}}},
    {"type": "function", "function": {
        "name": "list_open_threads",
        "description": "程序替用户自动点开着哪些论坛帖子（标签页），每个开了多久、最后一条消息多久前、"
                       "收到过几条。用户问「现在开着哪些帖子 / 哪些可以关了 / 是不是开太多了」时用它。"
                       "**只读**：你没有关标签页的工具 —— 该关哪些你可以建议，"
                       "真正的关闭由程序按「闲置多久自动关」那个设置来做，或者他自己点。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "list_providers",
        "description": "模型端点配置：每家服务的地址、Key 填没填、拉到过哪些模型、默认模型是谁、"
                       "今天调了几次。用户说「模型用不了 / 拉不到模型 / 换个模型」时先调它看清楚。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "list_hooks",
        "description": "通知与转发的配置：本机弹窗/声音/网页通知开关、免打扰时段、每条出口"
                       "（转发地址打码）。用户问「通知发到哪了 / 为什么没转发」时用它。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "recent_hits",
        "description": "最近命中过规则的消息，带命中了哪条规则。用户问「最近都提醒了些啥 / "
                       "刚才那条是谁发的」时用它。",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "最多几条，默认 20，上限 100"}}}}},
    {"type": "function", "function": {
        "name": "test_message",
        "description": "拿一条假消息对**全部规则**逐个试算，每条会命中还是卡在哪个条件上。"
                       "用户问「这条消息为什么没提醒我」时用它，比一条条 test_rule 快。",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string", "description": "假消息正文"},
            "channel_id": {"type": "string"}, "guild_id": {"type": "string"},
            "author": {"type": "string"}, "author_id": {"type": "string"},
            "is_bot": {"type": "boolean"}, "is_dm": {"type": "boolean"},
            "kind": {"type": "string", "description": "msg 或 thread"}},
            "required": ["content"]}}},
    {"type": "function", "function": {
        "name": "get_logs",
        "description": "最近的运行日志（报错、出口失败、规则执行失败都在里面）。"
                       "排查「哪里炸了」时调它。",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "最多几条，默认 30，上限 100"}}}}},
    {"type": "function", "function": {
        "name": "export_extract_templates",
        "description": "把「批量提取」页存的模板整包导出来（跟界面「导出」按钮同一份）。"
                       "用户要备份/分享提取模板时用它。没有导入工具，导入必须人在界面上点。",
        "parameters": {"type": "object", "properties": {}}}},
]

# **故意没有 import_rules 工具**（硬规矩 10）：导入会覆盖、甚至删掉用户手填了一晚上的规则，
# 那道「先预览再落库」的闸在界面上（`dry_run` 预览 → 他点确认才第二次请求真写）。
# 模型自己吃一个文件就绕过了闸。要帮他搬别人的规则：把包念清楚，然后让他点界面上的「导入规则」按钮。
WB_WRITE_TOOLS = ("update_rule", "create_rule", "set_rule_enabled", "delete_rule")


def ago_txt(ts):
    d = max(0, int(now() - (ts or 0)))
    if d < 60:
        return f"{d} 秒前"
    if d < 3600:
        return f"{d // 60} 分钟前"
    if d < 86400:
        return f"{d // 3600} 小时前"
    return f"{d // 86400} 天前"


def brief_rule(r):
    """给模型看的规则摘要：全字段太长，但条件一个都不能省——它得据此判断改哪里。"""
    keep = ("name", "kinds", "guild_ids", "channel_ids", "thread_ids", "include_threads_of_channels",
            "dm", "accounts", "author_ids", "author_name_contains", "ignore_bots", "mention_only",
            "keywords_any", "keywords_all", "regex", "min_len", "action", "prompt",
            "notify_min_score", "cooldown_sec", "max_per_hour")
    out = {"id": str(r.get("id")), "enabled": bool(r.get("enabled")), "hits": r.get("hits", 0)}
    for k in keep:
        v = r.get(k)
        if v not in ([], "", 0, None) or k in ("name", "action", "ignore_bots", "kinds"):
            out[k] = v
    return out


# 给模型看的规则包 = 界面「导出规则」那一份，加三条差别：转发地址打码（那是个能往外发东西的
# 密址，不该跟着对话上传到模型厂商那边）、超长提示词截断、整包太大时退化成清单。
# **完整、可直接导入的文件永远只有一条路**：界面「监听规则」页的「导出规则」按钮。
WB_MASK = "***（转发地址不给模型看，导出文件里是完整的）"
WB_PACK_LIMIT = 3500        # 工具结果喂回模型时会被截到 6000 字，超了它看到的是半截 JSON


def wb_export_pack(rules, ids=None):
    """(给模型看的规则包, 提示列表)。ids 只导这几条，留空=全部。"""
    notes = []
    if ids:
        want = [str(x) for x in (ids if isinstance(ids, list) else [ids])]
        have = {str(r["id"]) for r in rules}
        miss = [x for x in want if x not in have]
        rules = [r for r in rules if str(r["id"]) in want]
        if miss:
            notes.append("这些 id 不存在，已跳过：" + "、".join(miss[:5]))
    out, masked = [], 0
    for r in rules:
        e = rule_for_export(r)
        if e.get("webhook_url"):
            e["webhook_url"], masked = WB_MASK, masked + 1
        out.append(e)
    if masked:
        notes.append(f"有 {masked} 条的转发地址打了码 —— 那是密址，不随对话外发。"
                     "用户自己点「导出规则」按钮下载到的文件里是完整的")
    pack = {"schema": RULES_SCHEMA, "app": "dcwatch", "version": VERSION,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"), "count": len(out), "rules": out}
    if len(json.dumps(pack, ensure_ascii=False)) > WB_PACK_LIMIT:
        for e in out:
            if len(e.get("prompt") or "") > 80:
                e["prompt"] = e["prompt"][:80] + "…（提示词太长，这里截断了）"
        notes.append("规则条数多，提示词正文在这里被截断了，导出文件里是完整的")
    if len(json.dumps(pack, ensure_ascii=False)) > WB_PACK_LIMIT:
        pack = {"schema": RULES_SCHEMA, "app": "dcwatch", "version": VERSION, "count": len(out),
                "rules": [{"name": e["name"], "action": e["action"]} for e in out],
                "truncated": True}
        notes.append(f"{len(out)} 条规则整包太大，塞不进这轮对话，上面只是清单。"
                     "要完整的就让用户点「监听规则」页的「导出规则」按钮下载文件")
    return pack, notes


async def run_wb_tool(app, name, args, allow_ids):
    """执行一个工作台工具。返回 (给模型看的 dict, 给用户看的一句话, 有没有改动配置)。
    所有写操作都在这里落地，模型改完立刻生效——这就是「能干活」和「只会讲步骤」的区别。"""
    args = args if isinstance(args, dict) else {}
    rules = app.rules(False)
    byid = {str(r["id"]): r for r in rules}

    def need_rule():
        rid = str(args.get("id") or "").strip()
        r = byid.get(rid)
        if not r:
            raise ValueError(f"没有 id={rid} 这条规则。现有的是：" +
                             ("、".join(f"{x['id']}={x.get('name')}" for x in rules) or "一条都没有"))
        return rid, r

    if name == "list_rules":
        return {"rules": [brief_rule(r) for r in rules],
                "note": "改哪条就用它的 id 调 update_rule" if rules else "他一条规则都没有"}, "", False

    if name == "list_channels":
        txt, known = app.rule_ctx()
        return {"known": txt}, "", False

    if name == "list_open_threads":
        c = app.br_cfg()
        rows = []
        for x in app.open_rows():
            last = app.thread_last(x["tid"], x["opened_at"])
            n = app.db.q("SELECT COUNT(*) n FROM messages WHERE channel_id=? AND kind!='thread'",
                         (x["tid"],))[0]["n"]
            rows.append({"tid": x["tid"], "name": x["name"], "parent_id": x["parent_id"],
                         "opened": ago_txt(x["opened_at"]), "last_msg": ago_txt(last) if last else "还没有消息",
                         "idle_min": int((now() - last) / 60) if last else None, "msgs": n})
        pend = app.db.q("""SELECT COUNT(*) n FROM threads_open
                           WHERE opened_at IS NULL AND closed_at IS NULL""")[0]["n"]
        return {"auto_open": c["auto_open"], "open": rows, "queued": pend,
                "max_tabs": c["max_tabs"], "close_idle_min": c["close_idle_min"],
                "note": ("自动点开新帖是关着的，所以这里是空的 —— 论坛里的帖子他不点开，"
                         "网页里就没有帖内消息，扩展也读不到。要开在「设置」页的"
                         "「自动点开新帖」那一块" if not c["auto_open"] else
                         "你只能念和建议，不能关标签页。闲置超过 "
                         f"{c['close_idle_min']} 分钟程序会自己关（0=不自动关）")}, "", False

    if name == "get_status":
        bl = app.bridge_list()
        return {"version": VERSION, "mode": app.cfg["discord"].get("mode", "browser"),
                "can_send": can_send(app),
                "bridges": [{"account": b.get("account"), "where": b.get("where"),
                             "fresh": b["fresh"], "ver": b.get("ver")} for b in bl],
                "findings": app.findings(),
                "msgs": app.db.q("SELECT COUNT(*) n FROM messages")[0]["n"]}, "", False

    if name == "export_rules":
        pack, notes = wb_export_pack(rules, args.get("ids"))
        pack["notes"] = notes
        pack["how_to_get_the_file"] = (
            "完整文件在界面「监听规则」页顶部的「导出规则」按钮，点一下就下载，文件名形如 "
            f"dcwatch-规则{pack['count']}条-v{VERSION}-0731-1740.json")
        pack["how_to_import"] = ("对方在他那台机器上点旁边的「导入规则」按钮选这个文件，"
                                 "程序会先给他看「哪几条会被覆盖」再让他确认。"
                                 "你自己没有导入工具，这一步必须由人点")
        return pack, "", False

    if name == "search_messages":
        q = (args.get("query") or "").strip()
        where, a = ["1=1"], []
        if q:
            where.append("lower(content) LIKE ?")
            a.append("%" + q.lower() + "%")
        if args.get("channel_id"):
            where.append("(channel_id=? OR parent_id=?)")
            a += [str(args["channel_id"])] * 2
        lim = max(1, min(int(args.get("limit") or 20), 100))
        rows = app.db.q("SELECT author,content,channel_name,channel_id,ts FROM messages WHERE "
                        + " AND ".join(where) + " ORDER BY ts DESC LIMIT ?", a + [lim])
        return {"count": len(rows),
                "messages": [{"author": r["author"], "channel": r["channel_name"],
                              "channel_id": r["channel_id"], "content": (r["content"] or "")[:300],
                              "when": ago_txt(r["ts"])} for r in rows]}, "", False

    if name == "test_rule":
        rid, r = need_rule()
        ev = {"source": "discord", "kind": args.get("kind") or "msg",
              "guild_id": str(args.get("guild_id") or (r["guild_ids"] or [""])[0]),
              "channel_id": str(args.get("channel_id") or (r["channel_ids"] or [""])[0]),
              "parent_id": "", "channel_name": "test", "is_thread": False,
              "author_id": str(args.get("author_id") or (r["author_ids"] or [""])[0]),
              "author": args.get("author") or "someone",
              "is_bot": bool(args.get("is_bot")), "content": args.get("content") or "",
              "is_dm": bool(args.get("is_dm")), "mentions_me": False, "ts": now(), "msg_id": "0"}
        ok, why = app.match(r, ev)
        return {"match": ok, "why": NICE_WHY.get(why, why),
                "enabled": bool(r["enabled"]),
                "note": "" if r["enabled"] else "注意：这条规则是停用状态，就算算得中也不会提醒"}, "", False

    if name == "set_rule_enabled":
        rid, r = need_rule()
        on = 1 if args.get("enabled", True) else 0
        app.db.x("UPDATE rules SET enabled=? WHERE id=?", (on, rid))
        return ({"ok": True, "id": rid, "enabled": bool(on)},
                f"{'启用' if on else '停用'}了规则「{r.get('name')}」", True)

    if name == "delete_rule":
        rid, r = need_rule()
        app.db.x("DELETE FROM rules WHERE id=?", (rid,))
        return ({"ok": True, "deleted": rid},
                f"删除了规则「{r.get('name')}」（这一步不可逆）", True)

    if name in ("update_rule", "create_rule"):
        notes = []
        if name == "update_rule":
            rid, r = need_rule()
            patch = args.get("patch")
            if not isinstance(patch, dict) or not patch:
                raise ValueError("patch 是空的，没有可改的字段")
            bad = [k for k in patch if k not in DEFAULT_RULE]
            merged = dict(r)
            merged.update({k: v for k, v in patch.items() if k in DEFAULT_RULE})
            enabled = int(r["enabled"])
        else:
            rid, patch, bad = None, (args.get("rule") or {}), []
            if not isinstance(patch, dict) or not patch:
                raise ValueError("rule 是空的")
            bad = [k for k in patch if k not in DEFAULT_RULE]
            merged = dict(patch)
            enabled = 1 if args.get("enabled", True) else 0
        if bad:
            notes.append("这些字段名不存在，已忽略：" + "、".join(bad[:5]))
        # 规则里本来就有的 ID 是真的，别被当成模型编的给洗掉
        ok_ids = set(allow_ids) | {str(x) for k in ("guild_ids", "channel_ids", "thread_ids", "author_ids")
                                   for x in ((byid.get(str(rid)) or {}).get(k) or [])}
        rule, notes = sanitize_draft(merged, notes, ok_ids, app.cfg["default_model"].get("model") or "")
        blob = json.dumps({k: v for k, v in rule.items() if k in DEFAULT_RULE}, ensure_ascii=False)
        if rid:
            app.db.x("UPDATE rules SET json=?,enabled=? WHERE id=?", (blob, enabled, rid))
            changed = [k for k, v in patch.items() if k in DEFAULT_RULE]
            human = f"改了规则「{rule.get('name')}」的：" + "、".join(changed[:6])
        else:
            rid = app.db.x("INSERT INTO rules(json,enabled) VALUES(?,?)", (blob, enabled))
            human = f"新建了规则「{rule.get('name')}」" + ("" if enabled else "（先停用着）")
        return ({"ok": True, "id": str(rid), "rule": brief_rule(dict(rule, id=rid, enabled=enabled)),
                 "notes": notes}, human, True)

    # ---- B6：只读工具一批（模型「能调用的功能太少」）。写工具仍只有规则那四个 ----
    if name == "list_providers":
        dm = app.cfg["default_model"]
        used = app.db.q("SELECT COUNT(*) n FROM aiusage WHERE ts>?", (now() - 86400,))[0]["n"]
        cache = app.cfg.get("models_cache") or {}
        return {"default": f"{dm.get('provider')} / {dm.get('model')}",
                "providers": [{"name": p.get("name"), "base_url": p.get("base_url"),
                               "key": "已填" if p.get("api_key") else "空（空 Key 拉模型必 401）",
                               "models": (cache.get(p.get("name")) or [])[:30]}
                              for p in app.cfg.get("providers", [])],
                "ai_daily": f"{used} / {app.cfg.get('ai_daily_call_cap', 500)}",
                "note": "Key 空的那一家：到「模型接入」页粘 Key → 按「保存服务商」→ 再拉模型列表。"
                        "改端点配置只能人在界面上做，你没有写工具"}, "", False

    if name == "list_hooks":
        sk = app.cfg.get("sinks") or {}
        return {"toast": bool(sk.get("toast")), "sound": bool(sk.get("sound")),
                "browser": bool(sk.get("browser")),
                "quiet": f"{sk.get('quiet_from') or '—'} ~ {sk.get('quiet_to') or '—'}",
                "hooks": [{"name": h.get("name"), "enabled": bool(h.get("enabled")),
                           "method": h.get("method"), "url": WB_MASK if h.get("url") else "",
                           "verified": bool(h.get("verified"))} for h in sk.get("hooks") or []],
                "note": "转发地址是密址，不给模型看；界面上点「测试」过的那条 verified=true。"
                        "你没有改出口的工具"}, "", False

    if name == "recent_hits":
        lim = max(1, min(int(args.get("limit") or 20), 100))
        rows = app.db.q("""SELECT author,content,channel_name,matched,score,ts FROM messages
                           WHERE matched<>'' AND matched<>'0' ORDER BY ts DESC LIMIT ?""", (lim,))
        return {"count": len(rows),
                "hits": [{"author": r["author"], "channel": r["channel_name"],
                          "content": (r["content"] or "")[:300], "rules": r["matched"],
                          "score": r["score"], "when": ago_txt(r["ts"])} for r in rows],
                "note": "这是命中过规则的消息；想翻全部消息用 search_messages"}, "", False

    if name == "test_message":
        ev = {"source": "discord", "kind": args.get("kind") or "msg",
              "guild_id": str(args.get("guild_id") or ""), "parent_id": "",
              "channel_id": str(args.get("channel_id") or ""), "channel_name": "test",
              "is_thread": False, "author_id": str(args.get("author_id") or ""),
              "author": args.get("author") or "someone", "is_bot": bool(args.get("is_bot")),
              "content": args.get("content") or "", "is_dm": bool(args.get("is_dm")),
              "mentions_me": False, "ts": now(), "msg_id": "0"}
        out = []
        for r in rules:
            ok, why = app.match(r, ev)
            out.append({"id": str(r["id"]), "name": r.get("name"), "enabled": bool(r["enabled"]),
                        "match": ok, "why": "" if ok else NICE_WHY.get(why, why)})
        return {"rules": out,
                "note": "逐条试算的结果。算得中但 enabled=false 的那条不会提醒；"
                        "一条都没算中时，按 why 里说的卡点改条件"}, "", False

    if name == "get_logs":
        lim = max(1, min(int(args.get("limit") or 30), 100))
        rows = app.db.q("SELECT level,text,ts FROM logs ORDER BY id DESC LIMIT ?", (lim,))
        return {"count": len(rows),
                "logs": [{"level": r["level"], "text": (r["text"] or "")[:200],
                          "when": ago_txt(r["ts"])} for r in rows]}, "", False

    if name == "export_extract_templates":
        tpls = [tpl_for_export(t) for t in norm_tpls(app.cfg.get("extract_templates"))]
        return {"schema": EXTRACT_SCHEMA, "app": "dcwatch", "version": VERSION,
                "count": len(tpls), "extract_templates": tpls,
                "note": "批量提取页的模板包。你没有导入工具：导入要人在「批量提取」页点"
                        "「导入」、看过预览再确认"}, "", False

    raise ValueError(f"没有 {name} 这个工具")


# 不支持函数调用的模型走这条路：让它在正文里输出 ```dcwatch 块。
# 便宜的国产模型、本机 Ollama 里有不少不认 tools，不能因此就让工作台变回废物。
WB_TEXT_PROTO = """## 你可以直接动手（重要）
你的接口不支持函数调用，所以要动手时，在回答里单独输出一个代码块，格式严格如下：

```dcwatch
{"tool":"工具名","args":{...}}
```

一次只放一个块，块外可以写给用户看的话。程序会执行它，把结果发回给你，你再接着说。
**不要**把这个块当例子展示给用户看——你写出来它就会真的执行。

可用的工具（args 见说明）：
- list_rules {}：列出所有规则（含 id）。要改规则前必须先调它，别猜 id。
- update_rule {"id":"3","patch":{...}}：改一条规则，只写要改的字段。
- create_rule {"rule":{...},"enabled":true}：新建规则。
- set_rule_enabled {"id":"3","enabled":true}：启用/停用。
- delete_rule {"id":"3"}：删除（不可逆，用户没明说删就别用）。
- test_rule {"id":"3","content":"[图片]"}：试算会不会命中。
- list_channels {}：真实的频道 / 人 ID。
- search_messages {"query":"key","limit":20}：搜已收到的消息。
- get_status {}：现在的收信状况和自查结论。
- list_open_threads {}：程序自动开着哪些帖子标签页（只读，你不能关，只能建议）。
- list_providers {}：模型端点 / Key 填没填 / 默认模型 / 今天调了几次（只读）。
- list_hooks {}：通知开关和出口清单，转发地址打码（只读）。
- recent_hits {"limit":20}：最近命中过规则的消息（只读）。
- test_message {"content":"...","channel_id":"..."}：拿一条假消息对全部规则逐个试算，
  回答「这条为什么没提醒我」用它（只读）。
- get_logs {"limit":30}：最近的运行日志（只读）。
- export_extract_templates {}：批量提取的模板整包导出（只读，没有导入工具）。
- export_rules {"ids":["3"]}：把规则整包导出（ids 留空=全部），可以念给用户、或让他搬到另一台机器。
  **没有导入工具**：导入会覆盖他手填的规则，必须他自己在界面上点「导入规则」看过预览再确认。

""" + RULE_FIELDS_DOC


WB_TOOLS_HOWTO = """## 你可以直接动手（重要）
你有一组工具，能**真的**读写这个程序的配置：list_rules / update_rule / create_rule /
set_rule_enabled / delete_rule / test_rule / list_channels / search_messages / get_status /
export_rules / list_open_threads / list_providers / list_hooks / recent_hits / test_message /
get_logs / export_extract_templates。

用户说「帮我改一下这条规则」「让它连表情包也提醒」「把那条停掉」时，**直接调工具改完再回话**，
不要输出一二三步教他自己去点。他要是想自己点，他不会来问你。

纪律：
1. 改之前先 list_rules 拿到真实 id 和现在的条件，绝不凭名字猜 id。
2. 一次只改他要求的那部分，别顺手改别的。
3. 改完用 test_rule 试算一次，然后用一句话告诉他：改了什么、现在会不会命中。
4. 删除是不可逆的：他没明确说「删」，就用 set_rule_enabled 停用。
5. 需要频道 / 人的 ID 就调 list_channels，绝不编 ID。
6. 改完要提醒他：改动已经生效了，去「监听规则」页能看到。
7. 他要「备份 / 换台电脑 / 把规则发给朋友」就调 export_rules 把包念给他，并告诉他
   「监听规则」页上就有「导出规则」按钮能直接下载完整文件（包里的转发地址你看到的是打码的）。
   **你没有导入工具**：导入会覆盖他手填的规则，得他自己点「导入规则」，先看预览再确认。"""


# 用户在「模型接入」页把「允许模型直接改规则」关掉了。上面那两段都不发，
# 换成这段 —— 不然它会照着 ```dcwatch 的写法输出，程序又不执行，用户看着一团乱码。
WB_HANDS_OFF = """## 你这轮不能动手（用户自己关掉了）
用户在「模型接入」页把「允许模型直接改规则」这个勾**关掉**了，所以这一轮你只能说话，
不能改他的配置。要改规则时：讲清楚该改哪一栏、改成什么，并告诉他
「你把『模型接入』页的『允许模型直接改规则』打开，我就能自己动手了」。
不要输出 ```dcwatch 这种代码块 —— 现在没人执行它。"""


def wb_text_calls(text):
    """从正文里抠出 ```dcwatch 块。没有 tools 的模型靠这个动手。"""
    out = []
    for m in re.finditer(r"```dcwatch\s*(\{.*?\})\s*```", text or "", re.S):
        with contextlib.suppress(Exception):
            j = json.loads(m.group(1))
            if isinstance(j, dict) and j.get("tool"):
                out.append({"id": f"t{len(out)}", "type": "function",
                            "function": {"name": j["tool"],
                                         "arguments": json.dumps(j.get("args") or {}, ensure_ascii=False)}})
    return out


def strip_text_calls(text):
    return re.sub(r"```dcwatch\s*\{.*?\}\s*```", "", text or "", flags=re.S).strip()


READ_HUMAN = {"list_rules": "看了一遍你的规则", "list_channels": "查了真实的频道 ID",
              "search_messages": "翻了已经收到的消息", "get_status": "看了现在的收信状况",
              "test_rule": "拿一条假消息试算了一下", "export_rules": "把你的规则整包导了一份出来",
              "list_open_threads": "看了一遍现在开着哪些帖子",
              "list_providers": "看了一遍模型端点配置", "list_hooks": "看了一遍通知与转发配置",
              "recent_hits": "翻了最近命中过的消息", "test_message": "拿这条消息对全部规则逐个试算了",
              "get_logs": "翻了最近的运行日志", "export_extract_templates": "把提取模板整包导了一份出来"}


async def wb_run(app, prov, model, msgs, allow_ids, emit=None, stream=False, max_steps=6):
    """工作台的一轮对话：模型可以连着调工具，直到它不再想动手为止。
    返回 (最终回答, 干过的事, 有没有改到配置)。emit(kind, payload) 用来往界面上推流。
    act=False（用户把「允许模型直接改规则」关了）时一轮就走完，两条动手路径都不通。"""
    acts, changed, final, step = [], False, "", 0
    act = app.acting()

    async def send(kind, payload):
        if emit:
            await emit(kind, payload)

    while step < max_steps:
        step += 1
        use_tools = act and app.tools_ok(prov, model)
        try:
            if stream:
                msg, quiet = None, False
                async for kind, val in app.chat_stream(prov, model, msgs, max_tokens=1500,
                                                       rule="manual",
                                                       tools=WB_TOOLS if use_tools else None):
                    if kind != "delta":
                        msg = val
                        continue
                    # 文本指令模式下，```dcwatch 块是给程序看的，别让它在界面上刷出来
                    if act and not use_tools and ("```" in val or quiet):
                        quiet = True
                        continue
                    await send("delta", val)
                msg = msg or {"role": "assistant", "content": ""}
            else:
                msg = await app.chat(prov, model, msgs, max_tokens=1500, rule="manual",
                                     tools=WB_TOOLS if use_tools else None, want_msg=True)
        except ToolsUnsupported as e:
            app.mark_no_tools(prov, model, str(e))
            # 这句要进 acts，不能只走流式推送 —— 非流式那条路上用户否则完全看不见发生了什么
            note = {"tool": "note", "ok": True, "wrote": False, "err": "",
                    "human": "这个模型不支持函数调用，改用文本指令模式，功能一样"}
            acts.append(note)
            await send("act", note)
            msgs[0]["content"] = msgs[0]["content"].replace(app.sys_prompt("wb_tools"),
                                                             app.sys_prompt("wb_text"))
            step -= 1               # 这一轮不算数，重来一次
            continue
        text = msg.get("content") or ""
        calls = (msg.get("tool_calls") or []) if act else []
        if not calls and act and not use_tools:
            calls = wb_text_calls(text)
            if calls:
                text = strip_text_calls(text)
        if not calls:
            final = text
            break
        if use_tools:
            msgs.append({"role": "assistant", "content": text or None, "tool_calls": calls})
        else:
            msgs.append({"role": "assistant", "content": msg.get("content") or ""})
        results = []
        for c in calls[:4]:
            fn = (c.get("function") or {})
            nm = fn.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            try:
                data, human, wrote = await run_wb_tool(app, nm, args, allow_ids)
                ok, err = True, ""
            except Exception as e:
                data, human, wrote, ok, err = {"error": str(e)[:300]}, "", False, False, str(e)[:200]
            changed = changed or wrote
            act = {"tool": nm, "ok": ok, "wrote": wrote, "err": err,
                   "human": human or READ_HUMAN.get(nm, nm)}
            acts.append(act)
            await send("act", act)
            results.append((c.get("id") or nm, nm, data))
        if use_tools:
            for cid, nm, data in results:
                msgs.append({"role": "tool", "tool_call_id": cid, "name": nm,
                             "content": json.dumps(data, ensure_ascii=False)[:6000]})
        else:
            msgs.append({"role": "user", "content": "（程序执行结果，不是用户说的）\n" + "\n".join(
                f"{nm} → {json.dumps(data, ensure_ascii=False)[:3000]}" for _, nm, data in results)})
    else:
        final = final or "动作有点多，我先停一下。要不你看看现在的规则，再告诉我下一步？"
    return final, acts, changed


def can_send(app):
    """能不能往 Discord 发消息。旁听模式只能收——界面上就不该出现输入框。"""
    d = app.cfg.get("discord") or {}
    return bool(d.get("token")) and d.get("mode") in ("bot", "user")


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


# ---------- 骨架提示词：出厂值在这儿，用户可以在界面上覆盖（B3） ----------
# 以前这四条写死不让改，理由是「改坏了向导会失效」。用户要求「我得看得到内容、然后我可以改」，
# 所以改成：出厂值留在这里当兜底，cfg["sys_prompts"] 里有非空覆盖就用覆盖的，
# 界面上一键就能恢复出厂。改坏了最坏的结果是向导变笨，恢复默认即可 —— 不会丢数据。
BUILTIN_SYS = {
    "wizard": ("规则向导（会反问你的那个）", "WIZARD_SYS"),
    "compose": ("一句话直接出规则（老路径）", "COMPOSE_SYS"),
    "workbench": ("AI 工作台的身份与边界", "WORKBENCH_SYS"),
    "wb_tools": ("工作台的手·函数调用版", "WB_TOOLS_HOWTO"),
    "wb_text": ("工作台的手·文本指令版（模型不支持函数调用时）", "WB_TEXT_PROTO"),
    "wb_off": ("工作台的手·关掉之后", "WB_HANDS_OFF"),
}
BUILTIN_SYS_TEXT = {"wizard": WIZARD_SYS, "compose": COMPOSE_SYS, "workbench": WORKBENCH_SYS,
                    "wb_tools": WB_TOOLS_HOWTO, "wb_text": WB_TEXT_PROTO, "wb_off": WB_HANDS_OFF}

PRESET_SCHEMA = "dcwatch.preset/1"       # 认这个名字才当预设包；字段不兼容了就 /2
PRESET_DIR = BASE / "presets"            # 程序自带的预设包放这儿，用户能直接用记事本打开改


BROWSER_RANGE = {"max_tabs": (1, 20), "per_hour": (1, 60), "close_idle_min": (0, 1440)}


def norm_browser(raw):
    """洗「自动点开新帖」的设置。返回 (干净的 dict, 错误话术)；错误话术非空就别存。

    跟 norm_params 一个口径：越界**不静悄悄夹住**。用户以为自己设了「最多开 100 个」，
    实际被改成 20，行为跟他想的不一样又看不出原因，比直接报错糟。
    """
    if not isinstance(raw, dict):
        return {}, "browser 必须是对象"
    out = {}
    for k, v in raw.items():
        if k not in DEFAULT_CONFIG["browser"]:
            return {}, f"认不出的设置「{k}」"
        if k in ("auto_open", "only_rule_channels"):
            out[k] = bool(v)
            continue
        try:
            n = int(float(v))
        except Exception:
            return {}, f"「{k}」要填整数"
        lo, hi = BROWSER_RANGE[k]
        if not (lo <= n <= hi):
            return {}, f"「{k}」要在 {lo} ~ {hi} 之间，你填的是 {v}"
        out[k] = n
    return out, ""


def norm_params(raw):
    """校验采样参数。返回 (干净的 dict, 错误话术)；错误话术非空就别存。

    越界不静悄悄夹住 —— 用户以为自己设了 temperature=5，实际被改成 2，
    行为跟他想的不一样又找不到原因，比直接报错糟得多。
    """
    if not isinstance(raw, dict):
        return {}, "params 必须是对象"
    out = {}
    for k, v in raw.items():
        if k not in DEFAULT_PARAMS:
            return {}, f"认不出的参数「{k}」。要传别的就写进「附加参数」那一栏"
        if k == "extra":
            t = str(v or "").strip()
            if t:
                try:
                    if not isinstance(json.loads(t), dict):
                        return {}, "「附加参数」要是一个 JSON 对象，形如 {\"seed\":42}"
                except Exception as e:
                    return {}, f"「附加参数」不是合法 JSON（{e}）"
            out["extra"] = t
            continue
        if v is None or v == "":
            out[k] = None                     # 明确表示「不传这个字段」
            continue
        try:
            n = float(v)
        except Exception:
            return {}, f"「{k}」要填数字"
        lo, hi = PARAM_RANGE[k]
        if not (lo <= n <= hi):
            return {}, f"「{k}」要在 {lo} ~ {hi} 之间，你填的是 {v}"
        out[k] = int(n) if k == "max_tokens" else n
    return out, ""


def preset_pack(cfg, name=""):
    """把「模型怎么被指挥」这件事整包导出：骨架提示词 + 动作提示词 + 采样参数 + 后处理模式。

    不含 API Key、不含服务商地址、不含规则 —— 那些是这台机器的账，换机器必然不一样。
    """
    return {
        "schema": PRESET_SCHEMA, "app": "dcwatch", "version": VERSION,
        "name": name or f"我的预设 {time.strftime('%m-%d')}",
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sys_prompts": {k: (cfg.get("sys_prompts") or {}).get(k) or BUILTIN_SYS_TEXT[k]
                        for k in BUILTIN_SYS},
        "prompts": {k: (cfg.get("prompts") or {}).get(k) or DEFAULT_PROMPTS[k]
                    for k in DEFAULT_PROMPTS},
        "params": dict(DEFAULT_PARAMS, **{k: v for k, v in ((cfg.get("ai") or {})
                                                           .get("params") or {}).items()
                                          if k in DEFAULT_PARAMS}),
        "post": str((cfg.get("ai") or {}).get("post") or "off"),
    }


def diff_preset(cfg, pack):
    """预设包 vs 现在，逐项列出会变成什么。硬规矩 10：先给他看，再落库。"""
    ch = []
    for k, (label, _) in BUILTIN_SYS.items():
        new = str((pack.get("sys_prompts") or {}).get(k) or "").strip()
        if not new:
            continue
        old = ((cfg.get("sys_prompts") or {}).get(k) or BUILTIN_SYS_TEXT[k]).strip()
        if new != old:
            ch.append({"kind": "骨架提示词", "key": k, "label": label,
                       "old_len": len(old), "new_len": len(new),
                       "same_as_builtin": new == BUILTIN_SYS_TEXT[k].strip(),
                       "preview": new[:180]})
    for k in DEFAULT_PROMPTS:
        if k not in (pack.get("prompts") or {}):
            continue
        new = str(pack["prompts"][k] or "").strip()
        old = str((cfg.get("prompts") or {}).get(k) or DEFAULT_PROMPTS[k]).strip()
        if new != old:
            ch.append({"kind": "动作提示词", "key": k, "label": k,
                       "old_len": len(old), "new_len": len(new),
                       "same_as_builtin": new == DEFAULT_PROMPTS[k].strip(),
                       "preview": new[:180]})
    if isinstance(pack.get("params"), dict):
        cur = dict(DEFAULT_PARAMS, **{k: v for k, v in ((cfg.get("ai") or {}).get("params") or {}).items()
                                     if k in DEFAULT_PARAMS})
        for k, v in pack["params"].items():
            if k in DEFAULT_PARAMS and (v if v not in ("",) else None) != cur.get(k):
                ch.append({"kind": "参数", "key": k, "label": k,
                           "old_len": 0, "new_len": 0, "same_as_builtin": False,
                           "preview": f"{cur.get(k)} → {v}"})
    if pack.get("post") and str(pack["post"]) in AI_POST and \
            str(pack["post"]) != str((cfg.get("ai") or {}).get("post") or "off"):
        ch.append({"kind": "后处理", "key": "post", "label": "提示词后处理",
                   "old_len": 0, "new_len": 0, "same_as_builtin": False,
                   "preview": f"{(cfg.get('ai') or {}).get('post') or 'off'} → {pack['post']}"})
    return ch


def post_process(msgs, mode):
    """提示词后处理。抄酒馆（SillyTavern）那个「提示词后处理」下拉：用户实测调严之后
    模型听话很多。原理不神秘 —— 很多兼容接口对 messages 的形状有隐含要求
    （system 只能一条且在最前、user/assistant 必须交替、最后一条得是 user），
    形状不对时模型的注意力就散，输出格式跟着崩。这里把形状规整掉。

    mode: off / merge / strict（见 AI_POST）。不认识的值按 off 处理。
    只动 role+content 的形状，一个字都不改内容。
    """
    if mode not in ("merge", "strict") or not msgs:
        return msgs
    sys_txt = "\n\n".join(str(m.get("content") or "").strip()
                          for m in msgs if m.get("role") == "system" and m.get("content"))
    rest = [dict(m) for m in msgs if m.get("role") != "system"]
    # 带 tool_calls / tool 结果的轮次不能合并（合了函数调用就废了），原样让它过
    if any(m.get("role") == "tool" or m.get("tool_calls") for m in rest):
        return msgs
    out = []
    for m in rest:
        if out and out[-1]["role"] == m.get("role"):
            out[-1]["content"] = str(out[-1]["content"]) + "\n\n" + str(m.get("content") or "")
        else:
            out.append({"role": m.get("role") or "user", "content": str(m.get("content") or "")})
    while out and out[0]["role"] == "assistant":
        out.pop(0)                       # 开头不能是 assistant
    if not out or out[-1]["role"] != "user":
        out.append({"role": "user", "content": "请按上面的格式回答。"})
    if mode == "strict":
        if sys_txt:
            out[0]["content"] = sys_txt + "\n\n---\n\n" + out[0]["content"]
        return out
    return ([{"role": "system", "content": sys_txt}] if sys_txt else []) + out


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


class ToolsUnsupported(RuntimeError):
    """这个接口不认 tools 字段。工作台会自动退回「文本指令」模式，不该让用户看见报错。"""


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
        self._gate_n = 0              # A7 收信闸拦下的条数（诊断包要印出来）
        self.started = now()          # 用于诊断包里的「已跑多久」
        self.bridges = {}             # 每个装了扩展的浏览器 = 一个桥，独立跟踪
        self.hb_seen = {}             # 来 /api/ext/hb 要过指令的桥：bid → 最后一次来的时间
        self._inflight = set()        # 正在处理的 msg_id，防两个桥同时报同一条
        self._last_toast = 0.0        # 系统通知防刷屏
        # 本机提醒合并：涌进来一批消息时攒成一条通知 + 一次提示音。
        # 以前提示音一条一条播（PlaySound 是异步的），十几条叠在一起就是一片电音；
        # 通知那边则是 4 秒内直接丢掉多余的，等于漏消息。两个都不对。
        self._pend = []               # [(head, body)] 等着合并发出去的
        self._pend_task = None
        self._pend_sound = False
        self.no_tools = set()         # 试过一次不支持函数调用的 provider|model，别反复白试
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
        # 页面里的旧脚本直连（扩展重载过但那个 Discord 标签页没按 F5）。
        # 它还能转发消息，看着像在工作，所以必须显式记下来，否则没人能看出问题。
        br["stale_ctx"] = bool(b.get("stale_ctx"))
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

    # ---------- 自动点开新帖（C2） ----------
    # 背景（用户问对了）：浏览器旁听是「读网页 DOM」，Discord 只把**点开过的**频道/帖子
    # 渲染进 DOM。所以论坛里的新帖，不点开就一条帖内消息都读不到 —— 现在能看到的
    # 「有人开新帖」只是列表页上的标题。想读正文只有两条路：用户自己点开挂着，
    # 或者程序替他点开（就是这一块）。决策全在服务端：扩展只执行 + 回报，它不知道规则在盯什么。
    MAX_ORDERS = 3          # 一次最多给 3 条 open / close 指令（一次开 20 个标签页就是脚本行为）

    def br_cfg(self):
        c = dict(DEFAULT_CONFIG["browser"])
        c.update({k: v for k, v in (self.cfg.get("browser") or {}).items() if k in c})
        return c

    def rule_parents(self):
        """所有**启用**规则盯着的频道 ID（第 3 层频道 + 第 4 层帖子都算）。
        only_rule_channels 开着时，只有落在这里面的新帖才值得自动点开。"""
        out = set()
        for r in self.rules():
            for k in ("channel_ids", "thread_ids"):
                for x in r.get(k) or []:
                    out.add(str(x))
        return out

    def queue_thread(self, ev):
        """「有人开新帖」这条路径顺手排一次队。不开自动开帖就什么都不做。

        只排队，不在这里开 —— 开标签页得等扩展下一次心跳来拿指令（它才是有手的那个）。
        """
        c = self.br_cfg()
        if not c["auto_open"]:
            return False
        tid = str(ev.get("channel_id") or "")
        if not tid.isdigit():
            return False
        parent = str(ev.get("parent_id") or "")
        if c["only_rule_channels"]:
            keep = self.rule_parents()
            if not (parent in keep or tid in keep):
                return False
        if self.db.q("SELECT 1 FROM threads_open WHERE tid=? LIMIT 1", (tid,)):
            return False        # 幂等：同一个帖子只排一次（哪怕两个桥各报了一次）
        gid = str(ev.get("guild_id") or "")
        self.db.x("""INSERT INTO threads_open(tid,url,name,parent_id,guild_id,wanted,tries)
                     VALUES(?,?,?,?,?,?,0)""",
                  (tid, ev.get("url") or f"https://discord.com/channels/{gid or '@me'}/{tid}",
                   (ev.get("channel_name") or tid)[:120], parent, gid, now()))
        self.log("info", f"新帖「{(ev.get('channel_name') or tid)[:40]}」排进自动打开队列")
        return True

    def thread_last(self, tid, fallback=0.0):
        """这个帖子最后一条消息的时间。没有消息就用兜底值（一般是 opened_at）。
        判断「很久没人说话」用 SQL 就够了，不需要模型（PLAN_C2 第 1 节：模型每 30 秒判一次是纯烧钱）。"""
        r = self.db.q("SELECT MAX(ts) t FROM messages WHERE channel_id=? AND kind!='thread'", (str(tid),))
        return float((r and r[0]["t"]) or 0) or float(fallback or 0)

    def open_rows(self):
        return self.db.q("SELECT * FROM threads_open WHERE opened_at IS NOT NULL AND closed_at IS NULL")

    def lead_bridge(self):
        """多浏览器时谁负责执行指令。取「最近 90 秒来要过指令的桥里 id 最小的那个」——
        必须是确定性的：按「最后活跃」选会来回抖，两个浏览器就会各开一遍同一个帖子。
        候选人只看调过 /api/ext/hb 的桥，不能看所有 touch 过的：只 ingest 从不来要
        指令的幻影桥（没填 bridge 的 "anon"、扩展重载后没按 F5 的 "page-direct" 旧页面）
        一旦当选，指令发给了不存在的手，自动开帖就静默死掉（e2e_tabs 第 6 节钉着）。"""
        live = sorted(b for b, t in self.hb_seen.items() if now() - t < 90)
        return live[0] if live else ""

    def tab_orders(self, bridge=""):
        """给扩展的指令。纯逻辑、可单测（e2e_tabs.py 把闸门一条条打过）。"""
        c = self.br_cfg()
        opened = self.open_rows()
        hour = self.db.q("SELECT COUNT(*) n FROM threads_open WHERE opened_at>?", (now() - 3600,))[0]["n"]
        out = {"open": [], "close": [], "limits": dict(
            c, open_now=len(opened), opened_this_hour=hour,
            open_left_this_hour=max(0, c["per_hour"] - hour),
            tabs_left=max(0, c["max_tabs"] - len(opened)))}
        if not c["auto_open"]:
            return out
        lead = self.lead_bridge()
        mine = (not bridge) or (not lead) or bridge == lead
        room = min(c["max_tabs"] - len(opened), c["per_hour"] - hour, self.MAX_ORDERS)
        if mine and room > 0:
            for r in self.db.q("""SELECT * FROM threads_open
                                  WHERE opened_at IS NULL AND closed_at IS NULL AND tries<2
                                  ORDER BY wanted ASC LIMIT ?""", (room,)):
                out["open"].append({"tid": r["tid"], "url": r["url"],
                                    "why": f"新帖：{r['name'] or r['tid']}"})
        if c["close_idle_min"] > 0:
            cut = now() - c["close_idle_min"] * 60
            for r in opened:
                if len(out["close"]) >= self.MAX_ORDERS:
                    break
                if (r["opened_at"] or 0) > now() - 300:
                    continue          # 刚开的不关，否则开了就关来回抖
                if r["bridge"] and bridge and r["bridge"] != bridge and r["bridge"] in \
                        [b["id"] for b in self.bridge_list() if b.get("fresh")]:
                    continue          # 是别的浏览器开的，而那个浏览器还活着，让它自己关
                last = self.thread_last(r["tid"], r["opened_at"])
                if last < cut:
                    out["close"].append({"tid": r["tid"],
                                         "why": f"闲置 {int((now() - last) / 60)} 分钟"})
        return out

    def tabs_report(self, b):
        """扩展回报「我开了/关了哪些」。**服务端不猜**——用户手动关掉标签页服务端不知道，
        所以「现在到底开着哪些」这个事实只认扩展的回报（PLAN_C2 第 2 节）。"""
        bid = str(b.get("bridge") or "")[:64]
        n_open = n_close = n_fail = 0
        for tid in [str(x) for x in (b.get("opened") or [])][:20]:
            self.db.x("""UPDATE threads_open SET opened_at=?, closed_at=NULL, err='', bridge=?
                         WHERE tid=? AND opened_at IS NULL""", (now(), bid, tid))
            r = self.db.q("SELECT name FROM threads_open WHERE tid=?", (tid,))
            self.log("info", f"程序替你点开了帖子「{(r[0]['name'] if r else tid)[:40]}」")
            n_open += 1
        for tid in [str(x) for x in (b.get("closed") or [])][:20]:
            self.db.x("UPDATE threads_open SET closed_at=? WHERE tid=? AND closed_at IS NULL",
                      (now(), tid))
            r = self.db.q("SELECT name FROM threads_open WHERE tid=?", (tid,))
            self.log("info", f"关掉了闲置的帖子标签页「{(r[0]['name'] if r else tid)[:40]}」")
            n_close += 1
        for f in (b.get("failed") or [])[:20]:
            if not isinstance(f, dict):
                continue
            tid, err = str(f.get("tid") or ""), str(f.get("err") or "?")[:200]
            if not tid:
                continue
            if f.get("gone"):
                # 「要关的那个标签页找不到了」= 用户自己关了。这不算失败，是事实同步
                self.db.x("UPDATE threads_open SET closed_at=? WHERE tid=? AND closed_at IS NULL",
                          (now(), tid))
                continue
            self.db.x("UPDATE threads_open SET tries=tries+1, err=? WHERE tid=?", (err, tid))
            n_fail += 1
            row = self.db.q("SELECT name,tries FROM threads_open WHERE tid=?", (tid,))
            if row and (row[0]["tries"] or 0) >= 2:
                self.log("warn", f"帖子「{(row[0]['name'] or tid)[:40]}」自动打开失败两次了"
                                 f"（{err[:60]}），你手动点一下这个帖子就行")
        return {"opened": n_open, "closed": n_close, "failed": n_fail}

    # ---------- 开服监听（D3） ----------
    # 跟 Discord 无关：盯一个网址「开了没有」（服务关服维护、登记没开放之类）。
    # 定时探；状态由「关」翻「开」才提醒（提醒走跟规则命中同一条管线：本机弹窗 +
    # 提示音 + 全部转发出口），由「开」翻「关」默认不提醒（怕吵，单个目标可开）。
    WATCH_MIN_EVERY = 15        # 再快就是打人家网站了，界面上也按这个下限说

    def watch_list(self):
        return self.db.q("SELECT * FROM watch ORDER BY id")

    def watch_save(self, b):
        """表单进来的字段收拾干净。带 id 是更新（没带的字段沿用旧的），不带是新建。"""
        w = {"name": "", "url": "", "every_sec": 60, "expect": "", "absent": "",
             "enabled": 1, "notify_open": 1, "notify_close": 0}
        wid = int(b.pop("id", 0) or 0)
        if wid:
            old = self.db.q("SELECT * FROM watch WHERE id=?", (wid,))
            if not old:
                return None, "这条监视已经不在了（刚删掉？）"
            w.update({k: old[0][k] for k in w})
        for k, lim in (("name", 60), ("url", 300), ("expect", 100), ("absent", 100)):
            if k in b:
                w[k] = str(b.get(k) or "").strip()[:lim]
        for k in ("enabled", "notify_open", "notify_close"):
            if k in b:
                w[k] = int(bool(b[k]))
        if "every_sec" in b:
            with contextlib.suppress(Exception):
                w["every_sec"] = max(self.WATCH_MIN_EVERY, min(86400, int(b["every_sec"])))
        if not w["url"].lower().startswith(("http://", "https://")):
            return None, "网址要以 http:// 或 https:// 开头"
        if not w["name"]:
            # 名字留空就用域名凑一个 —— 列表里总得有东西可看
            w["name"] = urllib.parse.urlparse(w["url"]).netloc or w["url"][:40]
        if wid:
            self.db.x("""UPDATE watch SET name=?,url=?,every_sec=?,expect=?,absent=?,
                         enabled=?,notify_open=?,notify_close=? WHERE id=?""",
                      (w["name"], w["url"], w["every_sec"], w["expect"], w["absent"],
                       w["enabled"], w["notify_open"], w["notify_close"], wid))
        else:
            wid = self.db.x("""INSERT INTO watch(name,url,every_sec,expect,absent,
                               enabled,notify_open,notify_close) VALUES(?,?,?,?,?,?,?,?)""",
                            (w["name"], w["url"], w["every_sec"], w["expect"], w["absent"],
                             w["enabled"], w["notify_open"], w["notify_close"]))
        row = self.db.q("SELECT * FROM watch WHERE id=?", (wid,))
        return (row[0] if row else None), ""

    async def watch_probe(self, w):
        """探一次，只发请求不写库。返回 (state, http_status, err)。"""
        try:
            async with self.http.get(w["url"], timeout=aiohttp.ClientTimeout(total=12),
                                     **self.rq()) as r:
                st = r.status
                body = await r.text(errors="replace")
        except Exception as e:
            return "closed", 0, str(e)[:150]
        if w.get("absent") and w["absent"] in body:
            return "closed", st, ""
        if w.get("expect"):
            return ("open" if w["expect"] in body else "closed"), st, ""
        return ("open" if st < 400 else "closed"), st, ""

    async def watch_run(self, w):
        """探一次 + 落库 + 状态翻转时提醒。/api/watch/{id}/check 和后台巡检共用这一条路。"""
        state, st, err = await self.watch_probe(w)
        old = w.get("state") or "unknown"
        t, changed = now(), old != state
        self.db.x("""UPDATE watch SET state=?,last_check=?,last_status=?,err=?,
                     last_change=CASE WHEN ?=1 THEN ? ELSE last_change END WHERE id=?""",
                  (state, t, st, err, int(changed), t, w["id"]))
        if old == "unknown":
            self.log("info", f"开服监听「{w['name']}」探了一次：{'开着' if state == 'open' else '没开'}"
                             f"（{'HTTP ' + str(st) if st else err or '连不上'}）。"
                             f"第一次只落状态，翻了脸才提醒")
            return self.db.q("SELECT * FROM watch WHERE id=?", (w["id"],))[0]
        if changed:
            if state == "open" and w["notify_open"]:
                await self.notify_watch(w, True, st, err)
            elif state == "closed" and w["notify_close"]:
                await self.notify_watch(w, False, st, err)
            else:
                self.log("info", f"开服监听「{w['name']}」{old}→{state}，"
                                 f"但这个方向的提醒没开，只记一笔")
        return self.db.q("SELECT * FROM watch WHERE id=?", (w["id"],))[0]

    async def notify_watch(self, w, opened, st, err):
        """开/关翻转的统一出口。出口挂一个记一个日志，不耽误别的出口。"""
        s = self.cfg["sinks"]
        head = (f"🟢 {w['name']} 开了！" if opened else f"🔴 {w['name']} 关了")
        body = w["url"] + (f"（HTTP {st}）" if st else (f"（{err}）" if err else ""))
        self.log("warn" if opened else "info", f"开服监听：{head} {body}")
        if not self.in_quiet_hours():
            self.queue_local(head, body[:180], sound=bool(s.get("sound")),
                             toast=bool(s.get("toast")))
        text = f"{head}\n{body}"
        vals = {"extracted": "", "need_human": "", "text": text, "title": head, "body": body,
                "content": body, "author": "dcwatch 开服监听", "channel": "开服监听",
                "server": "", "url": w["url"], "score": "", "tags": "", "todo": "",
                "json": json.dumps({"watch": {"id": w["id"], "name": w["name"], "url": w["url"],
                                              "state": "open" if opened else "closed",
                                              "http_status": st, "err": err}},
                                   ensure_ascii=False)}
        jobs = {}
        for i, h in enumerate(s.get("hooks") or []):
            if h.get("enabled", True) and h.get("url"):
                jobs[h.get("name") or f"出口{i + 1}"] = self.push_hook(h, vals)
        for name, res in zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)):
            if isinstance(res, Exception):
                self.log("error", f"开服监听 {name} 发送失败: {res}")

    def findings(self):
        """一眼结论：把「为什么它没提醒我」这个问题的常见答案自动查一遍。

        诊断包第 [0] 段和界面「收信箱」顶部都用它。这是刻意加的：一份诊断包有几百行，
        真正的原因往往是「一条规则都没建」或者「扩展重载了但页面没按 F5」这种一句话的事，
        不该指望用户（或者看诊断的人）拿肉眼在几百行里找线索。
        每条结论 = 现象 + 该做什么，不写「可能有问题」这种没用的话。"""
        out = []
        add = lambda lv, what, why: out.append({"level": lv, "what": what, "why": why})
        brs = self.bridge_list()
        live = [b for b in brs if b["fresh"]]
        stale = [b for b in live if b.get("stale_ctx")]
        real = [b for b in live if not b.get("stale_ctx")]
        mode = self.cfg["discord"].get("mode", "browser")
        rules = self.rules(False)
        on_rules = [x for x in rules if x.get("enabled")]
        n_msg = (self.db.q("SELECT COUNT(*) c FROM messages") or [{"c": 0}])[0]["c"]
        n_hit = (self.db.q("SELECT COUNT(*) c FROM messages WHERE matched<>'' AND matched<>'0'")
                 or [{"c": 0}])[0]["c"]

        # ---- 消息进不进得来 ----
        if mode == "browser":
            if not self.cfg["sources"].get("browser", True):
                add("bad", "浏览器旁听的开关是关的", "左下角把「浏览器旁听」打开，否则扩展送来的消息全被丢掉。")
            elif not brs:
                add("bad", "没有任何浏览器在旁听",
                    "扩展没装上，或者装完没在 Discord 页面按 F5 —— 内容脚本只在页面加载时注入。")
            elif not live:
                add("bad", f"扩展装过，但最近 90 秒没有心跳（最后一次 {int(brs[0]['ago'] / 60)} 分钟前）",
                    "Discord 标签页关了/睡了，或扩展被停用。打开 Discord 按 F5 就恢复。")
            if stale and not real:
                add("bad", "这个 Discord 页面里跑的是旧脚本",
                    "扩展重载/更新过，但那个标签页没刷新。它还能直连转发，所以看着像在工作，"
                    "实际上扩展的改动全都没生效。到 Discord 标签页按一次 F5。")
            elif stale:
                add("warn", "有一个 Discord 标签页需要按 F5",
                    "那个页面还连着旧脚本（扩展重载过）。按 F5 后这条就没了。")
            for b in real:
                if b.get("ver") and cmp_ver(b["ver"], EXT_MIN) < 0:
                    add("warn", f"扩展是旧版 v{b['ver']}（程序要求 v{EXT_MIN}）",
                        "chrome://extensions 点这个扩展卡片上的刷新箭头，再回 Discord 按 F5。"
                        + ("你还开着「自动点开新帖」——那个功能全靠新版扩展执行指令，"
                           "旧版根本不认，所以现在一个帖子都不会被打开。"
                           if self.br_cfg()["auto_open"] else ""))
                    break
        elif self.dc and self.dc.state != "online":
            add("bad", f"Discord 直连状态是 {self.dc.state}",
                "Token 模式下这里必须是 online，看下面 [2] 段的报错。")

        # ---- 扩展看见了却没上报 ----
        parsed = sent = 0
        skips = {}
        # 同一个页面可能同时以「后台脚本」和「页面直连」两个身份出现（扩展重载过），
        # 两边的 stats 是同一份，加起来会翻倍 —— 有正常桥时就只看正常桥
        for b in (real or live):
            st = b.get("stats") or {}
            parsed += st.get("parsed", 0) or 0
            sent += st.get("sent", 0) or 0
            for k, v in (st.get("skip") or {}).items():
                skips[k] = skips.get(k, 0) + (v or 0)
        if parsed and not sent:
            why = "、".join(f"{NICE_SKIP.get(k, k)} {v} 条" for k, v in skips.items() if v)
            add("warn", f"扩展解析到 {parsed} 条，但一条都没上报",
                (f"跳过原因：{why}。" if why else "")
                + "「整批渲染」和「历史消息」都是故意跳的（切频道、往上滚不该刷屏）；"
                  "要把已经发过的内容弄进来，用药丸面板里的「抓历史」。"
                  "如果你确认刚刚有人发了新消息却没上报，把这份诊断发出去。")
        if live and not n_msg:
            add("warn", "库里一条消息都没有",
                (f"启动以来有 {self._gate_n} 条消息被收信闸拦下了：它们所在的地方没有任何启用的规则在盯"
                 "（现在没写规则就是不收信）。想收哪个频道，去「监听规则」建一条框住它；"
                 "要是那个频道本来就安静，自己发一条测试最快。" if self._gate_n else
                 "从程序启动到现在，你正在看的那个频道可能就是没有新消息 —— 自己发一条测试最快。"))

        # ---- 有消息之后，规则和出口 ----
        if not rules:
            add("bad", "一条规则都没有",
                "没有规则 = 消息进来了也不会有任何提醒。到「监听规则」点「◆ 帮我建一条」，"
                "让它问你几句就能生成。")
        elif not on_rules:
            add("bad", f"{len(rules)} 条规则全是停用状态", "把要用的那条打开。")
        elif all("msg" not in (x.get("kinds") or ["msg"]) for x in on_rules):
            # 「开新帖」和「有人说话」是两回事。规则里第 0 项勾成了只听新帖，
            # 那么频道里聊翻天也一条都不会响 —— 这是最容易填错、又最看不出来的一格。
            names = "、".join(x.get("name") or "(没名字)" for x in on_rules[:3])
            off_msg = [x for x in rules if not x.get("enabled") and "msg" in (x.get("kinds") or ["msg"])]
            add("bad", f"开着的规则（{names}）只在「有人开新帖」时触发，普通聊天消息一条都不会命中",
                "到「监听规则」打开那条规则，第 0 项「什么时候触发」把「有人发消息」也勾上。"
                + (f"（你还有 {len(off_msg)} 条听消息的规则是停用状态，"
                   f"比如「{off_msg[0].get('name') or '(没名字)'}」，直接打开它也行）" if off_msg else ""))
        elif n_msg and not n_hit:
            add("warn", f"收到过 {n_msg} 条消息，一条都没命中规则",
                "多半是条件太窄（频道 ID 填错、关键词太长、忘了勾「包含私信」）。"
                "到「监听规则」用「试算」把真实消息贴进去，它会告诉你卡在哪一条。")
        sk = self.cfg.get("sinks", {})
        hooks_on = [h for h in sk.get("hooks", []) if h.get("enabled")]
        if on_rules and not sk.get("toast") and not sk.get("sound") and not hooks_on:
            add("warn", "命中了也不会有动静", "弹窗和提示音都关着，也没有开着的转发出口。")
        errs = self.db.q("SELECT COUNT(*) c FROM logs WHERE level='error' AND ts>?", (now() - 3600,))
        if errs and errs[0]["c"]:
            add("warn", f"最近一小时有 {errs[0]['c']} 条报错", "看下面 [6] 段的日志。")

        if not out:
            add("ok", "没查出明显问题",
                f"在旁听 {len(real)} 个浏览器，{len(on_rules)} 条规则开着，库里 {n_msg} 条消息、"
                f"命中 {n_hit} 条。")
        # 致命的排前面。界面顶部就那么几行，「规则永远不会命中」不该排在
        # 「这个频道最近没人说话」后面 —— 用户只会读第一条。
        out.sort(key=lambda x: {"bad": 0, "warn": 1}.get(x["level"], 2))
        return out

    def dry_run_samples(self, limit=6):
        """拿真实见过的消息，逐条对每一条规则跑一遍 match()，报出卡在哪一步。

        为什么要有这个：诊断包能告诉你「规则填了什么」，但填了什么和会不会命中是两回事 ——
        频道 ID 填进了「子区」那一栏、第 0 项勾成只听新帖、忘了勾私信，看条件本身全都很像对的。
        所以直接用真消息跑一遍，把「卡在哪个输入框」印出来，别让人靠脑补。

        样本来源两个，优先用库里的真消息；库是空的时候（消息一条都没进来，恰恰是最需要
        排查的时候）退回用扩展心跳送来的 recent —— 那里有真实正文（前 40 字）和真实频道路径。"""
        rules = self.rules(False)
        if not rules:
            return []
        out = []
        for m in self.db.q("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)):
            # 库里没有 mentions_me 这一列（存的时候就没留），所以「只在@我」的规则在这里一律算不中，
            # 下面会单独注一句，免得看的人以为规则坏了
            out.append({"text": m.get("content") or "", "from": "库里的消息", "cut": False,
                        "ev": {"source": m.get("source") or "discord", "guild_id": m.get("guild_id") or "",
                               "channel_id": m.get("channel_id") or "", "parent_id": m.get("parent_id") or "",
                               "channel_name": m.get("channel_name") or "",
                               "is_thread": bool(m.get("is_thread")),
                               "author_id": m.get("author_id") or "", "author": m.get("author") or "",
                               "is_bot": bool(m.get("is_bot")), "content": m.get("content") or "",
                               "is_dm": not (m.get("guild_id") or ""), "mentions_me": False,
                               "ts": m.get("ts") or now(), "msg_id": m.get("msg_id") or "0",
                               "kind": m.get("kind") or "msg"}})
        if not out:
            seen_txt = set()
            for b in self.bridge_list():
                st = b.get("stats") or {}
                url = st.get("url") or ""
                mt = re.search(r"/channels/(@me|\d+)/(\d+)", url)
                gid = "" if not mt or mt.group(1) == "@me" else mt.group(1)
                cid = mt.group(2) if mt else ""
                for rc in (st.get("recent") or [])[:limit]:
                    txt = (rc.get("what") or "").strip()
                    if not txt or txt in seen_txt:
                        continue
                    seen_txt.add(txt)
                    out.append({"text": txt, "cut": True,
                                "from": f"扩展在 {url or '页面'} 上看到的（正文只留了前 40 字）",
                                "ev": {"source": "discord", "guild_id": gid, "channel_id": cid,
                                       "parent_id": "", "channel_name": b.get("where") or "",
                                       "is_thread": False, "author_id": "", "author": "",
                                       "is_bot": False, "content": txt, "is_dm": not gid,
                                       "mentions_me": False, "ts": now(), "msg_id": "0", "kind": "msg"}})
                if len(out) >= limit:
                    break
        for s in out[:limit]:
            rows = []
            for j in rules:
                ok, why = self.match(j, s["ev"])
                rows.append({"name": j.get("name") or "(没名字)", "on": bool(j.get("enabled")),
                             "ok": ok, "why": NICE_WHY.get(why, why)})
            s["rows"] = rows
        return out[:limit]

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
        # 开服监听（D3）也要报进实况：模型被问「能盯服务开没开吗」时，
        # 光有功能表还不够，最好能直接说出他现在盯着哪几个。
        ws = self.watch_list()
        if ws:
            L.append("他在「开服监听」里盯着 " + str(len(ws)) + " 个网址目标：" + "；".join(
                f"{w['name']}（{'开着' if w.get('state') == 'open' else ('没开' if w.get('state') == 'closed' else '还没探过')}"
                f"{'，已停用' if not w.get('enabled') else ''}）" for w in ws[:8])
                + "。要加新目标或改这些，得他自己去侧栏「开服监听」页——你只能指路。")
        else:
            L.append("「开服监听」里一个目标都没加。"
                     "他要是想知道某个服务/服务器开没开、开放登记没有，就是用这一页（可以加很多个），"
                     "不是写规则。")
        L.append("自动点开新帖：" + ("开着" if self.br_cfg().get("auto_open") else "关着（默认）")
                 + "。论坛新帖不点开是读不到帖内消息的，这个开关在「设置」页。")
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
        # 注意顺序："/models" 必须在 "/model" 前面，否则 ".../models" 会被剥成 ".../s"
        for tail in ("/chat/completions", "/completions", "/models", "/model"):
            if b.endswith(tail):
                b = b[: -len(tail)].rstrip("/")
        out = [b]
        if not b.endswith("/v1"):
            out.append(b + "/v1")
        return [x for x in out if x]

    async def list_models(self, base_url, api_key):
        # Key 是空的又对着外地地址，别放空枪 —— 直接说清楚。（A6：他机器上诊断包「Key 空」+ 401 就是这么来的）
        looks_local = any(h in (base_url or "") for h in ("127.0.0.1", "localhost", "0.0.0.0", "ollama"))
        if not api_key and not looks_local:
            raise RuntimeError("API Key 是空的：在这家服务的卡片里把 Key 粘进去，先按「保存服务商」，"
                               "再点「⇣ 拉取模型列表」。（本机 Ollama 这类才不需要 Key）")
        hdr = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        tried = []
        self.models_rescued = ""      # 这次是不是靠兜底救回来的（界面上要说一句）
        # 两轮：先要求不压缩（identity），躲开压缩体解不开那一类；再走默认。
        for enc in ("identity", None):
            h = dict(hdr)
            if enc:
                h["Accept-Encoding"] = enc
            for base in self.base_candidates(base_url):
                tag = f"{base}/models{'（不压缩）' if enc else ''}"
                try:
                    async with self.http.get(f"{base}/models", headers=h,
                                             timeout=aiohttp.ClientTimeout(total=25), **self.rq()) as r:
                        raw, cut = await read_tolerant(r)
                        if r.status == 200:
                            ids, how = [], ""
                            try:
                                j = loads_loose(raw)
                                arr = j.get("data", j) if isinstance(j, dict) else j
                                ids = [m.get("id") if isinstance(m, dict) else m
                                       for m in (arr if isinstance(arr, list) else [])]
                                ids = [i for i in ids if isinstance(i, str) and i]
                                if cut:
                                    how = "对方把响应发到一半就断了，已按收到的部分解析"
                            except Exception:
                                ids = ids_from_text(raw)       # 连 JSON 都拼不回来
                                if ids:
                                    how = "对方返回的 JSON 不完整，已从原始数据里把模型名捞出来"
                            if ids:
                                self.models_base = base
                                self.models_rescued = how
                                if how:
                                    self.log("WARN", "拉模型兜底生效：%s（%s，拿到 %d 个）"
                                             % (how, base, len(ids)))
                                return sorted(set(ids))
                            tried.append(f"{tag} -> HTTP 200 但解不出模型名 {raw[:80]!r}")
                        else:
                            tried.append(f"{tag} -> HTTP {r.status} "
                                         f"{raw[:120].decode('utf-8', 'replace')}")
                except Exception as e:
                    tried.append(f"{tag} -> {type(e).__name__}: {str(e)[:120]}")
            if self.base_candidates(base_url) and any("HTTP 401" in t or "HTTP 403" in t
                                                      for t in tried):
                break                          # Key 不对，换编码也没意义，别白试第二轮
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
            from urllib.parse import urlparse as _up
            _host = _up(base_url if "://" in base_url else "https://" + base_url).netloc or base_url
            hint = (f"。连不上 {_host}：TCP 连接根本没建立起来。"
                    + (f"你在「设置」里填了代理（{px}），但走这个代理还是连不上——换个节点或检查代理软件。"
                       if px else
                       "如果你开着代理软件（TUN/全局）它还是连不上，多半是 TUN 抓不到 python.exe"
                       "——不少加速器只接管浏览器和游戏，不接管这种命令行程序，浏览器能通≠它能通。"
                       "最稳的办法：去「设置」直接填代理软件的本地端口"
                       "（Clash 一般是 http://127.0.0.1:7890 或 7897，v2rayN 一般是 http://127.0.0.1:10809），"
                       "填完再拉一次；也可以换 DeepSeek / 通义 / 本机 Ollama 这类能直连的"))
        elif any("HTTP 401" in t or "HTTP 403" in t for t in tried):
            hint = "。看着像 Key 不对或没权限"
        elif any("HTTP 404" in t for t in tried):
            hint = "。404 一般是 Base URL 写法不对，正确的形如 https://api.deepseek.com/v1"
        elif any("ClientPayloadError" in t or "Payload" in t or "解不出模型名" in t
                 or "不是完整 JSON" in t for t in tried):
            hint = ("。对方返回的响应体不规范（长度对不上或压缩编码不标准），"
                    "两种兜底读法都没救回来。这不是你机器的问题，先确认这家中转站的 "
                    "Base URL 和 Key 没写错；不行就先手填一个模型名，"
                    "拉不到列表不影响正常调用")
        raise RuntimeError(f"拉取模型失败{hint}\n试过：{body}")

    def chat_prep(self, provider_name, model, messages, json_mode=False, max_tokens=800,
                  tools=None, stream=False):
        """一次 chat 请求的公共部分。非流式和流式共用，免得两边的代理/base 修正走岔。"""
        p = self.provider(provider_name) or {}
        base = (p.get("base_url") or "").rstrip("/")
        if not base or not model:
            raise RuntimeError("未配置 provider/model")
        cap = self.cfg.get("ai_daily_call_cap", 500)
        used = self.db.q("SELECT COUNT(*) n FROM aiusage WHERE ts>?", (now() - 86400,))[0]["n"]
        if used >= cap:
            raise RuntimeError(f"已达今日调用上限 {cap}")
        pr = self.ai_params()
        # 后处理（B2）：函数调用那条路不能动形状，所以带 tools 时跳过
        messages = messages if tools else post_process(messages, self.ai_post())
        body = {"model": model, "messages": messages, "max_tokens": max_tokens}
        for k in ("temperature", "top_p", "presence_penalty", "frequency_penalty"):
            if pr.get(k) is not None and pr.get(k) != "":
                body[k] = float(pr[k])
        if pr.get("max_tokens"):            # 用户自己指定了就盖掉各用途的默认
            body["max_tokens"] = int(pr["max_tokens"])
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if tools:
            body["tools"], body["tool_choice"] = tools, "auto"
        if stream:
            body["stream"] = True
        if str(pr.get("extra") or "").strip():
            # 附加参数：原样并进请求体。写坏了只影响这一次调用，报错会带上原文
            try:
                ex = json.loads(pr["extra"])
                if not isinstance(ex, dict):
                    raise ValueError("附加参数要是一个 JSON 对象")
                body.update(ex)
            except Exception as e:
                raise RuntimeError(f"「附加参数」不是合法 JSON 对象（{e}）。"
                                   "去「模型接入」页的「高级参数」里改，或者清空它")
        hdr = {"Content-Type": "application/json"}
        if p.get("api_key"):
            hdr["Authorization"] = "Bearer " + p["api_key"]
        if not base.endswith("/v1") and getattr(self, "models_base", ""):
            base = self.models_base              # 用拉模型时验证过的那个 base
        return base, hdr, body

    def acting(self):
        """允不允许模型自己动手改规则。「模型接入」页那个勾管的是这个 —— 关掉之后
        函数调用和 ```dcwatch 文本指令**都**不能用，不然那个勾等于没关。"""
        return bool((self.cfg.get("ai") or {}).get("tools", True))

    def tools_ok(self, provider_name, model):
        """这个模型能不能用函数调用（只看接口能力，跟上面那个勾无关）。
        试过一次被拒就记住，别每轮都白试一次。"""
        return f"{provider_name}|{model}" not in self.no_tools

    def mark_no_tools(self, provider_name, model, why=""):
        self.no_tools.add(f"{provider_name}|{model}")
        self.log("warn", f"{model} 这个接口不支持函数调用，工作台改用文本指令模式"
                         + (f"：{why[:120]}" if why else ""))

    def ai_used(self, t0, rule, model, u=None, err=""):
        if err:
            self.db.x("INSERT INTO aiusage(ts,rule,model,in_tok,out_tok,ok,err) VALUES(?,?,?,0,0,0,?)",
                      (t0, rule, model, err[:300]))
        else:
            u = u or {}
            self.db.x("INSERT INTO aiusage(ts,rule,model,in_tok,out_tok,ok,err) VALUES(?,?,?,?,?,1,'')",
                      (t0, rule, model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)))

    async def chat(self, provider_name, model, messages, json_mode=False, max_tokens=800, rule="-",
                   tools=None, want_msg=False):
        """want_msg=True 时返回整个 message（要读 tool_calls 就得要它），否则只返回正文。"""
        base, hdr, body = self.chat_prep(provider_name, model, messages, json_mode, max_tokens, tools)
        t0 = now()
        try:
            async with self.http.post(f"{base}/chat/completions", headers=hdr, json=body, **self.rq(),
                                      timeout=aiohttp.ClientTimeout(total=120)) as r:
                txt = await r.text()
                if r.status != 200:
                    if tools and r.status in (400, 404, 422) and "tool" in txt.lower():
                        raise ToolsUnsupported(txt[:200])
                    raise RuntimeError(f"{r.status} {txt[:300]}")
                j = json.loads(txt)
            msg = j["choices"][0]["message"]
            self.ai_used(t0, rule, model, j.get("usage"))
            return msg if want_msg else (msg.get("content") or "")
        except Exception as e:
            self.ai_used(t0, rule, model, err=str(e))
            raise

    async def chat_stream(self, provider_name, model, messages, max_tokens=1200, rule="-", tools=None):
        """流式。产出 ("delta", 文本片段) 若干，最后一个是 ("msg", 完整 message)。
        模型想十几秒才吐第一个字是常态，界面上只有一个「…」的话用户会以为卡死了。"""
        base, hdr, body = self.chat_prep(provider_name, model, messages, False, max_tokens, tools, True)
        t0, content, calls = now(), "", {}
        try:
            async with self.http.post(f"{base}/chat/completions", headers=hdr, json=body, **self.rq(),
                                      timeout=aiohttp.ClientTimeout(total=300, sock_read=120)) as r:
                if r.status != 200:
                    txt = (await r.text())[:300]
                    if tools and r.status in (400, 404, 422) and "tool" in txt.lower():
                        raise ToolsUnsupported(txt)
                    raise RuntimeError(f"{r.status} {txt}")
                async for raw in r.content:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        ch = (json.loads(data).get("choices") or [{}])[0]
                    except Exception:
                        continue
                    d = ch.get("delta") or ch.get("message") or {}
                    if d.get("content"):
                        content += d["content"]
                        yield ("delta", d["content"])
                    for tc in (d.get("tool_calls") or []):
                        cur = calls.setdefault(tc.get("index", len(calls)),
                                               {"id": "", "type": "function",
                                                "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            cur["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            cur["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            cur["function"]["arguments"] += fn["arguments"]
        except Exception as e:
            self.ai_used(t0, rule, model, err=str(e))
            raise
        self.ai_used(t0, rule, model)
        msg = {"role": "assistant", "content": content}
        if calls:
            msg["tool_calls"] = [calls[k] for k in sorted(calls)]
        yield ("msg", msg)

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
    def covers(self, rule, ev):
        """A7 收信闸：这条规则的「什么时候 + 听哪里 + 听谁」罩不罩得住这条消息。
        罩得住才收进收信箱；听内容（最短/关键词/正则）不在这里 —— 那档只筛提醒，
        拿它挡收信会把 AI 复核、工作台要看的现场上下文掐死。match() = covers() + 听内容。"""
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
        return True, "罩得住"

    def match(self, rule, ev):
        """ev: normalized event dict. returns (ok, reason)。
        收不收看 covers()；这里只管「提不提醒」的内容档（最短/关键词/正则）。"""
        ok, why = self.covers(rule, ev)
        if not ok:
            return False, why
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
        # 「有人开新帖」顺手排一次自动打开的队（C2）。跟规则命中无关：
        # 不点开就读不到帖内消息，而「值不值得点开」由 browser.only_rule_channels 决定。
        if ev.get("kind") == "thread" and not ev.get("scanned"):
            with contextlib.suppress(Exception):
                self.queue_thread(ev)
        # A7 收信闸：收信箱只收「有启用规则罩得住」的消息，零规则 = 零收信。
        # 闸只挡入库和实时推送：C2 自动开帖的排队在上面已经做完；抓历史走 store_only 不过闸。
        # 扩展自检消息（channel_id 固定 "0"）永远放行 —— 那是验通路的，不是收信。
        if ev["channel_id"] != "0" and not any(self.covers(r, ev)[0] for r in self.rules()):
            self._gate_n += 1
            return None
        matched, ai_json, score = [], None, None
        # AI 复核（B1）：passed 是「脚本命中且复核放行」的规则，通知只看它。
        # matched 仍记全部脚本命中的规则（那是事实，库里要留着），
        # 但被复核压掉的不进 passed —— 否则等于复核没生效。
        passed, chk_res = [], None
        for rule in self.rules():
            ok, _ = self.match(rule, ev)
            if not ok:
                continue
            matched.append(rule["name"])
            self.db.x("UPDATE rules SET hits=hits+1 WHERE id=?", (rule["id"],))
            if rule.get("ai_check"):
                chk = None
                try:
                    chk = await self.act_check(rule, ev)
                except Exception as e:
                    chk = {"err": str(e)}        # 调用炸了 → 下面 fail open 放行
                let_go, why = self.check_verdict(rule, chk)
                self.db.x("INSERT INTO aicheck(ts,rule,msg_id,hit,conf,kind,human,passed,"
                          "extracted,reason,err) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (now(), rule["name"], ev.get("msg_id") or "",
                           int(bool(chk.get("hit"))), int(chk.get("confidence") or 0),
                           str(chk.get("kind") or ""), int(bool(chk.get("need_human"))),
                           int(let_go), " ".join(chk.get("extracted") or []),
                           str(chk.get("reason") or ""), str(chk.get("err") or "")))
                if not let_go:
                    # 静默丢消息是这个程序最不该有的行为：日志必须说清是哪条、压掉了什么。
                    self.log("info", f"{rule['name']}: AI 复核判「不是」，没提醒 —— {why}"
                                     f"（消息 {ev.get('msg_id')}，原文前 40 字："
                                     f"{(ev.get('content') or '')[:40]}）")
                    continue
                if chk.get("err"):
                    self.log("warn", f"{rule['name']}: AI 复核没做成（{str(chk['err'])[:80]}），"
                                     f"按放行处理照旧提醒你")
                chk_res = chk_res or chk
            passed.append(rule["name"])
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
        row = dict(ev, id=mid, matched=",".join(matched), ai=ai_json, score=score, chk=chk_res)
        alert = bool(passed)                      # 复核压掉的不提醒（但库里 matched 照记）
        if score is not None and passed:
            mins = [r["notify_min_score"] for r in self.rules() if r["name"] in passed]
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

    def sys_prompt(self, key):
        """骨架提示词（向导 / 出规则 / 工作台 / 工作台的手）。同上：改过用他的。"""
        v = (self.cfg.get("sys_prompts") or {}).get(key)
        return v.strip() if isinstance(v, str) and v.strip() else BUILTIN_SYS_TEXT[key]

    def ai_params(self):
        """采样参数：出厂值 + 用户覆盖。值是 None/'' 的字段不往请求体里塞。"""
        return dict(DEFAULT_PARAMS, **{k: v for k, v in ((self.cfg.get("ai") or {})
                                                        .get("params") or {}).items()
                                      if k in DEFAULT_PARAMS})

    def ai_post(self):
        m = str((self.cfg.get("ai") or {}).get("post") or "off")
        return m if m in AI_POST else "off"

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

    async def act_check(self, rule, ev):
        """AI 复核（B1）：命中之后再让模型看一眼，把藏起来的密钥还原出来。

        返回一个 dict：正常是模型给的六个字段；读不懂 / 调用炸了就带 "err"，
        由 check_verdict 走 fail open。**只读、只回 JSON，一个工具都不给它**（不许做的事第 4 条）。
        """
        content = ev.get("content") or ""
        low = content.lower()
        if any(h.lower() in low for h in UNREADABLE_HINTS):
            # 真内容不在文字里，模型看了也只能瞎猜（还会编 key）。直接转人工，省一次调用。
            return {"hit": True, "confidence": 50, "kind": "unreadable", "need_human": True,
                    "extracted": [], "reason": "内容在附件/图片里，程序读不到", "by": "rule"}
        n = max(0, int(rule.get("ai_check_ctx") or 0))
        # ts<= 且倒序取 n+1 条再翻回来：要的是「这条 + 它前面几条」，这条排在最后。
        # 场景②（下一条说「删掉后面才是真的」）就靠这几条上下文才看得懂。
        rows = self.db.q("SELECT author,content FROM messages WHERE channel_id=? AND ts<=? "
                         "ORDER BY ts DESC LIMIT ?",
                         (ev.get("channel_id"), ev.get("ts") or now(), n + 1))[::-1] if n else []
        lines = [f"{r['author']}: {r['content']}" for r in rows
                 if str(r["content"] or "") != content]
        user = (f"频道: {ev.get('channel_name') or '?'}\n作者: {ev.get('author') or '?'}\n")
        if lines:
            user += "同频道前面几条（供你连起来读）：\n" + "\n".join(lines[-n:]) + "\n"
        user += f"要判断的这条：{content}"
        prov, model = self.pick_model(rule)
        out = await self.chat(prov, model, [
            {"role": "system", "content": rule.get("ai_check_prompt") or self.prompt("ai_check")},
            {"role": "user", "content": user}],
            json_mode=True, max_tokens=400, rule=rule["name"])
        try:
            j = json.loads(re.search(r"\{.*\}", out, re.S).group(0))
            if not isinstance(j, dict):
                raise ValueError("不是对象")
        except Exception:
            # 模型答了一段散文。**不能猜它想说什么** —— 交给 fail open 照旧通知。
            return {"err": "模型没按格式答：" + str(out)[:150]}
        ext = j.get("extracted")
        if isinstance(ext, str):
            ext = [ext]
        return {"hit": bool(j.get("hit", True)),
                "confidence": int(float(j.get("confidence") or 0)) if str(
                    j.get("confidence") or "").strip() not in ("", "None") else 0,
                "kind": str(j.get("kind") or "other")[:20],
                "extracted": [str(x).strip() for x in (ext or []) if str(x).strip()][:8],
                "need_human": bool(j.get("need_human")),
                "reason": str(j.get("reason") or "")[:200], "by": "model"}

    def check_verdict(self, rule, chk):
        """复核结论 → (放不放行, 为什么)。纯函数，好测。

        fail open 是这条功能的底线：模型挂了宁可吵一次，绝不吞消息（不许做的事第 3 条）。
        """
        if not isinstance(chk, dict) or chk.get("err"):
            return True, "复核失败，按 fail open 放行"
        if chk.get("need_human") and rule.get("ai_check_human", True):
            return True, "需要人工看"
        if chk.get("need_human"):
            return False, "模型说看不到内容，而你关掉了「看不到也提醒」"
        lo = int(rule.get("ai_check_min") or 0)
        conf = int(chk.get("confidence") or 0)
        if chk.get("hit") and conf >= lo:
            return True, ""
        return False, f"hit={chk.get('hit')} 置信 {conf} < 门槛 {lo}"

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

    def fmt_msg(self, ev, score=None, ai=None, chk=None):
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
        # AI 复核的结果（B1）：在这一层拼，所有出口（本机通知 / 转发 / {{text}}）就都带上了，
        # 不用在每个 hook 里各写一遍。
        if isinstance(chk, dict):
            if chk.get("err"):
                head = "[AI 复核失败] " + head
                extra += f"\n复核没做成：{str(chk['err'])[:60]}——所以照旧提醒你，请自己看一眼"
            elif chk.get("need_human"):
                head = "【需要你人工看】" + head
                extra += f"\n模型说：{chk.get('reason') or '内容我看不到'} —— 你自己去看一眼"
            if chk.get("extracted"):
                extra += "\n模型还原出来的：" + " ".join(str(x) for x in chk["extracted"])
        return head, body, extra, ev.get("url") or ""

    async def notify(self, ev, row):
        """命中后的统一出口。任何一个出口挂了都只记日志，不影响其它出口。"""
        s = self.cfg["sinks"]
        score = row.get("score")
        if score is not None and score < int(s.get("min_score") or 0):
            return
        head, body, extra, url = self.fmt_msg(ev, score, row.get("ai"), row.get("chk"))
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
        chk = row.get("chk") or {}
        return {"extracted": " ".join(str(x) for x in (chk.get("extracted") or [])),
                "need_human": "1" if chk.get("need_human") else "",
                "text": text, "title": head, "body": (body + extra).strip(),
                "content": ev.get("content") or "", "author": ev.get("author") or "",
                "channel": ev.get("channel_name") or "", "server": ev.get("guild_name") or "",
                "url": url, "score": "" if row.get("score") is None else row["score"],
                "tags": "、".join(str(t) for t in (ai.get("tags") or [])),
                "todo": ai.get("todo") or "",
                "json": json.dumps({"event": ev, "score": row.get("score"), "ai": row.get("ai"),
                                    "matched": row.get("matched"), "check": row.get("chk")},
                                   ensure_ascii=False)}

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
            "author": (d.get("member") or {}).get("nick") or au.get("global_name") or au.get("username", ""),
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
    # 骨架提示词的出厂值和参数出厂值：界面要拿它们做占位和「恢复默认」（B3 / B4）
    c["sys_defaults"] = BUILTIN_SYS_TEXT
    c["sys_meta"] = {k: v[0] for k, v in BUILTIN_SYS.items()}
    c["param_defaults"] = DEFAULT_PARAMS
    c["post_modes"] = AI_POST
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
            # 一眼结论：跟诊断包第 [0] 段同一份判断，界面上直接显示，
            # 别让用户先怀疑程序坏了、再去导出文件、再等人看
            "findings": app.findings(),
            "env": {"win": IS_WIN, "frozen": FROZEN, "data_dir": str(DATA_DIR), "port": app.port,
                    "ver": VERSION, "ext_min": EXT_MIN, "ext_have": ext_version(),
                    "autostart": autostart_state(), "pyver": sys.version.split()[0],
                    # 「我这份配置到底存在哪」必须能在界面上看见：
                    # 不然用户换个文件夹跑，看到旧的模型配置，只能怀疑是密钥泄露了
                    "code_dir": str(BASE), "db_path": str(DB_PATH),
                    "db_exists": Path(DB_PATH).exists(),
                    "shared_data": FROZEN,
                    # 旁听模式发不出消息，界面就不该摆一个按了必失败的发送框
                    "can_send": can_send(app),
                    "ai": dict({"stream": True, "tools": True}, **(app.cfg.get("ai") or {}))},
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
        if "sys_prompts" in patch:                    # 骨架提示词（B3）：只认已知的键
            sp, bad = {}, []
            if not isinstance(patch["sys_prompts"], dict):
                return web.json_response({"ok": False, "error": "sys_prompts 必须是对象"}, status=400)
            for k, v in patch["sys_prompts"].items():
                if k not in BUILTIN_SYS:
                    bad.append(k)
                elif isinstance(v, str) and v.strip() and v.strip() != BUILTIN_SYS_TEXT[k].strip():
                    sp[k] = v.strip()[:20000]         # 跟出厂值一样就不存，省得占地方
            if bad:
                return web.json_response({"ok": False, "error": "认不出的提示词名：" + "、".join(bad[:5])},
                                         status=400)
            patch["sys_prompts"] = sp
        if isinstance(patch.get("ai"), dict):         # 采样参数（B4）+ 后处理（B2）
            merged = dict(app.cfg.get("ai") or {})
            if "post" in patch["ai"] and patch["ai"]["post"] not in AI_POST:
                return web.json_response({"ok": False, "error": "post 只能是 " + " / ".join(AI_POST)},
                                         status=400)
            if "params" in patch["ai"]:
                ok, err = norm_params(patch["ai"]["params"])
                if err:
                    return web.json_response({"ok": False, "error": err}, status=400)
                patch["ai"]["params"] = ok
            merged.update(patch["ai"])
            patch["ai"] = merged
        if "browser" in patch:                        # 自动点开新帖（C2）
            clean, err = norm_browser(patch["browser"])
            if err:
                return web.json_response({"ok": False, "error": err}, status=400)
            patch["browser"] = dict(app.br_cfg(), **clean)
            if not patch["browser"]["auto_open"]:
                # 关掉总开关时把「排着队还没开的」一笔勾销。否则过一周他再打开，
                # 程序会突然点开一堆早就凉了的老帖子。已经开着的标签页不动（他可以自己关）。
                app.db.x("UPDATE threads_open SET closed_at=? WHERE opened_at IS NULL "
                         "AND closed_at IS NULL", (now(),))
        if "extract_templates" in patch:
            patch["extract_templates"] = norm_tpls(patch["extract_templates"])
        app.cfg.update(patch)
        app.save_cfg()
        if "sinks" in patch:
            # 开关改了要立刻告诉所有开着的标签页：不然旧页面拿着旧配置
            # 继续弹网页通知，用户以为「关了开关还弹」（A5）
            await app.bus.push("sinks", app.cfg["sinks"])
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
        return web.json_response({"ok": True, "models": ms,
                                  "rescued": getattr(app, "models_rescued", "") or ""})

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
        if rid:
            # 界面编辑一条导入来的规则时，别把「导入来的」这个戳擦掉（它只在库里，
            # 表单里没有对应输入框，所以提交的 body 里可能根本没带）
            old = app.db.q("SELECT json FROM rules WHERE id=?", (rid,))
            if old:
                prev = json.loads(old[0]["json"])
                for k in IMPORT_MARKS:
                    if k in prev and k not in b:
                        b[k] = prev[k]
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

    # ---------- 开服监听（D3） ----------
    @r.get("/api/watch")
    async def watchlist(_):
        rows = app.watch_list()
        for w in rows:
            w["check_ago"] = round(now() - w["last_check"], 1) if w["last_check"] else None
            w["change_ago"] = round(now() - w["last_change"], 1) if w["last_change"] else None
        return web.json_response({"ok": True, "watch": rows,
                                  "min_every": App.WATCH_MIN_EVERY})

    @r.post("/api/watch")
    async def watchsave(req):
        b = await req.json()
        row, err = app.watch_save(b)
        if err:
            return web.json_response({"ok": False, "error": err})
        return web.json_response({"ok": True, "id": row["id"], "watch": row})

    @r.delete("/api/watch/{wid}")
    async def watchdel(req):
        app.db.x("DELETE FROM watch WHERE id=?", (req.match_info["wid"],))
        return web.json_response({"ok": True})

    @r.post("/api/watch/{wid}/check")
    async def watchcheck(req):
        """「立刻查一次」：不等巡检周期，探完直接回最新状态。"""
        rows = app.db.q("SELECT * FROM watch WHERE id=?", (req.match_info["wid"],))
        if not rows:
            return web.json_response({"ok": False, "error": "这条监视已经不在了（刚删掉？）"})
        row = await app.watch_run(rows[0])
        return web.json_response({"ok": True, "watch": row})

    @r.get("/api/rules/export")
    async def exportrules(_):
        """所有规则导出成一个文件，可以直接发给另一台机器导入。

        带 schema 名和版本号：导入方先认 schema，认不出就明说，而不是硬吃下去
        再表现成「导进来了但什么都不听」。id 和命中数是本机的账，不导出。
        """
        rules = [rule_for_export(x) for x in app.rules(False)]
        pack = {"schema": RULES_SCHEMA, "app": "dcwatch", "version": VERSION,
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "count": len(rules), "rules": rules}
        fn = f"dcwatch-规则{len(rules)}条-v{VERSION}-{time.strftime('%m%d-%H%M')}.json"
        return web.Response(body=json.dumps(pack, ensure_ascii=False, indent=1).encode(),
                            headers={"Content-Type": "application/json; charset=utf-8",
                                     "Cache-Control": "no-store",
                                     "Content-Disposition":
                                     'attachment; filename="dcwatch-rules.json"; '
                                     f"filename*=UTF-8''{urllib.parse.quote(fn)}"})

    @r.post("/api/rules/import")
    async def importrules(req):
        """导入规则。**默认只预览不写库**，界面拿这份预览问用户「确定要覆盖这几条吗」。

        载荷：{data: 规则包(对象或原始文本), mode: "merge"|"replace", dry_run: true}
        mode=merge（默认）同名的覆盖、其余新增；mode=replace 还会删掉包里没有的规则。
        重名认的是 name —— 用户能看懂的东西只有名字，id 在两台机器上必然不同。
        """
        b = await req.json()
        data, mode = b.get("data"), (b.get("mode") or "merge")
        dry = b.get("dry_run", True)
        if mode not in ("merge", "replace"):
            return web.json_response({"ok": False, "error": "mode 只能是 merge 或 replace"}, status=400)
        if isinstance(data, (str, bytes)):
            try:
                data = json.loads(data)
            except Exception as e:
                return web.json_response(
                    {"ok": False, "error": f"这个文件不是 JSON，读不了（{e}）。"
                                           "请选从「导出规则」下载下来的那个 .json 文件"}, status=400)
        notes = []
        if isinstance(data, list):
            incoming, schema, from_ver = data, "", ""
            notes.append("这个文件没有 schema 头，按「一串规则」处理了")
        elif isinstance(data, dict) and isinstance(data.get("rules"), list):
            incoming = data["rules"]
            schema, from_ver = str(data.get("schema") or ""), str(data.get("version") or "")
            if schema and schema.split("/")[0] != RULES_SCHEMA.split("/")[0]:
                return web.json_response(
                    {"ok": False, "error": f"这不是 dcwatch 的规则包（schema={schema}）"}, status=400)
            if schema and schema != RULES_SCHEMA:
                notes.append(f"规则包版本是 {schema}，本程序是 {RULES_SCHEMA}，"
                             "认不出的字段会被丢掉")
            if not schema:
                notes.append("这个文件没写 schema，按 dcwatch 规则包试着读了")
        else:
            return web.json_response(
                {"ok": False, "error": "看不出这是规则包：要么是 {\"schema\":\"dcwatch.rules/1\","
                                       "\"rules\":[...]}，要么是一个规则数组"}, status=400)
        if not incoming:
            return web.json_response({"ok": False, "error": "这个规则包里一条规则都没有"}, status=400)

        have = app.rules(False)
        by_name = {}
        for x in have:
            by_name.setdefault(str(x.get("name") or "").strip(), x)
        plan, seen = [], set()
        for raw in incoming:
            rule, rnotes = sanitize_import_rule(raw)
            if rule is None:
                notes.extend(rnotes)
                continue
            en = 1 if (raw.get("enabled", 1) if isinstance(raw, dict) else 1) else 0
            if rule["action"].startswith("ai") and not (rule.get("model")
                                                        or app.cfg.get("default_model", {}).get("model")):
                rnotes.append("这条要用大模型，但本机还没设默认模型 —— 先去「模型接入」选一个")
            old = by_name.get(rule["name"])
            item = {"name": rule["name"], "enabled": en, "notes": rnotes, "rule": rule}
            if old and old["id"] not in seen:
                seen.add(old["id"])
                d = diff_rule(old, rule)
                if not d and bool(old["enabled"]) == bool(en):
                    item["act"], item["changes"] = "same", []
                else:
                    item["act"], item["changes"] = "overwrite", d
                    if bool(old["enabled"]) != bool(en):
                        item["changes"] = d + [("开关：" + ("开→关" if old["enabled"] else "关→开"))]
                item["target_id"] = old["id"]
                item["hits"] = old["hits"]
            else:
                item["act"], item["changes"] = "new", []
            plan.append(item)
        removes = ([{"id": x["id"], "name": x["name"], "hits": x["hits"]}
                    for x in have if x["id"] not in seen] if mode == "replace" else [])
        summary = {k: sum(1 for p in plan if p["act"] == k) for k in ("new", "overwrite", "same")}
        summary["remove"] = len(removes)

        if not dry:
            # 盖一个本机戳：这条是导入来的、什么时候、包是哪一版。诊断包 [4] 段会印出来，
            # 不然拿了别人一份规则包之后，排查时看不出「这条到底是他自己填的还是导进来的」。
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            for p in plan:
                if p["act"] == "same":
                    continue
                marked = dict(p["rule"], imported_at=stamp,
                              imported_from=(f"v{from_ver}" if from_ver else "不带版本号的规则包"))
                blob = json.dumps({k: v for k, v in marked.items()
                                   if k not in ("id", "enabled", "hits")}, ensure_ascii=False)
                if p["act"] == "overwrite":
                    app.db.x("UPDATE rules SET json=?,enabled=? WHERE id=?",
                             (blob, p["enabled"], p["target_id"]))
                else:
                    p["target_id"] = app.db.x("INSERT INTO rules(json,enabled) VALUES(?,?)",
                                              (blob, p["enabled"]))
            for x in removes:
                app.db.x("DELETE FROM rules WHERE id=?", (x["id"],))
            app.log("info", f"导入规则：新增 {summary['new']} 条、覆盖 {summary['overwrite']} 条、"
                            f"没变 {summary['same']} 条、删除 {summary['remove']} 条"
                            + (f"（来自 v{from_ver} 的规则包）" if from_ver else ""))
        for p in plan:
            p.pop("rule", None)
        return web.json_response({"ok": True, "dry_run": bool(dry), "mode": mode,
                                  "schema": schema, "from_version": from_ver,
                                  "plan": plan, "removes": removes, "summary": summary,
                                  "notes": notes, "rules": app.rules(False)})

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
        """把发给模型的每一段原话交出来 —— 现在**还能改**（B3）。

        以前这里是只读的，理由是「改坏了向导会失效」。用户的要求是「我得看得到内容，
        然后我可以改」，做成酒馆预设那样。所以现在：出厂值在 BUILTIN_SYS_TEXT 里当兜底，
        改过的存在 cfg["sys_prompts"]，一键恢复默认，整包可导出导入（/api/preset/*）。
        """
        cur = app.cfg.get("sys_prompts") or {}
        meta = {
            "wizard": {
                "when": "「监听规则」页顶部每问你一轮、每出一次规则，都会带上这段",
                "why": "把「该问什么、什么最容易漏、模糊需求该怎么拆」这些经验写死在这里，"
                       "这样换成便宜的模型也不至于乱答",
                "risk": "删掉里面的「只输出 JSON」或者字段表，向导会开始答非所问 —— 恢复默认即可"},
            "compose": {
                "when": "向导之外的快速通道，现在界面默认走向导，这条留着兼容",
                "why": "只翻译不反问，适合你已经很清楚要什么的时候",
                "risk": "同上"},
            "workbench": {
                "when": "「AI 工作台」里你每发一句话都会带上这段，后面还会附上这台机器的实况"
                        "（收信方式、有没有浏览器在旁听、你有哪些规则、最近哪些频道来过消息、"
                        "以及你这句话里贴的 Discord 链接拆出来的 ID）",
                "why": "没有这段的时候，模型不知道自己在一个监听程序里，会回「我无法访问 Discord」"
                       "然后推荐你去写 Discord 机器人或者用 Zapier —— 而这些功能你手上这个程序本来就有。"
                       "它也会把「帮我写规则」误解成群聊管理规则。",
                "risk": "把「监听是程序做的，不是你做的」那一段删了，模型立刻退回「我无法访问第三方平台」"},
            "wb_tools": {
                "when": "工作台每次调用都跟着「工作台身份」一起发，模型接口支持函数调用时用这版",
                "why": "没有这段，模型只会回「你去点第几个勾」；有了它，它自己就把规则改了",
                "risk": "改坏了模型会不敢动手，或者调错工具名"},
            "wb_text": {
                "when": "模型接口不支持函数调用时，自动换成这版（```dcwatch 文本块）",
                "why": "便宜模型多半不支持函数调用，靠约定一个文本块也能让它动手",
                "risk": "块的格式改了，程序就解析不出来，模型说改了其实没改"},
            "wb_off": {
                "when": "「模型接入」页的「允许模型直接改规则」关掉后，换成这段",
                "why": "明确告诉模型这轮只能讲步骤，不然它会假装自己改了",
                "risk": "小"},
        }
        return web.json_response({"ok": True, "editable": True, "builtin": [
            dict(meta[k], key=k, name=BUILTIN_SYS[k][0], where="server.py 里的" + BUILTIN_SYS[k][1],
                 text=app.sys_prompt(k), builtin=BUILTIN_SYS_TEXT[k],
                 changed=bool(str(cur.get(k) or "").strip()))
            for k in BUILTIN_SYS], "post": app.ai_post(), "post_modes": AI_POST,
            "params": app.ai_params(), "param_defaults": DEFAULT_PARAMS,
            "editable_hint": "这六条是骨架：改坏了向导 / 工作台会变笨，但点「恢复出厂」就回来，不会丢数据。"
                             "命中之后那几种动作（打分、摘要、抽取、回复）的提示词在「模型接入」页最下面。"})

    @r.get("/api/presets")
    async def listpresets(_):
        """程序自带的预设包（`presets/*.json`）。装的时候走 /api/preset/import 那道预览闸。

        为什么不写死在代码里：用户要的是「以文件的形式导入，但我得看得到内容、我可以改」。
        放成文件他自己就能用记事本打开看、改、或者删掉。
        """
        out = []
        for f in sorted(PRESET_DIR.glob("*.json")) if PRESET_DIR.is_dir() else []:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                out.append({"file": f.name, "name": f.name, "desc": f"这个文件读不了（{e}）",
                            "bad": True, "size": f.stat().st_size})
                continue
            out.append({"file": f.name, "name": str(d.get("name") or f.stem)[:80],
                        "desc": str(d.get("desc") or "")[:300], "size": f.stat().st_size,
                        "schema": str(d.get("schema") or "")})
        return web.json_response({"ok": True, "presets": out, "dir": str(PRESET_DIR)})

    @r.get("/api/presets/file")
    async def presetfile(req):
        """把自带预设的原文交出来（界面拿它去走预览闸）。只认 presets/ 下的文件名。"""
        name = Path(str(req.query.get("id", ""))).name          # 防目录穿越
        f = PRESET_DIR / name
        if not name.endswith(".json") or not f.is_file():
            return web.json_response({"ok": False, "error": f"没有这个自带预设：{name}"}, status=404)
        try:
            return web.json_response(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:
            return web.json_response({"ok": False, "error": f"{name} 不是合法 JSON（{e}）"}, status=400)

    @r.get("/api/preset/export")
    async def exportpreset(_):
        """整包导出预设（骨架提示词 + 动作提示词 + 采样参数 + 后处理模式）。

        不含 Key、不含服务商地址、不含规则 —— 换机器那些必然不一样。跟提取模板包同一套口径。
        """
        pack = preset_pack(app.cfg)
        fn = f"dcwatch-预设-v{VERSION}-{time.strftime('%m%d-%H%M')}.json"
        return web.Response(body=json.dumps(pack, ensure_ascii=False, indent=1).encode(),
                            headers={"Content-Type": "application/json; charset=utf-8",
                                     "Cache-Control": "no-store",
                                     "Content-Disposition":
                                     'attachment; filename="dcwatch-preset.json"; '
                                     f"filename*=UTF-8\'\'{urllib.parse.quote(fn)}"})

    @r.post("/api/preset/import")
    async def importpreset(req):
        """导入预设。**默认只预览不写库**（硬规矩 10）—— 提示词是用户一个字一个字调出来的。

        载荷：{data: 预设包(对象或原始文本), dry_run: true}
        """
        b = await req.json()
        data, dry = b.get("data"), b.get("dry_run", True)
        if isinstance(data, (str, bytes)):
            try:
                data = json.loads(data)
            except Exception as e:
                return web.json_response(
                    {"ok": False, "error": f"这个文件不是 JSON，读不了（{e}）。"
                                           "请选从「导出预设」下载下来的那个 .json 文件"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "预设包要是一个对象，形如 "
                                      "{\"schema\":\"dcwatch.preset/1\",\"sys_prompts\":{...}}"},
                                     status=400)
        notes, schema = [], str(data.get("schema") or "")
        if schema and schema.split("/")[0] != PRESET_SCHEMA.split("/")[0]:
            # 走错门：把规则包 / 提取模板包喂给预设导入
            which = ("规则包，要去「监听规则」页点「⇣ 导入规则」" if schema.startswith("dcwatch.rules")
                     else "提取模板包，要去「批量提取」页导入" if schema.startswith("dcwatch.extract")
                     else f"schema={schema}")
            return web.json_response({"ok": False, "error": f"这不是预设包（{which}）"}, status=400)
        if schema and schema != PRESET_SCHEMA:
            notes.append(f"预设包版本是 {schema}，本程序是 {PRESET_SCHEMA}，认不出的字段会被丢掉")
        if not schema:
            notes.append("这个文件没写 schema，按 dcwatch 预设包试着读了")
        if not any(isinstance(data.get(k), (dict, str)) for k in ("sys_prompts", "prompts", "params", "post")):
            return web.json_response({"ok": False, "error": "这个预设包里没有任何能用的内容："
                                      "sys_prompts / prompts / params / post 一个都没有"}, status=400)
        changes = diff_preset(app.cfg, data)
        if not dry and changes:
            sp = dict(app.cfg.get("sys_prompts") or {})
            for k, v in (data.get("sys_prompts") or {}).items():
                if k in BUILTIN_SYS and isinstance(v, str) and v.strip():
                    if v.strip() == BUILTIN_SYS_TEXT[k].strip():
                        sp.pop(k, None)              # 跟出厂一样就别存
                    else:
                        sp[k] = v.strip()[:20000]
            pm = dict(app.cfg.get("prompts") or {})
            for k, v in (data.get("prompts") or {}).items():
                if k in DEFAULT_PROMPTS and isinstance(v, str):
                    pm[k] = v.strip()[:20000]
            ai = dict(app.cfg.get("ai") or {})
            if isinstance(data.get("params"), dict):
                clean, err = norm_params({k: v for k, v in data["params"].items() if k in DEFAULT_PARAMS})
                if err:
                    return web.json_response({"ok": False, "error": "预设里的参数有问题：" + err}, status=400)
                ai["params"] = dict(ai.get("params") or {}, **clean)
            if str(data.get("post") or "") in AI_POST:
                ai["post"] = str(data["post"])
            app.cfg.update({"sys_prompts": sp, "prompts": pm, "ai": ai})
            app.save_cfg()
            app.log("info", f"导入预设「{str(data.get('name') or '未命名')[:40]}」，"
                            f"{len(changes)} 处改动生效")
        return web.json_response({"ok": True, "dry_run": bool(dry), "name": str(data.get("name") or "")[:80],
                                  "from_version": str(data.get("version") or ""),
                                  "changes": changes, "notes": notes,
                                  "config": None if dry else safe_cfg(app.cfg)})

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
        msgs = ([{"role": "system", "content": app.sys_prompt("wizard")},
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
            out = await app.chat(prov, model, [{"role": "system", "content": app.sys_prompt("compose")},
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

    def wb_prepare(b):
        """工作台一次请求的公共准备：模型、消息、允许写进规则的真实 ID。"""
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
        hist = [m for m in (b.get("history") or [])
                if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")][-8:]
        extra = (b.get("system") or app.prompt("ask") or "").strip()
        hands = app.sys_prompt("wb_off") if not app.acting() else \
            app.sys_prompt("wb_tools" if app.tools_ok(prov, model) else "wb_text")
        sysmsg = app.sys_prompt("workbench") + "\n\n" + hands \
            + "\n\n## 这台机器现在的实况\n" + app.workbench_ctx(text)
        if extra and extra != DEFAULT_PROMPTS["ask"]:
            sysmsg += "\n\n## 用户自己追加的要求（优先照做，但不能违反上面的边界）\n" + extra
        msgs = [{"role": "system", "content": sysmsg}]
        msgs += [{"role": m["role"], "content": str(m["content"])[:4000]} for m in hist]
        msgs.append({"role": "user", "content": (f"消息上下文:\n{ctx}\n\n" if ctx else "") + text})
        # 允许写进规则的 ID：库里见过的 + 他自己在这句话/这轮对话里贴出来的
        _, known = app.rule_ctx(text + " " + " ".join(str(m.get("content") or "") for m in hist))
        return prov, model, msgs, known

    def wb_save_pair(sid, user_text, ai_text, acts):
        """一轮一问一答落库。sid 为空或不存在就建个新会话；写进去同时把会话名顶成
        第一句话的头 20 个字（还是「新会话」才顶），并刷新 updated。"""
        user_text, ai_text = str(user_text)[:4000], str(ai_text or "")[:8000]
        if not user_text:
            return None
        sid = int(sid or 0)
        row = app.db.q("SELECT id,name FROM wb_sessions WHERE id=?", (sid,))
        if not row:
            ts = now()
            sid = app.db.x("INSERT INTO wb_sessions(name,created,updated) VALUES('新会话',?,?)",
                           (ts, ts))
            app.db.x("INSERT INTO kv(k,v) VALUES('wb_cur',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                     (str(sid),))
            row = [{"name": "新会话"}]
        ts = now()
        app.db.x("INSERT INTO wb_msgs(sid,r,t,acts,ts) VALUES(?,?,?,'',?)", (sid, "u", user_text, ts))
        app.db.x("INSERT INTO wb_msgs(sid,r,t,acts,ts) VALUES(?,?,?,?,?)",
                 (sid, "a", ai_text, json.dumps(acts or [], ensure_ascii=False), ts))
        name = row[0]["name"]
        if name == "新会话":
            name = user_text.replace("\n", " ").strip()[:20] or "新会话"
        app.db.x("UPDATE wb_sessions SET name=?,updated=? WHERE id=?", (name, ts, sid))
        return sid

    @r.post("/api/ask")
    async def ask(req):
        b = await req.json()
        prov, model, msgs, known = wb_prepare(b)
        try:
            if b.get("plain"):       # 「测试一次调用」这种：只要一句回话，别让它去调工具
                return web.json_response({"ok": True, "acts": [], "changed": False,
                                          "text": await app.chat(prov, model, msgs, max_tokens=200,
                                                                 rule="manual")})
            text, acts, changed = await wb_run(app, prov, model, msgs, known)
            wb_save_pair(b.get("sid"), b.get("prompt", ""), text, acts)
            out = {"ok": True, "text": text, "acts": acts, "changed": changed}
            if changed:
                out["rules"] = app.rules(False)
            return web.json_response(out)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

    @r.post("/api/ask/stream")
    async def ask_stream(req):
        """流式版工作台。模型想十几秒才开口是常事，没有流式的话界面上只有一个「…」，
        用户分不清是在想还是卡死了。"""
        b = await req.json()
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream; charset=utf-8",
                                           "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        await resp.prepare(req)

        async def line(obj):
            with contextlib.suppress(Exception):
                await resp.write(b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\n\n")

        try:
            prov, model, msgs, known = wb_prepare(b)
            await line({"t": "start", "model": model})

            async def emit(kind, payload):
                await line({"t": kind, "d": payload} if kind == "delta" else
                           {"t": kind, "act": payload} if kind == "act" else {"t": kind, "d": payload})
            text, acts, changed = await wb_run(app, prov, model, msgs, known, emit=emit, stream=True)
            wb_save_pair(b.get("sid"), b.get("prompt", ""), text, acts)
            done = {"t": "done", "text": text, "acts": acts, "changed": changed}
            if changed:
                done["rules"] = app.rules(False)
            await line(done)
        except Exception as e:
            await line({"t": "error", "error": str(e)[:400]})
        with contextlib.suppress(Exception):
            await resp.write_eof()
        return resp

    def wb_sess_list():
        """会话列表 + 每个会话的消息条数（界面排序、显示用）。"""
        rows = app.db.q("SELECT id,name,created,updated FROM wb_sessions ORDER BY updated DESC")
        last = {r["sid"]: r["n"] for r in app.db.q(
            "SELECT sid,COUNT(*) n FROM wb_msgs GROUP BY sid")}
        for r in rows:
            r["n"] = last.get(r["id"], 0)
        return rows

    @r.get("/api/wb/sessions")
    async def wb_sessions(_):
        cur = app.db.q("SELECT v FROM kv WHERE k='wb_cur'")
        cur = int(cur[0]["v"]) if cur and cur[0]["v"] else 0
        return web.json_response({"ok": True, "sessions": wb_sess_list(), "cur": cur})

    @r.post("/api/wb/session/new")
    async def wb_new(_):
        ts = now()
        sid = app.db.x("INSERT INTO wb_sessions(name,created,updated) VALUES('新会话',?,?)", (ts, ts))
        app.db.x("INSERT INTO kv(k,v) VALUES('wb_cur',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (str(sid),))
        return web.json_response({"ok": True, "id": sid})

    @r.post("/api/wb/session/open")
    async def wb_open(req):
        """打开一个会话：记为当前，并把它的消息捞出来（最多 60 条，防止超长）。"""
        b = await req.json()
        sid = int(b.get("id") or 0)
        if not app.db.q("SELECT id FROM wb_sessions WHERE id=?", (sid,)):
            return web.json_response({"ok": False, "error": "会话不存在"}, status=404)
        app.db.x("INSERT INTO kv(k,v) VALUES('wb_cur',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (str(sid),))
        rows = app.db.q("SELECT r,t,acts,ts FROM wb_msgs WHERE sid=? ORDER BY id DESC LIMIT 60", (sid,))
        msgs = [{"r": x["r"], "t": x["t"],
                 "acts": json.loads(x["acts"]) if x["acts"] else []}
                for x in reversed(rows)]
        return web.json_response({"ok": True, "id": sid, "name": app.db.q(
            "SELECT name FROM wb_sessions WHERE id=?", (sid,))[0]["name"], "msgs": msgs})

    @r.post("/api/wb/session/rename")
    async def wb_rename(req):
        b = await req.json()
        sid, name = int(b.get("id") or 0), str(b.get("name") or "").strip()[:40]
        if not sid or not name:
            return web.json_response({"ok": False, "error": "缺 id 或 name"}, status=400)
        app.db.x("UPDATE wb_sessions SET name=? WHERE id=?", (name, sid))
        return web.json_response({"ok": True})

    @r.post("/api/wb/session/del")
    async def wb_del(req):
        """删会话连着它的消息一起删。界面必须先 confirm，这里没有 second chance。"""
        b = await req.json()
        sid = int(b.get("id") or 0)
        app.db.x("DELETE FROM wb_msgs WHERE sid=?", (sid,))
        app.db.x("DELETE FROM wb_sessions WHERE id=?", (sid,))
        # 删的是当前会话就把指针清掉，界面会自己建新会话
        cur = app.db.q("SELECT v FROM kv WHERE k='wb_cur'")
        if cur and cur[0]["v"] == str(sid):
            app.db.x("DELETE FROM kv WHERE k='wb_cur'")
        return web.json_response({"ok": True})

    BATCH_SYS = """你在批量翻一批 Discord 消息，把用户要的东西挑出来。

只输出 JSON：{"rows":[{"msg_id":"...","value":"挑出来的东西","note":"一句话说明/上下文"}]}
- 一条消息里有多个就拆成多行；没有就**不要**给这条消息编一行出来。
- value 必须是消息里**原样出现**的内容，一个字都不许改、不许补全、不许猜。
- 整批都没有就给 {"rows":[]}。宁可漏，不许编 —— 编出来的东西会让用户白跑一趟。"""

    @r.get("/api/extract/export")
    async def exporttpls(_):
        """把批量提取的模板整包导出成一个文件，可以发给别人或搬到另一台机器。

        跟规则包同一套口径：带 schema 名和版本号，id 和「导入来的」戳是本机的账，不导出。
        """
        tpls = [tpl_for_export(t) for t in norm_tpls(app.cfg.get("extract_templates"))]
        pack = {"schema": EXTRACT_SCHEMA, "app": "dcwatch", "version": VERSION,
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "count": len(tpls), "templates": tpls}
        fn = f"dcwatch-提取模板{len(tpls)}个-v{VERSION}-{time.strftime('%m%d-%H%M')}.json"
        return web.Response(body=json.dumps(pack, ensure_ascii=False, indent=1).encode(),
                            headers={"Content-Type": "application/json; charset=utf-8",
                                     "Cache-Control": "no-store",
                                     "Content-Disposition":
                                     'attachment; filename="dcwatch-extract.json"; '
                                     f"filename*=UTF-8''{urllib.parse.quote(fn)}"})

    @r.post("/api/extract/import")
    async def importtpls(req):
        """导入提取模板。**默认只预览不写库**（硬规矩 10），界面拿这份预览问用户。

        载荷：{data: 模板包(对象或原始文本), mode: "merge"|"replace", dry_run: true}
        重名认 name 不认 id —— 用户能看懂的只有名字，id 在两台机器上必然不同。
        """
        b = await req.json()
        data, mode = b.get("data"), (b.get("mode") or "merge")
        dry = b.get("dry_run", True)
        if mode not in ("merge", "replace"):
            return web.json_response({"ok": False, "error": "mode 只能是 merge 或 replace"}, status=400)
        if isinstance(data, (str, bytes)):
            try:
                data = json.loads(data)
            except Exception as e:
                return web.json_response(
                    {"ok": False, "error": f"这个文件不是 JSON，读不了（{e}）。"
                                           "请选从「导出模板」下载下来的那个 .json 文件"}, status=400)
        notes = []
        if isinstance(data, list):
            incoming, schema, from_ver = data, "", ""
            notes.append("这个文件没有 schema 头，按「一串模板」处理了")
        elif isinstance(data, dict) and isinstance(data.get("templates"), list):
            incoming = data["templates"]
            schema, from_ver = str(data.get("schema") or ""), str(data.get("version") or "")
            if schema and schema.split("/")[0] != EXTRACT_SCHEMA.split("/")[0]:
                # 最容易发生的走错门：把规则包喂给模板导入。明说是哪一种，别让他自己猜
                which = "规则包" if schema.startswith("dcwatch.rules") else f"schema={schema}"
                return web.json_response(
                    {"ok": False, "error": f"这不是提取模板包（{which}）。"
                     + ("规则包要去「监听规则」页点「⇣ 导入规则」"
                        if schema.startswith("dcwatch.rules") else "")}, status=400)
            if schema and schema != EXTRACT_SCHEMA:
                notes.append(f"模板包版本是 {schema}，本程序是 {EXTRACT_SCHEMA}，认不出的字段会被丢掉")
            if not schema:
                notes.append("这个文件没写 schema，按 dcwatch 模板包试着读了")
        else:
            # 最容易发生的走错门：把规则包喂给模板导入口。两个包长得几乎一样，
            # 只有 rules / templates 这一个键不同 —— 明说是哪一种并指路，别让他自己猜。
            sch = str((data or {}).get("schema") or "") if isinstance(data, dict) else ""
            if isinstance(data, dict) and (isinstance(data.get("rules"), list)
                                           or sch.startswith("dcwatch.rules")):
                return web.json_response(
                    {"ok": False, "error": "这是**规则包**，不是提取模板包。"
                                           "规则要去「监听规则」页点「⇣ 导入规则」"}, status=400)
            return web.json_response(
                {"ok": False, "error": "看不出这是模板包：要么是 {\"schema\":\"dcwatch.extract/1\","
                                       "\"templates\":[...]}，要么是一个模板数组"}, status=400)
        if not incoming:
            return web.json_response({"ok": False, "error": "这个模板包里一个模板都没有"}, status=400)

        have = norm_tpls(app.cfg.get("extract_templates"))
        by_name = {}
        for x in have:
            by_name.setdefault(x["name"], x)
        plan, seen, merged = [], set(), list(have)
        for raw in incoming:
            t, tnotes = sanitize_import_tpl(raw)
            if t is None:
                notes.extend(tnotes)
                continue
            old = by_name.get(t["name"])
            item = {"name": t["name"], "want": t["want"][:120], "notes": tnotes}
            if old and old["id"] not in seen:
                seen.add(old["id"])
                d = diff_tpl(old, t)
                item["act"], item["changes"] = ("same", []) if not d else ("overwrite", d)
                item["target_id"] = old["id"]
                if d:
                    for i, m in enumerate(merged):
                        if m["id"] == old["id"]:
                            merged[i] = dict(t, id=old["id"])
            else:
                item["act"], item["changes"] = "new", []
                merged.append(t)
            plan.append(item)
        removes = ([{"id": x["id"], "name": x["name"]} for x in have if x["id"] not in seen]
                   if mode == "replace" else [])
        if mode == "replace":
            gone = {x["id"] for x in removes}
            merged = [m for m in merged if m["id"] not in gone]
        summary = {k: sum(1 for p in plan if p["act"] == k) for k in ("new", "overwrite", "same")}
        summary["remove"] = len(removes)

        if not dry:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            names = {p["name"] for p in plan if p["act"] != "same"}
            for m in merged:
                if m["name"] in names:
                    m["imported_at"] = stamp
                    m["imported_from"] = f"v{from_ver}" if from_ver else "不带版本号的模板包"
            app.cfg["extract_templates"] = norm_tpls(merged)
            app.save_cfg()
            app.log("info", f"导入提取模板：新增 {summary['new']} 个、覆盖 {summary['overwrite']} 个、"
                            f"没变 {summary['same']} 个、删除 {summary['remove']} 个"
                            + (f"（来自 v{from_ver} 的模板包）" if from_ver else ""))
        return web.json_response({"ok": True, "dry_run": bool(dry), "mode": mode,
                                  "schema": schema, "from_version": from_ver,
                                  "plan": plan, "removes": removes, "summary": summary,
                                  "notes": notes,
                                  "templates": norm_tpls(app.cfg.get("extract_templates"))})

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

    @r.options("/api/ext/hb")
    async def hb_pre(_):
        return web.Response(headers=CORS)

    @r.options("/api/ext/tabs")
    async def tabs_pre(_):
        return web.Response(headers=CORS)

    async def _hb_body(req):
        if req.method == "POST":
            with contextlib.suppress(Exception):
                return await req.json()
        return {}

    @r.route("*", "/api/ext/hb")
    async def ext_hb(req):
        """扩展每 30 秒问一次「有什么要我干的」（C2）。

        为什么挂在这里而不是塞进 /api/state：/api/state 是界面也在轮询的公共状态，
        把「去开这个标签页」的指令混进去，等于每个开着界面的标签页都收到一遍。
        指令必须点对点发给**一个**桥，见 lead_bridge()。
        """
        if req.method not in ("GET", "POST"):
            return web.json_response({"ok": False, "error": "用 GET 或 POST"}, status=405,
                                     headers=CORS)
        b = await _hb_body(req)
        bid = str(b.get("bridge") or req.query.get("bridge") or "")[:64]
        if bid:
            self_hb = app.hb_seen
            self_hb[bid] = now()
            # 别让这个表无限长：两天没来要过指令的桥忘掉（它再来时会重新登记）
            for dead in [x for x, t in self_hb.items() if now() - t > 172800]:
                del self_hb[dead]
        if b.get("ver") or req.query.get("ver"):
            # 顺手记一下这个桥还活着（不带 n，不影响消息计数）
            app.touch_bridge({"bridge": bid, "ver": b.get("ver") or req.query.get("ver")})
        o = app.tab_orders(bid)
        return web.json_response({"ok": True, "server_ver": VERSION, "ext_min": EXT_MIN,
                                  "open": o["open"], "close": o["close"], "limits": o["limits"]},
                                 headers=CORS)

    @r.post("/api/ext/tabs")
    async def ext_tabs(req):
        """扩展回报执行结果。这一条是整个自动开帖功能的准确性来源 ——
        服务端不许自己猜「现在开着哪些」（用户手动关了标签页，服务端不会知道）。"""
        b = await req.json()
        if not isinstance(b, dict):
            return web.json_response({"ok": False, "error": "要一个对象"}, status=400, headers=CORS)
        got = app.tabs_report(b)
        o = app.tab_orders(str(b.get("bridge") or "")[:64])
        return web.json_response({"ok": True, **got, "open_now": o["limits"]["open_now"]},
                                 headers=CORS)

    @r.get("/api/ext/threads")
    async def ext_threads(_):
        """界面用：现在程序开着哪些帖子标签页（「自动点开新帖」那一块的状态行）。"""
        rows = []
        for r_ in app.db.q("SELECT * FROM threads_open ORDER BY COALESCE(opened_at,wanted) DESC LIMIT 50"):
            last = app.thread_last(r_["tid"], r_["opened_at"])
            rows.append({"tid": r_["tid"], "name": r_["name"], "url": r_["url"],
                         "parent_id": r_["parent_id"],
                         "state": ("已关" if r_["closed_at"] else "开着" if r_["opened_at"]
                                   else ("打开失败" if (r_["tries"] or 0) >= 2 else "排队中")),
                         "opened_ago": ago_txt(r_["opened_at"]) if r_["opened_at"] else "",
                         "idle": ago_txt(last) if last else "", "tries": r_["tries"] or 0,
                         "err": r_["err"] or ""})
        o = app.tab_orders()
        return web.json_response({"ok": True, "threads": rows, "limits": o["limits"],
                                  "ext_min": EXT_MIN})

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

        P("\n[0] 一眼结论（程序自己查的，下面几段是原始数据）")
        for f in app.findings():
            P("  " + {"bad": "✗", "warn": "!", "ok": "✓"}.get(f["level"], "-"), f["what"])
            P("      →", f["why"])

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
            if b.get("stale_ctx"):
                P("      注意      这是页面里的旧脚本直连（扩展重载/更新过，那个标签页没按 F5）")
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
        if app._gate_n:
            P("    收信闸   ", f"服务端拦下 {app._gate_n} 条：没有任何启用的规则罩得住它们"
              "（没写规则 = 不收信）。想收就去建/改一条规则把那个频道框进来。")

        P("\n[4] 规则（自上而下全部匹配）")
        rules = app.rules(False)
        if not rules:
            P("  一条都没有")
        imp = [j for j in rules if j.get("imported_at")]
        if imp:
            last = max(j["imported_at"] for j in imp)
            P(f"  其中 {len(imp)}/{len(rules)} 条是导入来的，最近一次导入 {last}"
              "（导进来的规则里的频道 ID 是对方的，不一定是你能看到的频道）")
        for j in rules:
            P("  -", ("[开]" if j.get("enabled") else "[停]"), j.get("name") or "(没名字)",
              "| 命中", j.get("hits", 0), "次", "| 动作", j.get("action"))
            kinds = j.get("kinds") or ["msg"]
            P("      什么时候 ", "、".join(("有人发消息" if k == "msg" else "有人开新帖") for k in kinds)
              + ("   ← 只听新帖，普通聊天消息不会命中" if "msg" not in kinds else ""))
            P("      听哪里   ", "服务器", V(j.get("guild_ids")), "频道", V(j.get("channel_ids")),
              "子区", V(j.get("thread_ids")),
              "| 含子区" if j.get("include_threads_of_channels") else "", "| 私信" if j.get("dm") else "")
            P("      听谁     ", "用户", V(j.get("author_ids")), "| 昵称含", V(j.get("author_name_contains")),
              "| 忽略机器人", V(j.get("ignore_bots")), "| 只在@我", V(j.get("mention_only")))
            P("      听内容   ", "任一", V(j.get("keywords_any")), "| 全含", V(j.get("keywords_all")),
              "| 正则", V(j.get("regex")), "| 最短", j.get("min_len"))
            P("      阈值/节流", "分数≥", j.get("notify_min_score"), "| 冷却", j.get("cooldown_sec"), "秒",
              "| 每小时≤", j.get("max_per_hour"), "| 攒", j.get("summary_every"))
            P("      AI 复核   ", "[开]" if j.get("ai_check") else "[关]",
              "| 门槛", j.get("ai_check_min"), "| 上下文", j.get("ai_check_ctx"), "条",
              "| 看不到也提醒", V(j.get("ai_check_human")),
              "| 自带提示词" if str(j.get("ai_check_prompt") or "").strip() else "| 用默认提示词")
            if j.get("imported_at"):
                P("      来路     ", f"{j['imported_at']} 从规则包导入"
                                     f"（{j.get('imported_from') or '来源不明'}）")

        P("\n[4.5] 拿真实消息试算（规则填了什么，和会不会命中，是两回事）")
        dry = app.dry_run_samples()
        if not rules:
            P("  没有规则，跳过")
        elif not dry:
            P("  没有可用的样本：库里没消息，扩展也还没送来看到过的正文。")
        for s in dry:
            P("  消息：", (s["text"] or "")[:60], "（来源：" + s["from"] + "）")
            for rw in s["rows"]:
                P("     ", "[开]" if rw["on"] else "[停]", rw["name"], "→",
                  ("会命中" if rw["ok"] else "不命中 · " + rw["why"])
                  + ("" if rw["on"] else "（这条是停用的，就算命中也不会提醒）"))
        if dry and any(s["cut"] for s in dry):
            P("  注：正文只有前 40 字，关键词卡在后半句的话这里会显示成不命中，属正常。")
        if any("@我" in rw["why"] for s in dry for rw in s["rows"]):
            P("  注：试算的样本没带「有没有 @ 你」这个信息，所以勾了「只在@我」的规则在这里一定算不中。")
        if any(j.get("ai_check") for j in rules):
            P("  注：有规则开了 AI 复核，上面只算了**脚本条件那一半**。脚本这半都不中的话，"
              "模型根本不会被叫起来；脚本中了还要过复核那道闸，结果看下面 [4.6]。")

        # AI 复核的战果（B1）。为什么必须有这一段：复核判「不是」而被压掉的消息，
        # 用户自己**永远发现不了**（他只知道"没提醒我"）。这段是唯一的追查入口。
        P("\n[4.6] AI 复核最近战果（开了「AI 复核」的规则才有）")
        chks = app.db.q("SELECT * FROM aicheck ORDER BY id DESC LIMIT 20")
        if not chks:
            P("  一次都没跑过。要么没有规则开复核，要么开了的那条还没被脚本条件命中过"
              "（复核只在脚本命中之后才叫模型 —— 这是省额度的设计，不是 bug）")
        for c in chks:
            P("  ", time.strftime("%m-%d %H:%M:%S", time.localtime(c["ts"])),
              "|", (c["rule"] or "?")[:14],
              "|", "放行" if c["passed"] else "压掉",
              "| 置信", c["conf"], "| hit", c["hit"], "|", c["kind"] or "-",
              "| 提取到", len((c["extracted"] or "").split()) if c["extracted"] else 0, "个",
              "| 要人工看" if c["human"] else "",
              "|", ("失败：" + (c["err"] or "")[:50]) if c["err"] else (c["reason"] or "")[:50])
        if chks:
            d1 = app.db.q("SELECT COUNT(*) n, SUM(passed) p, SUM(err<>'') e FROM aicheck WHERE ts>?",
                          (now() - 86400,))[0]
            n, p, e = d1["n"] or 0, d1["p"] or 0, d1["e"] or 0
            P(f"  最近 24 小时复核 {n} 次，放行 {p}，压掉 {n - p}，失败 {e}"
              f"（失败一律按放行处理，宁可吵你一次也不吞消息）")
            if n and n == e:
                P("  ⚠ 全都失败了：多半是没选默认模型、Key 错了、或者出网被挡。"
                  "看 [6] 日志里那条 warn 的原文。这期间等于复核没生效（消息照旧提醒你）。")

        P("\n[5] 通知与转发")
        sk = app.cfg.get("sinks", {})
        P("  本机提醒        ", "弹窗", sk.get("toast"), "| 声音", sk.get("sound"), sk.get("sound_name") or "",
          "| 免打扰", sk.get("quiet_from"), "-", sk.get("quiet_to"), "| 分数门槛", sk.get("min_score"))
        for h in sk.get("hooks", []):
            P("  出口            ", ("[开]" if h.get("enabled") else "[停]"), h.get("name") or "(没名字)",
              "→", mask(h.get("url")), "| 测过" if h.get("verified") else "| 没测过")
        if not sk.get("hooks"):
            P("  出口             （一个都没配）")

        # 硬规矩 5：新增了字段就得在诊断包里印出来，不然排查时只能靠猜。
        # 模板不参与"为什么没提醒我"，所以只占一小段：有几个、限没限频道、是不是导入来的。
        P("\n[5.5] 批量提取的模板")
        tpls = norm_tpls(app.cfg.get("extract_templates"))
        if not tpls:
            P("  一个都没有（批量提取每次手打也能用，模板只是存下来省事）")
        for t in tpls:
            P("  -", t["name"], "| 读", t["limit"], "条",
              "| 频道 " + (t["channel_id"] or "不限"),
              "| 只看命中过的" if t["only_matched"] else "",
              "| 昵称含 " + t["author_contains"] if t["author_contains"] else "")
            P("      要提取   ", (t["want"] or "")[:80])
            if t.get("imported_at"):
                P("      来路     ", f"{t['imported_at']} 从模板包导入"
                                     f"（{t.get('imported_from') or '来源不明'}）")

        P("\n[5.6] 工作台会话")
        sess = app.db.q("SELECT id,name,updated FROM wb_sessions ORDER BY updated DESC")
        cnt = {r["sid"]: r["n"] for r in app.db.q("SELECT sid,COUNT(*) n FROM wb_msgs GROUP BY sid")}
        if not sess:
            P("  还没有会话（工作台一句话都没说过；聊天是从这个版本才开始存库的）")
        for s in sess:
            P("  -", f"#{s['id']}", s["name"] or "新会话", "|", cnt.get(s["id"], 0), "条消息",
              "| 最后", time.strftime("%m-%d %H:%M", time.localtime(s["updated"] or 0)))

        # 硬规矩 5 again：提示词和采样参数会直接决定模型的行为，
        # 「模型答得不对」的排查第一步就是看这里改过什么。不印全文（几千字），只印改没改。
        P("\n[5.7] 模型怎么被指挥的（提示词 + 采样参数）")
        P("  提示词后处理     ", app.ai_post(), "—", AI_POST[app.ai_post()][:40])
        pr = app.ai_params()
        P("  采样参数         ", " ".join(f"{k}={pr[k]}" for k in
                                        ("temperature", "top_p", "max_tokens",
                                         "presence_penalty", "frequency_penalty")
                                        if pr.get(k) is not None and pr.get(k) != "")
          or "（全空，用服务商默认）")
        P("  附加参数         ", (pr.get("extra") or "")[:120] or "（没填）")
        sp = app.cfg.get("sys_prompts") or {}
        for k, (label, const) in BUILTIN_SYS.items():
            t = app.sys_prompt(k)
            P("  骨架·" + label[:16].ljust(18),
              (f"用户改过，{len(t)} 字（出厂 {len(BUILTIN_SYS_TEXT[k])} 字）"
               if str(sp.get(k) or "").strip() else f"出厂原版，{len(t)} 字"))
        pmc = app.cfg.get("prompts") or {}
        for k in DEFAULT_PROMPTS:
            t = app.prompt(k)
            P("  动作·" + k.ljust(18),
              (f"用户改过，{len(t)} 字" if str(pmc.get(k) or "").strip() else f"出厂原版，{len(t)} 字"))

        # C2：用户会问「它到底替我开了什么」。这是信任问题，必须能自证，别只留在日志里。
        # （段号用 5.8 而不是 PLAN 写的 [6]：[6] 早就是运行日志了，重新编号会让老诊断包对不上。）
        P("\n[5.8] 自动点开新帖")
        bc = app.br_cfg()
        P("  总开关          ", "开" if bc["auto_open"] else "关（默认；关着＝完全是老行为）")
        P("  上限            ", f"同时最多 {bc['max_tabs']} 个 · 每小时最多 {bc['per_hour']} 个 · "
                               + ("只开有规则在盯的父频道下面的" if bc["only_rule_channels"] else "所有新帖都开"))
        P("  闲置自动关      ", f"{bc['close_idle_min']} 分钟" if bc["close_idle_min"] else "不自动关")
        lim = app.tab_orders()["limits"]
        P("  现在开着        ", lim["open_now"], "个 | 这一小时已开", lim["opened_this_hour"], "个")
        for x in app.open_rows():
            last = app.thread_last(x["tid"], x["opened_at"])
            P("   - ", (x["name"] or x["tid"])[:36].ljust(38), "开了", ago_txt(x["opened_at"]),
              "| 最后一条消息", ago_txt(last) if last else "还没有", "| 桥", x["bridge"] or "-")
        recent = app.db.q("""SELECT * FROM threads_open ORDER BY COALESCE(opened_at,wanted) DESC
                             LIMIT 10""")
        if not recent:
            P("  （一个都没排过队。总开关关着就是这样）")
        for x in recent:
            P("   ·", (x["name"] or x["tid"])[:30].ljust(32),
              ("已关 " + time.strftime("%m-%d %H:%M", time.localtime(x["closed_at"]))) if x["closed_at"]
              else ("开着" if x["opened_at"] else f"排队中（试过 {x['tries'] or 0} 次）"),
              ("| 失败：" + x["err"][:60]) if x["err"] else "")

        P("\n[5.9] 开服监听")
        ws = app.watch_list()
        if not ws:
            P("  （一个监视目标都没加。要盯「某个服务开没开」去侧栏「开服监听」页加）")
        for w in ws:
            P("  ", f"[{w['id']}] " + (w["name"] or "?")[:30].ljust(32),
              {"open": "🟢 开", "closed": "🔴 关"}.get(w["state"], "？没探过"),
              "| 每", w["every_sec"], "秒",
              "| 上次探", ago_txt(w["last_check"]) if w["last_check"] else "还没",
              "| HTTP", w["last_status"] or "-",
              ("| 错：" + w["err"][:50]) if w["err"] else "",
              "| 关" if not w["enabled"] else "")
            P("      ", w["url"][:90],
              ("| 含「" + w["expect"] + "」才算开") if w["expect"] else "",
              ("| 含「" + w["absent"] + "」就算关") if w["absent"] else "",
              "| 开了提醒" if w["notify_open"] else "",
              "| 关了也提醒" if w["notify_close"] else "")

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
        app.db.x("DELETE FROM aicheck WHERE ts < ?", (now() - days * 86400,))   # 跟消息同一个保留期
        app.db.x("DELETE FROM logs WHERE ts < ?", (now() - 3 * 86400,))
        # 半小时没心跳的桥就不再列出来（浏览器关了/扩展卸了）
        for bid in [k for k, v in app.bridges.items() if now() - v.get("last", 0) > 1800]:
            app.bridges.pop(bid, None)
        await asyncio.sleep(3600)


async def watch_loop(app: App):
    """开服监听（D3）的巡检：每 5 秒看一遍哪些目标到期了，到期的探一次。
    单个目标挂了只记日志，不耽误别的目标；整个循环不能死 —— 死了就是「再也不提醒」。"""
    while True:
        try:
            for w in app.watch_list():
                if not w["enabled"]:
                    continue
                due = int(w["every_sec"] or 60)
                if now() - (w["last_check"] or 0) < max(App.WATCH_MIN_EVERY, due):
                    continue
                await app.watch_run(w)
        except Exception as e:
            with contextlib.suppress(Exception):
                app.log("error", f"开服监听巡检出错: {e}")
        await asyncio.sleep(5)


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
        asyncio.create_task(watch_loop(app))               # D3 开服监听
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
