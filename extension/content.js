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

function flush() {
  flushTimer = null;
  const messages = queue.splice(0, 50);
  if (!messages.length) return;
  fetch(ENDPOINT, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  }).catch(() => { /* dcwatch 没开着就丢掉，不打扰页面 */ });
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

/* 心跳：让 dcwatch 界面能显示“浏览器旁听 在线” */
const ping = () => fetch(ENDPOINT, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ ping: true }),
}).catch(() => {});
ping();
setInterval(ping, 45000);

globalThis.__dcwatch = { parseLi, seen, queue };    // 调试用：控制台可手动验证解析
console.log("[dcwatch] bridge 已挂载，转发到", ENDPOINT);
