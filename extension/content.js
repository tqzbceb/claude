/* dcwatch bridge — 旁听 discord.com 网页版，把新消息转发到本机 dcwatch。
   只读 DOM，不改页面、不发请求给 Discord、不碰你的 token。
   端口和 dcwatch 的 --port 一致，默认 8777。 */
const ENDPOINT = "http://127.0.0.1:8777/api/ingest";
const BOOT_QUIET_MS = 4000;   // 刚打开/刚切频道时已有的历史消息不算“新消息”
const MSG_MAX_AGE_MS = 3 * 60 * 1000;  // 消息自己的时间戳超过这么久 = 历史，不上报
const RENDER_BURST = 8;       // 一批冒出这么多条才怀疑是渲染/回填（还要看消息本身新不新）
const FLOOD_MAX = 25;         // 一批里真·新消息超过这么多 = 不正常，宁可不报（防刷屏）
const DISCORD_EPOCH = 1420070400000;
/* 脚本注入时扩展还是活的，这时候把版本记下来；之后扩展被重载就取不到了 */
const EXT_VER = (() => { try { return chrome.runtime.getManifest().version; } catch (e) { return ""; } })();

/* Discord 的消息 ID（snowflake）高位就是毫秒时间戳。
   这是判断「新消息还是历史」最可靠的依据 —— 比「页面加载后 4 秒」靠谱得多：
   在侧栏点开子区、往上滚加载旧消息，URL 都不变，光靠时间窗一定会把历史当新消息灌出来。 */
function snowflakeMs(id) {
  try {
    const t = Number(BigInt(id) >> 22n) + DISCORD_EPOCH;
    return t > DISCORD_EPOCH && t < Date.now() + 60000 ? t : 0;
  } catch (e) { return 0; }
}

/* 自诊断计数：出问题时你能直接看到「扩展到底有没有看见这条消息、为什么没上报」 */
const stats = { parsed: 0, sent: 0, lastSentAt: 0,
  skip: { history: 0, dup: 0, notext: 0, render: 0, quiet: 0 }, recent: [] };
function note(what, why) {
  stats.recent.unshift({ t: Date.now(), what: String(what).slice(0, 60), why });
  if (stats.recent.length > 12) stats.recent.pop();
}

const seen = new Set();
let boot = Date.now();
let queue = [];
let flushTimer = null;
let lastPath = location.pathname;

const txt = el => (el ? (el.innerText || "").trim() : "");

/* 从 URL 拿 guild/channel：/channels/<guildId|@me>/<channelId> */
function fromUrl() {
  const p = location.pathname.split("/");
  return { guild_id: p[2] === "@me" ? "" : (p[2] || ""), channel_id: p[3] || "", dm: p[2] === "@me" };
}

/* 页面标题形如 "(3) #general | 服务器名" */
function titleChannelName() {
  const t = (document.title || "").replace(/^\(\d+\)\s*/, "").split("|")[0].trim();
  return t.replace(/^[#@]/, "") || "";
}

/* 当前登录的是哪个号 —— 左下角用户区。多号监控时 dcwatch 要能分开显示。
   名字来源按可靠度排：用户区里的用户名标签 → 头像 aria-label；
   id 从头像 URL /avatars/<id>/ 抠（用默认头像的号抠不到，留空不影响功能）。
   Discord 的 class 名会变，所以只用 [class*=] 模糊匹配 + 多个候选。 */
let accCache = { name: "", id: "", at: 0 };
function myAccount() {
  if (accCache.name && Date.now() - accCache.at < 60000) return accCache;
  const area = document.querySelector(
    'section[class*="panels"], section[aria-label*="User"], div[class*="panels-"]');
  let name = "", id = "";
  if (area) {
    for (const sel of ['[class*="nameTag"] [class*="hovered"]', '[class*="nameTag"]',
                       '[class*="panelTitle"]', 'div[class*="usernameContainer"]',
                       '[class*="accountProfileCard"] [class*="username"]']) {
      const v = txt(area.querySelector(sel)).split("\n")[0].trim();
      if (v) { name = v; break; }
    }
    const img = area.querySelector('img[src*="/avatars/"]');
    const m = img && img.getAttribute("src").match(/\/avatars\/(\d+)\//);
    if (m) id = m[1];
  }
  if (name) accCache = { name, id, at: Date.now() };
  return accCache.name ? accCache : { name, id, at: 0 };
}

/* 子区面板（右侧展开的 thread）标题 */
function threadPanelName() {
  const h = document.querySelector('[class*="threadSidebar"] [class*="title"], [aria-label*="Thread"] h1, [class*="threadSidebar"] h1');
  return txt(h).split("\n")[0] || "";
}

function parseLi(li) {
  const m = /^chat-messages-(\d+)-(\d+)$/.exec(li.id || "");
  if (!m) return null;
  const [, channel_id, msg_id] = m;
  const content = txt(li.querySelector('[id^="message-content-"]'));
  if (!content) return null;                        // 纯图片/嵌入，没文字就不上报

  /* Discord 会把同一人连发的消息折叠，作者只出现在这一组的第一条上：往前找 */
  let author = "", author_id = "", is_bot = false, node = li, hops = 0;
  while (node && hops++ < 40) {
    const u = node.querySelector('[id^="message-username-"]');
    if (u) {
      author = txt(u.querySelector('[class*="username"]') || u).split("\n")[0];
      is_bot = !!node.querySelector('[class*="botTag"], [class*="botTagVerified"]');
      const img = node.querySelector('img[src*="/avatars/"]');
      const am = img && /\/avatars\/(\d+)\//.exec(img.getAttribute("src") || "");
      if (am) author_id = am[1];
      break;
    }
    node = node.previousElementSibling;
  }

  const ms = snowflakeMs(msg_id);
  const u = fromUrl();
  const is_thread = !!(u.channel_id && channel_id !== u.channel_id);
  const name = is_thread ? (threadPanelName() || channel_id) : (titleChannelName() || channel_id);
  return {
    msg_id, channel_id, guild_id: u.guild_id,
    parent_id: is_thread ? u.channel_id : "",
    is_thread, channel_name: name,
    author, author_id, is_bot,
    content,
    ts: (ms || Date.now()) / 1000,      // 消息真正的时间，不是我们看到它的时间
    age_ms: ms ? Date.now() - ms : 0,
    is_dm: u.dm,
    mentions_me: /mentioned/i.test(li.className || ""),
    url: `https://discord.com/channels/${u.guild_id || "@me"}/${channel_id}/${msg_id}`,
  };
}

/* 交给后台脚本去发：内容脚本自己发会被 Chrome 按「discord.com 访问本地网络」拦。
   后台脚本不在时退回直接 fetch —— 两种情况会走到这里：
   1) 你在控制台手动调试（没有扩展环境）；
   2) 扩展被重载/更新/停用过，而这个 Discord 标签页没刷新 → 页面里跑的还是旧脚本，
      它跟扩展的连接已经断了（chrome.runtime.id 变成 undefined）。
   第 2 种最坑：还能直连转发，看着「像在工作」，但扩展那边的改动一点都不生效，
   界面上也会多出一个认不出浏览器/版本的桥。所以这里显式打上 stale_ctx 标记，
   让 dcwatch 的诊断能一眼说出「按 F5」。 */
let ctxDead = false;
function send(payload) {
  try {
    if (globalThis.chrome && chrome.runtime && chrome.runtime.id) {
      chrome.runtime.sendMessage({ type: "dcwatch", payload }, () => void chrome.runtime.lastError);
      return;
    }
    if (globalThis.chrome && chrome.runtime) ctxDead = true;   // 有 chrome.runtime 但没 id = 断开了
  } catch (e) { ctxDead = true; /* 扩展被重载时 sendMessage 会抛 */ }
  fetch(ENDPOINT, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(Object.assign({
      bridge: "page-direct", browser: ctxDead ? "页面直连（扩展需按 F5）" : "页面直连（控制台）",
      ver: EXT_VER, stale_ctx: ctxDead,
    }, payload)),
  }).catch(() => { /* dcwatch 没开着就丢掉，不打扰页面 */ });
}

function flush() {
  flushTimer = null;
  const messages = queue.splice(0, 50);
  if (!messages.length) return;
  const a = myAccount();
  send({ messages, account: a.name, account_id: a.id });
}

function enqueue(msg) {
  queue.push(msg);
  if (!flushTimer) flushTimer = setTimeout(flush, 600);
}

/* ---------- 新帖 / 新子区 ----------
   论坛频道里「有人发了新帖」根本不是一条消息，页面上不会出现 chat-messages-*，
   所以光盯消息永远等不到。帖子本身也有 snowflake ID，用它的时间戳判断是不是刚开的 ——
   这样即使选择器多认了一堆老帖子（切频道时会一次渲染几十个），也会被时间挡在外面。 */
const seenThreads = new Set();
const THREAD_MAX_AGE_MS = 10 * 60 * 1000;   // 帖子比消息宽松些：滚到才看得见，晚几分钟很正常

function threadIdOf(el) {
  const d = el.getAttribute && (el.getAttribute("data-item-id") || el.getAttribute("data-list-item-id"));
  if (d) {
    const m = /(\d{15,25})/.exec(d);
    if (m) return m[1];
  }
  const a = el.matches && el.matches('a[href*="/channels/"]') ? el : (el.querySelector
    ? el.querySelector('a[href*="/channels/"]') : null);
  if (a) {
    const m = /\/channels\/[^/]+\/(\d{15,25})/.exec(a.getAttribute("href") || "");
    if (m) return m[1];
  }
  return "";
}

function threadTitleOf(el, id) {
  for (const sel of ['[class*="titleText"]', '[class*="postTitle"]', 'h3', '[class*="name"]']) {
    const v = txt(el.querySelector && el.querySelector(sel)).split("\n")[0].trim();
    if (v) return v.slice(0, 120);
  }
  return (txt(el).split("\n")[0] || "").trim().slice(0, 120) || id;
}

function scanThreads(nodes) {
  const u = fromUrl();
  for (const n of nodes) {
    if (n.nodeType !== 1) continue;
    const cands = [];
    if (threadIdOf(n)) cands.push(n);
    if (n.querySelectorAll) {
      n.querySelectorAll('[data-item-id],[data-list-item-id],a[href*="/channels/"]')
        .forEach(x => cands.push(x));
    }
    for (const el of cands) {
      const id = threadIdOf(el);
      if (!id || id === u.channel_id || seenThreads.has(id)) continue;
      seenThreads.add(id);
      if (seenThreads.size > 4000) seenThreads.clear();
      const ms = snowflakeMs(id);
      if (!ms) continue;                                  // 不是 snowflake，多半不是帖子
      if (Date.now() - ms > THREAD_MAX_AGE_MS) continue;  // 老帖子，只是被渲染出来而已
      const title = threadTitleOf(el, id);
      enqueue({
        kind: "thread",
        msg_id: "t" + id, channel_id: id, guild_id: u.guild_id,
        parent_id: u.channel_id, is_thread: true,
        channel_name: title,
        author: "", author_id: "", is_bot: false,
        content: "【新帖】" + title,
        ts: ms / 1000, age_ms: Date.now() - ms,
        is_dm: u.dm, mentions_me: false,
        url: `https://discord.com/channels/${u.guild_id || "@me"}/${id}`,
      });
      stats.parsed++; stats.sent++; stats.lastSentAt = Date.now();
      note("新帖：" + title, "已上报");
    }
  }
}

/* ---------- 抓历史 ----------
   平时故意不报历史消息（不然一切频道就刷屏）。但「把这个帖子从头翻一遍，
   让 AI 把里面的密钥/链接挑出来」是另一回事，得能主动抓。
   抓来的消息带 history 标记：只入库，不触发规则、不弹通知。 */
let scanning = false;
async function scanHistory(want = 300, onProgress = () => {}) {
  if (scanning) return { ok: false, error: "已经在抓了" };
  scanning = true;
  const got = new Map();
  try {
    const lis = () => [...document.querySelectorAll('li[id^="chat-messages-"]')];
    let scroller = null;
    for (const el of document.querySelectorAll('[class*="scroller"]')) {
      if (el.querySelector('li[id^="chat-messages-"]')) { scroller = el; break; }
    }
    if (!scroller) return { ok: false, error: "没找到消息区，先点开一个频道或帖子" };
    let idle = 0;
    for (let round = 0; round < 120 && got.size < want && idle < 4; round++) {
      const before = got.size;
      for (const li of lis()) {
        const m = parseLi(li);
        if (m) got.set(m.msg_id, m);
      }
      onProgress(got.size);
      if (got.size === before) idle++; else idle = 0;
      const top = scroller.scrollTop;
      scroller.scrollTop = Math.max(0, top - scroller.clientHeight * 0.9);
      if (scroller.scrollTop === top && top === 0) idle++;   // 已经到顶了
      await new Promise(r => setTimeout(r, 700));
    }
    const all = [...got.values()].sort((a, b) => a.ts - b.ts).slice(-want);
    const a = myAccount();
    for (let i = 0; i < all.length; i += 50) {
      send({ messages: all.slice(i, i + 50), account: a.name, account_id: a.id, history: true });
      await new Promise(r => setTimeout(r, 250));
    }
    note("抓历史 " + all.length + " 条", "已上报（只入库不提醒）");
    return { ok: true, n: all.length };
  } finally {
    scanning = false;
  }
}

const observer = new MutationObserver(muts => {
  if (location.pathname !== lastPath) { lastPath = location.pathname; boot = Date.now(); }
  const fresh = Date.now() - boot > BOOT_QUIET_MS;
  const lis = [];
  const added = [];
  for (const mu of muts) {
    for (const n of mu.addedNodes) {
      if (n.nodeType !== 1) continue;
      added.push(n);
      if (n.tagName === "LI" && n.id.startsWith("chat-messages-")) lis.push(n);
      else if (n.querySelectorAll) n.querySelectorAll('li[id^="chat-messages-"]').forEach(x => lis.push(x));
    }
  }
  if (fresh && !scanning) scanThreads(added);    // 新帖：跟消息是两条独立的路
  if (!lis.length) return;
  if (scanning) return;                          // 抓历史时别把翻出来的旧消息当新消息报

  /* 一批冒出一大堆，通常是 Discord 在渲染（切频道、点开子区面板、往上滚加载旧消息）。
     但「真的一下来了十几条」在活跃频道里也会发生 —— 开奖、发码、抢名额的时候恰恰就是刷屏，
     以前一律按渲染整批丢掉，等于最该提醒的时刻反而全漏（诊断包里会看到
     「解析 22 条 → 上报 0 条，整批渲染 22」）。
     所以改成看消息自己的时间戳：只有这一批里绝大多数是旧消息（或者认不出时间）才算渲染回填。
     真·新消息多到 FLOOD_MAX 以上仍然不报 —— 那种量级不是人在聊天。
     认成渲染时顺手把静默期重新计时：侧栏点开子区时 URL 不变，只能靠这个认出来。 */
  const stampOf = id => snowflakeMs((/-(\d+)$/.exec(id) || ["", ""])[1]);
  const stale = lis.filter(li => {
    const t = stampOf(li.id);
    return !t || Date.now() - t > MSG_MAX_AGE_MS;
  }).length;
  const brandNew = lis.length - stale;
  const render = lis.length >= RENDER_BURST && (stale >= lis.length * 0.6 || brandNew > FLOOD_MAX);
  if (render) boot = Date.now();

  for (const li of lis) {
    const id = li.id;
    if (seen.has(id)) { stats.skip.dup++; continue; }
    seen.add(id);
    if (seen.size > 4000) seen.clear();

    const msg = parseLi(li);
    if (!msg) { stats.skip.notext++; continue; }     // 纯图片/贴纸/嵌入，没文字
    stats.parsed++;

    if (render) { stats.skip.render++; note(msg.content, "整批渲染（切频道/点开子区/往上滚），当历史跳过"); continue; }
    if (msg.age_ms > MSG_MAX_AGE_MS) {
      stats.skip.history++;
      note(msg.content, `历史消息（${Math.round(msg.age_ms / 60000)} 分钟前发的）`); continue;
    }
    if (!fresh) { stats.skip.quiet++; note(msg.content, "刚打开页面的头几秒，当历史跳过"); continue; }

    enqueue(msg); stats.sent++; stats.lastSentAt = Date.now();
    note(msg.content, "已上报");
  }
});

/* 先把当前已渲染的消息记为“见过” */
document.querySelectorAll('li[id^="chat-messages-"]').forEach(li => seen.add(li.id));
observer.observe(document.body, { childList: true, subtree: true });

/* 心跳：让 dcwatch 界面左下角亮绿灯（服务端判定是 90 秒内有心跳），
   顺手把当前频道名带上，扩展图标的提示里会显示「正在旁听 #xxx」。
   20 秒一次，比 90 秒的判定留足余量，切频道后状态也跟得上。 */
const ping = () => {
  const a = myAccount();
  send({ ping: true, where: titleChannelName(), account: a.name, account_id: a.id,
    // 扩展这边看到的实情：解析了多少、上报了多少、因为什么跳过。
    // 服务端存起来，出问题时「导出诊断」里就有，不用你去点药丸截图。
    stats: { parsed: stats.parsed, sent: stats.sent, skip: stats.skip,
             recent: stats.recent.slice(0, 6),
             url: location.pathname, dm: fromUrl().dm,
             lis: document.querySelectorAll('li[id^="chat-messages-"]').length } });
};
ping();
setInterval(ping, 20000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) ping(); });

/* ================= 页面右下角的状态药丸 =================
   为什么放在页面里：状态挂在浏览器工具栏图标上，图标没固定出来就等于没有。
   放在 Discord 页面里，你一眼就能看到连没连上。用 shadow DOM 隔离，
   不会被 Discord 的样式影响，也不会影响它。 */
const HIDE_KEY = "dcwatch_pill_hidden";

function askStatus() {
  return new Promise(res => {
    try {
      if (globalThis.chrome && chrome.runtime && chrome.runtime.id) {
        chrome.runtime.sendMessage({ type: "dcwatch-status" }, r => {
          void chrome.runtime.lastError; res(r || null);
        });
        return;
      }
      if (globalThis.chrome && chrome.runtime) ctxDead = true;   // 扩展重载了，本页没刷新
    } catch (e) { ctxDead = true; }
    res(null);
  });
}
const tell = (type, extra) => new Promise(res => {
  try {
    chrome.runtime.sendMessage({ type, ...extra }, r => { void chrome.runtime.lastError; res(r); });
  } catch (e) { res(null); }
});

const CSS = `
  :host{all:initial}
  .w{position:fixed;right:14px;bottom:14px;z-index:2147483000;
     font:12.5px/1.5 "Segoe UI","Microsoft YaHei",system-ui,sans-serif;color:#1f1e1c}
  .pill{display:flex;align-items:center;gap:7px;background:#faf9f5;border:1px solid #d9d4c8;
        border-radius:999px;padding:5px 11px 5px 9px;box-shadow:0 2px 10px rgba(0,0,0,.18);cursor:pointer;
        user-select:none}
  .pill:hover{border-color:#c96442}
  .dot{width:9px;height:9px;border-radius:50%;background:#b9b4a8;flex:0 0 9px}
  .ok{background:#3f7d58} .warn{background:#c98a2a} .bad{background:#c0392b}
  .nm{font-weight:600}
  .st{color:#6b665d}
  .card{width:250px;background:#faf9f5;border:1px solid #d9d4c8;border-radius:10px;
        box-shadow:0 6px 24px rgba(0,0,0,.22);padding:11px 12px;margin-bottom:8px}
  .card h4{margin:0 0 6px;font-size:13px;display:flex;align-items:center;gap:6px}
  .card{width:330px}
  .row{display:flex;justify-content:space-between;gap:8px;padding:3px 0;color:#4a463f}
  .row b{font-weight:600}
  .hint{margin:7px 0 0;padding:7px 8px;border-radius:6px;background:#fdf3ee;border:1px solid #f0d5c8;
        font-size:12px;line-height:1.55}
  .hint.g{background:#f4f6f2;border-color:#d8e0d2}
  .btns{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}
  button{font:inherit;font-size:12px;padding:4px 8px;border-radius:6px;border:1px solid #d9d4c8;
         background:#fff;cursor:pointer;color:#1f1e1c}
  button:hover{border-color:#c96442;color:#c96442}
  .x{margin-left:auto;border:0;background:none;color:#6b665d;font-size:14px;padding:0 2px;cursor:pointer}
  .diag{margin-top:8px;padding-top:7px;border-top:1px solid #eae6dc}
  .lst{margin-top:5px;display:grid;gap:2px}
  .ln{display:flex;gap:6px;font-size:11.5px;line-height:1.45}
  .ln .w{flex:1;color:#4a463f;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ln .y{flex:none;color:#9a6a1f}
  .ln .y.g{color:#3f7d58}
  .tip{margin-top:6px;font-size:11px;line-height:1.5;color:#6b665d}
`;

let shadow = null, openPanel = false, lastSt = null;

function mountUI() {
  if (shadow || localStorage.getItem(HIDE_KEY) === "1") return;
  const host = document.createElement("div");
  host.id = "dcwatch-pill";
  shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `<style>${CSS}</style><div class="w"><div class="panel"></div>
    <div class="pill"><span class="dot"></span><span class="nm">dcwatch</span><span class="st">检测中</span></div></div>`;
  shadow.querySelector(".pill").addEventListener("click", () => { openPanel = !openPanel; paint(); });
  document.documentElement.appendChild(host);
}

function judge(st) {
  if (ctxDead) return ["bad", "按 F5 刷新本页",
    "扩展刚更新过（或被重载/停用），这个页面里跑的还是旧脚本，已经跟扩展断开了。" +
    "现在虽然还能直连转发消息，但扩展这边的改动一点都不生效 —— 在这个页面按一次 F5 就好。"];
  if (!st) return ["bad", "扩展没响应", "后台脚本没起来。去 chrome://extensions 点一下这个扩展的刷新按钮，再刷新本页。"];
  if (!st.serverOk) return ["bad", "连不上程序",
    `连不上 dcwatch（${st.serverErr || "?"}）。它启动了吗？端口 ${st.port || 8777} 对吗？`];
  if (st.sendErr) return ["bad", "投递失败", "投递失败：" + st.sendErr];
  if (!st.srcOn) return ["warn", "开关关着", "dcwatch 界面左下角，把「浏览器旁听」的开关打开。"];
  return ["ok", "正在旁听", `盯着 #${st.where || titleChannelName() || "?"}，有新消息就会转给 dcwatch。历史消息不上报。`];
}

const esc2 = t => String(t == null ? "" : t)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
function skipText() {
  const k = stats.skip, out = [];
  if (k.history) out.push(`历史 ${k.history}`);
  if (k.render) out.push(`整批渲染 ${k.render}`);
  if (k.quiet) out.push(`刚打开 ${k.quiet}`);
  if (k.notext) out.push(`无文字 ${k.notext}`);
  if (k.dup) out.push(`重复 ${k.dup}`);
  return out.length ? out.join("，") : "没有";
}

function paint() {
  if (!shadow) return;
  const st = lastSt;
  const [cls, short, hint] = judge(st);
  shadow.querySelector(".dot").className = "dot " + cls;
  shadow.querySelector(".st").textContent = short;
  const p = shadow.querySelector(".panel");
  if (!openPanel) { p.innerHTML = ""; return; }
  const ago = t => !t ? "还没有" : (Math.round((Date.now() - t) / 1000) < 60
    ? Math.round((Date.now() - t) / 1000) + " 秒前" : Math.round((Date.now() - t) / 60000) + " 分钟前");
  p.innerHTML = `<div class="card">
      <h4><span class="dot ${cls}"></span>dcwatch ${short}<button class="x" title="收起">×</button></h4>
      <div class="row"><span>本机程序</span><b>${st && st.serverOk ? "在跑 :" + (st.port || 8777) : "连不上"}</b></div>
      <div class="row"><span>当前账号</span><b>${myAccount().name || "认不出（不影响）"}</b></div>
      <div class="row"><span>本页在盯</span><b>#${(st && st.where) || titleChannelName() || "?"}</b></div>
      <div class="row"><span>已上报</span><b>${(st && st.sent) || 0} 条${st && st.lastMsgAt ? "（" + ago(st.lastMsgAt) + "）" : ""}</b></div>
      <div class="row"><span>服务端累计</span><b>${(st && st.srvCount) || 0} 条</b></div>
      <div class="hint ${cls === "ok" ? "g" : ""}">${hint}</div>
      <div class="diag">
        <div class="row"><span>本页解析到</span><b>${stats.parsed} 条 → 上报 ${stats.sent} 条</b></div>
        <div class="row"><span>跳过</span><b>${skipText()}</b></div>
        ${stats.recent.length ? `<div class="lst">${stats.recent.slice(0, 6).map(r =>
          `<div class="ln"><span class="w">${esc2(r.what) || "（无文字）"}</span>
            <span class="y ${r.why === "已上报" ? "g" : ""}">${esc2(r.why)}</span></div>`).join("")}</div>` : ""}
        <div class="tip">发了消息但这里没出现？说明扩展根本没看见它 —— 检查这个标签页是不是正停在那个频道／帖子上。
        出现了写「已上报」但没提醒你？那是规则没命中，去 dcwatch 界面「监听规则」点试算。</div>
      </div>
      <div class="tip">要把这个帖子/频道**已经有的**内容交给 AI 翻一遍（比如挑出所有密钥），
        点下面的「抓历史」。抓来的只入库，不会弹通知。</div>
      <div class="btns">
        <button class="s">抓历史</button>
        <button class="t">发测试消息</button>
        <button class="o">打开界面</button>
        <button class="h">不再显示</button>
      </div></div>`;
  p.querySelector(".x").onclick = e => { e.stopPropagation(); openPanel = false; paint(); };
  p.querySelector(".t").onclick = async e => {
    e.target.textContent = "发送中…";
    const r = await tell("dcwatch-test", {});
    e.target.textContent = r && r.ok ? "已发送 ✓" : "失败";
    setTimeout(refreshUI, 600);
  };
  p.querySelector(".s").onclick = async e => {
    const btn = e.target;
    btn.disabled = true;
    btn.textContent = "往上翻…";
    const r = await scanHistory(300, n => { btn.textContent = "已抓 " + n + " 条…"; });
    btn.textContent = r.ok ? "抓完 " + r.n + " 条 ✓" : (r.error || "失败");
    setTimeout(() => { btn.disabled = false; refreshUI(); }, 2500);
  };
  p.querySelector(".o").onclick = () =>
    window.open(`http://127.0.0.1:${(st && st.port) || 8777}/`, "_blank");
  p.querySelector(".h").onclick = () => {
    localStorage.setItem(HIDE_KEY, "1");
    if (shadow) { shadow.host.remove(); shadow = null; }
  };
}

async function refreshUI() { lastSt = await askStatus(); paint(); }

mountUI();
refreshUI();
setInterval(() => { if (!document.hidden) refreshUI(); }, 10000);

globalThis.__dcwatch = { parseLi, seen, queue, refreshUI, stats, snowflakeMs,
  scanHistory, scanThreads, seenThreads, threadIdOf,
  show: () => { localStorage.removeItem(HIDE_KEY); mountUI(); refreshUI(); } };
console.log("[dcwatch] bridge 已挂载，转发到", ENDPOINT, "，右下角有状态药丸；隐藏了就在控制台跑 __dcwatch.show()");
