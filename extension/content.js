/* dcwatch bridge — 旁听 discord.com 网页版，把新消息转发到本机 dcwatch。
   只读 DOM，不改页面、不发请求给 Discord、不碰你的 token。
   端口和 dcwatch 的 --port 一致，默认 8777。 */
const ENDPOINT = "http://127.0.0.1:8777/api/ingest";
const BOOT_QUIET_MS = 4000;   // 刚打开/刚切频道时已有的历史消息不算“新消息”
const BURST_SKIP = 25;        // 一次涌入超过这么多条 = 切频道渲染，不上报

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

  const u = fromUrl();
  const is_thread = !!(u.channel_id && channel_id !== u.channel_id);
  const name = is_thread ? (threadPanelName() || channel_id) : (titleChannelName() || channel_id);
  return {
    msg_id, channel_id, guild_id: u.guild_id,
    parent_id: is_thread ? u.channel_id : "",
    is_thread, channel_name: name,
    author, author_id, is_bot,
    content,
    is_dm: u.dm,
    mentions_me: /mentioned/i.test(li.className || ""),
    url: `https://discord.com/channels/${u.guild_id || "@me"}/${channel_id}/${msg_id}`,
  };
}

/* 交给后台脚本去发：内容脚本自己发会被 Chrome 按「discord.com 访问本地网络」拦。
   后台脚本不在时（比如你在控制台手动调试）退回直接 fetch。 */
function send(payload) {
  try {
    if (globalThis.chrome && chrome.runtime && chrome.runtime.id) {
      chrome.runtime.sendMessage({ type: "dcwatch", payload }, () => void chrome.runtime.lastError);
      return;
    }
  } catch (e) { /* 扩展被重载时 sendMessage 会抛，忽略 */ }
  fetch(ENDPOINT, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
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

const observer = new MutationObserver(muts => {
  if (location.pathname !== lastPath) { lastPath = location.pathname; boot = Date.now(); }
  const fresh = Date.now() - boot > BOOT_QUIET_MS;
  const lis = [];
  for (const mu of muts) {
    for (const n of mu.addedNodes) {
      if (n.nodeType !== 1) continue;
      if (n.tagName === "LI" && n.id.startsWith("chat-messages-")) lis.push(n);
      else if (n.querySelectorAll) n.querySelectorAll('li[id^="chat-messages-"]').forEach(x => lis.push(x));
    }
  }
  if (!lis.length) return;
  const burst = lis.length > BURST_SKIP;
  for (const li of lis) {
    const id = li.id;
    if (seen.has(id)) continue;
    seen.add(id);
    if (seen.size > 4000) seen.clear();
    if (!fresh || burst) continue;                  // 只登记、不上报
    const msg = parseLi(li);
    if (msg) enqueue(msg);
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
  send({ ping: true, where: titleChannelName(), account: a.name, account_id: a.id });
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
    } catch (e) { /* 扩展刚重载 */ }
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
  if (!st) return ["bad", "扩展没响应", "后台脚本没起来。去 chrome://extensions 点一下这个扩展的刷新按钮，再刷新本页。"];
  if (!st.serverOk) return ["bad", "连不上程序",
    `连不上 dcwatch（${st.serverErr || "?"}）。它启动了吗？端口 ${st.port || 8777} 对吗？`];
  if (st.sendErr) return ["bad", "投递失败", "投递失败：" + st.sendErr];
  if (!st.srcOn) return ["warn", "开关关着", "dcwatch 界面左下角，把「浏览器旁听」的开关打开。"];
  return ["ok", "正在旁听", `盯着 #${st.where || titleChannelName() || "?"}，有新消息就会转给 dcwatch。历史消息不上报。`];
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
      <div class="btns">
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

globalThis.__dcwatch = { parseLi, seen, queue, refreshUI,
  show: () => { localStorage.removeItem(HIDE_KEY); mountUI(); refreshUI(); } };
console.log("[dcwatch] bridge 已挂载，转发到", ENDPOINT, "，右下角有状态药丸；隐藏了就在控制台跑 __dcwatch.show()");
