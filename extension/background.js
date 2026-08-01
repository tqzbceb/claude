/* dcwatch bridge 的后台脚本。两件事：
   1) 把内容脚本解析好的消息发给本机 dcwatch
      （为什么不让内容脚本自己发：它的请求算在 discord.com 名下，Chrome 会用
        CORS + 「公网页面访问本地网络」那套规则拦掉。后台脚本带 host_permissions 不受限。）
   2) 记录状态、刷图标角标，让你一眼看出通没通：
        绿 ON  = dcwatch 在跑，而且有 Discord 标签页在旁听
        橙 ?   = dcwatch 在跑，但没有标签页在旁听（多半是装完扩展没刷新 Discord 页面）
        红 !   = 连不上 dcwatch（没启动，或端口不对）
*/
const DEF_PORT = 8777;
const FRESH_MS = 90_000;          // 和服务端 /api/state 的 90 秒判定保持一致

const getPort = async () => (await chrome.storage.local.get("port")).port || DEF_PORT;

/* 桥身份：每个浏览器（更准确说每个浏览器 profile）一个稳定 id。
   多个浏览器都装了这个扩展时，dcwatch 靠它把它们分开列出来。 */
async function getBridge() {
  const k = await chrome.storage.local.get("bridge");
  if (k.bridge) return k.bridge;
  const id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random())).slice(0, 8);
  await chrome.storage.local.set({ bridge: id });
  return id;
}
function browserName() {
  const u = navigator.userAgent;
  if (/Edg\//.test(u)) return "Edge";
  if (/OPR\//.test(u)) return "Opera";
  if (/Brave/.test(u)) return "Brave";
  if (/Chrome\/(\d+)/.test(u)) return "Chrome " + RegExp.$1;
  return "浏览器";
}
/* 每次投递都带上：桥 id、扩展版本、浏览器、当前账号 —— 这样界面上能看清是谁在报 */
async function stamp(body) {
  const acc = (await chrome.storage.local.get("acc")).acc || {};
  return { ...body, bridge: await getBridge(), ver: chrome.runtime.getManifest().version,
           browser: browserName(),
           account: body.account || acc.name || "", account_id: body.account_id || acc.id || "" };
}
const getSt = async () => (await chrome.storage.local.get("st")).st || {};
async function setSt(patch) {
  const st = { ...(await getSt()), ...patch };
  await chrome.storage.local.set({ st });
  return st;
}

/* ---------- 往 dcwatch 投递 ---------- */
async function post(body) {
  const port = await getPort();
  const t0 = Date.now();
  try {
    const full = await stamp(body);
    if (full.account) await chrome.storage.local.set({ acc: { name: full.account, id: full.account_id } });
    const r = await fetch(`http://127.0.0.1:${port}/api/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(full),
    });
    const txt = await r.text().catch(() => "");
    if (!r.ok) throw new Error(`dcwatch 返回 ${r.status} ${txt.slice(0, 80)}`);
    const patch = { lastTabPing: t0, sendErr: "", lastOkAt: t0,
                    account: full.account || "", bridge: full.bridge, ver: full.ver };
    if (body.ping) patch.where = body.where || "";
    else {
      const n = (body.messages || [body]).length;
      patch.sent = ((await getSt()).sent || 0) + n;
      patch.lastMsgAt = t0;
    }
    await setSt(patch);
    badge();
    return { ok: true, status: r.status };
  } catch (e) {
    // 投递失败也说明标签页是活的，只是本机不通——这个区别对排查很关键
    await setSt({ lastTabPing: t0, sendErr: String((e && e.message) || e) });
    badge();
    return { ok: false, error: String((e && e.message) || e) };
  }
}

/* ---------- 体检：只 GET /api/state，不碰服务端的心跳时间 ---------- */
async function health() {
  const port = await getPort();
  try {
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), 4000);
    const r = await fetch(`http://127.0.0.1:${port}/api/state`, { cache: "no-store", signal: ctl.signal });
    clearTimeout(to);
    if (!r.ok) throw new Error("HTTP " + r.status);
    const j = await r.json();
    await setSt({
      serverOk: true, serverErr: "", checkedAt: Date.now(), port,
      ver: chrome.runtime.getManifest().version,
      srvLast: j.status?.browser?.last || 0,
      srvCount: j.status?.browser?.count || 0,
      srvOnline: j.status?.browser?.state === "online",
      srcOn: j.config?.sources?.browser !== false,
      mode: j.config?.discord?.mode || "",
      srvVer: j.env?.ver || "",
      bridges: (j.status?.browser?.bridges || []).length,
      rules: (j.rules || []).filter(x => x.enabled).length,
    });
  } catch (e) {
    const msg = String((e && e.name) === "AbortError" ? "超时没响应" : (e && e.message) || e);
    await setSt({ serverOk: false, serverErr: msg, checkedAt: Date.now(), port,
                  ver: chrome.runtime.getManifest().version });
  }
  return badge();
}

async function badge() {
  const st = await getSt();
  const tabFresh = Date.now() - (st.lastTabPing || 0) < FRESH_MS;
  let text, color, tip;
  if (!st.serverOk) {
    [text, color, tip] = ["!", "#c0392b", `连不上 dcwatch（${st.serverErr || "未检测"}）— 它启动了吗？端口 ${st.port || DEF_PORT} 对吗？`];
  } else if (!tabFresh) {
    [text, color, tip] = ["?", "#9a6a1f", "dcwatch 在跑，但没有 Discord 标签页在旁听 — 打开频道页并按 F5 刷新一次"];
  } else if (st.sendErr) {
    [text, color, tip] = ["!", "#c0392b", "投递失败：" + st.sendErr];
  } else {
    [text, color, tip] = ["ON", "#3f7d58",
      `正常旁听中${st.account ? "（" + st.account + "）" : ""}${st.where ? "：#" + st.where : ""}，已上报 ${st.sent || 0} 条`
      + (st.rescued ? `，救回过 ${st.rescued} 个被 Chrome 回收的标签页` : "")];
  }
  await chrome.action.setBadgeText({ text });
  await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setTitle({ title: "dcwatch — " + tip });
  return { ...st, tabFresh, badge: text, tip };
}

/* ---------- 兜底心跳：主动去 Discord 标签页把状态拉过来 ----------
   为什么需要它：内容脚本自己每 20 秒报一次心跳，但那是 setInterval，
   而 Chrome 会给隐藏标签页的定时器降频（切走后对齐到 1 秒，隐藏超过 5 分钟后
   直接变成每分钟才跑一次）。服务端 90 秒没收到心跳就判失联 —— 于是你把
   Discord 丢在后台盯着，界面上却写「失联」，看着像扩展坏了。
   chrome.alarms 不受标签页可见性影响，所以由后台脚本兜这一手。
   只在「标签页确实超过 30 秒没报上来」时才拉，正常情况一个多余请求都不发。

   为什么不按 url 筛标签页：v1.11.0 之前这个扩展没有 "tabs" 权限，按 url 筛会
   吓用户一条「读取浏览记录」。v1.11.0 起为了自动开帖（C2）已经加了 tabs 权限，
   这里**可以**顺手改成 query({url:"*://discord.com/*"})—— 没改只是因为广播法
   实测无害（没有内容脚本的标签页 sendMessage 直接失败，吞掉就是），
   而心跳路径是全家当，不为省几次失败调用去动它。 */
async function pullTabs() {
  const st = await getSt();
  if (Date.now() - (st.lastTabPing || 0) < 30_000) return;   // 它自己报得上来，不打扰
  let tabs = [];
  try { tabs = await chrome.tabs.query({}); } catch (e) { return; }
  for (const t of tabs) {
    try {
      const body = await chrome.tabs.sendMessage(t.id, { type: "dcwatch-pull" });
      if (body && body.ping) await post(body);
    } catch (e) { /* 这个标签页里没有内容脚本（装扩展前就开着，没按 F5）——角标的「?」会说明 */ }
  }
}

/* ---------- 后台标签页防冻结/防回收（C6） ----------
   旁听全靠 Discord 页面活着。后台标签页本来就能继续收消息（MutationObserver
   不受可见性影响），真正会断的是 Chrome「内存节省程序」：标签页挂后台久了先
   **冻结**（页面 JS 全停，websocket 都断，这段消息全漏），再**回收**（页面整个
   卸载，切回去才重新加载）。用户看到的症状就是「窗口必须放前台才更新」。
   对策两层：
   1) 每个 Discord 频道标签页都设 autoDiscardable=false —— Chrome 官方给的唯一
      开关，明确告诉内存节省程序「这个标签页不许动」（冻结/回收都跳过它）。
   2) 每 30 秒巡检：发现已经被回收/冻结的（比如设置生效前就被收了的），立刻
      reload 救回来 —— 被回收的标签页本来就没有内容可失，reload 是恢复旁听，
      不是打扰；它会在后台安静加载，不抢焦点。 */
const isDiscordTab = t => !!(t && t.url && /^https:\/\/(ptb\.)?discord\.com\/channels\//.test(t.url));
async function protectTab(id) {
  try { await chrome.tabs.update(id, { autoDiscardable: false }); } catch (e) {}
}
async function protectAll() {
  let tabs = [];
  try { tabs = await chrome.tabs.query({ url: ["*://discord.com/channels/*", "*://ptb.discord.com/channels/*"] }); }
  catch (e) { return; }
  let rescued = 0;
  for (const t of tabs) {
    await protectTab(t.id);
    if (t.discarded || t.frozen) {
      try { await chrome.tabs.reload(t.id); rescued++; } catch (e) {}
    }
  }
  if (rescued) {
    const st = await getSt();
    await setSt({ rescued: (st.rescued || 0) + rescued, lastRescueAt: Date.now() });
  }
}
chrome.tabs.onCreated.addListener(t => { if (isDiscordTab(t)) protectTab(t.id); });
chrome.tabs.onUpdated.addListener((id, info, t) => { if (info.status === "loading" && isDiscordTab(t)) protectTab(id); });

/* ---------- 自动开帖：跟心跳一起每 30 秒问一次「有什么要我干的」（C2） ----------
   开哪些 / 关哪些全由服务端决定（它才知道规则在盯哪些频道、同时开着几个、
   一小时开了几个），扩展只执行 + 回报。「现在到底开着哪些」这个事实只认
   扩展的回报 —— 用户手动关掉标签页，服务端自己猜不到（PLAN_C2 第 2 节）。
   本地存的 opened 对照表（tid → tabId）有两个用处：扩展重载后不重复开；
   你手动关掉程序开的标签页时，立刻认出来并告诉服务端把名额腾出来。 */
const getOpened = async () => (await chrome.storage.local.get("opened")).opened || {};
const setOpened = m => chrome.storage.local.set({ opened: m });

/* D2：开帖用「新开一个最小化窗口」，不在当前窗口里加标签页。
   为什么：用户实测挂在当前窗口的后台标签页里 Discord 不更新（消息漏收），
   独立窗口哪怕最小化也会照常加载；而且一个窗口就一个帖子，你（或程序）关掉
   那唯一的标签页时窗口跟着一起没 —— 所以对照表照旧只记 tabId，
   关帖 / 手动关的清理路径一行都不用改。
   state:"minimized" 在个别平台/旧版本上会抛错 → 退回老路 tabs.create(active:false)，
   开帖这件事本身不能因此断掉。建完顺手给它上 C6 的防回收保护。 */
async function openPostTab(url) {
  try {
    const w = await chrome.windows.create({ url, state: "minimized", focused: false });
    const t = w && w.tabs && w.tabs[0];
    if (t && t.id != null) { await protectTab(t.id); return t; }
    throw new Error("窗口建好了但没拿到标签页");
  } catch (e) {
    const t = await chrome.tabs.create({ url, active: false, pinned: false });
    await protectTab(t.id);
    return t;
  }
}

async function tabOrders() {
  const port = await getPort();
  let j = null;
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/ext/hb`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(await stamp({})),      // 带 bridge + ver：服务端按它选「谁来执行」
    });
    if (r.ok) j = await r.json().catch(() => null);
    // 老版本程序（< v1.10.0）没有这个接口 → 404，安静跳过，什么都不会开
  } catch (e) { return; }                          // 程序没起：角标的「!」已经在说这件事了
  if (!j || !j.ok) return;
  const lim = j.limits || {};
  await setSt({ aoOn: !!lim.auto_open, aoNow: lim.open_now || 0 });   // popup 读这个，不自己再请求
  const opened = await getOpened();
  const rep = { opened: [], closed: [], failed: [] };

  // 先关后开：关掉才腾得出位置。一次最多各 3 条（服务端也是这么给的，这里再保一道）
  for (const c of (j.close || []).slice(0, 3)) {
    try {
      let tabId = opened[c.tid], alive = false;
      if (tabId != null) {
        try { await chrome.tabs.get(tabId); alive = true; } catch (e) { delete opened[c.tid]; }
      }
      if (!alive) {
        // 对照表里没有（或记录过期）：按 URL 兜底找一遍再下手
        const tabs = await chrome.tabs.query({ url: "*://discord.com/*" });
        const hit = tabs.find(t => t.url && t.url.includes("/" + c.tid));
        if (hit) { await chrome.tabs.remove(hit.id); rep.closed.push(c.tid); }
        else rep.failed.push({ tid: c.tid, err: "标签页已经不在了（你手动关了？）", gone: true });
      } else {
        await chrome.tabs.remove(tabId);
        rep.closed.push(c.tid);
      }
      delete opened[c.tid];
    } catch (e) { rep.failed.push({ tid: c.tid, err: String((e && e.message) || e) }); }
  }

  for (const o of (j.open || []).slice(0, 3)) {
    try {
      if (opened[o.tid] != null) {
        // 本地记过这个帖子：标签页还活着就不重复开（服务端也会记，这是双保险）
        try { await chrome.tabs.get(opened[o.tid]); rep.opened.push(o.tid); continue; }
        catch (e) { delete opened[o.tid]; }
      }
      const t = await openPostTab(o.url);        // D2：新开最小化窗口，不在当前窗口堆标签页
      opened[o.tid] = t.id;
      rep.opened.push(o.tid);
    } catch (e) { rep.failed.push({ tid: o.tid, err: String((e && e.message) || e) }); }
  }

  await setOpened(opened);
  if (rep.opened.length || rep.closed.length || rep.failed.length) {
    try {
      const r2 = await fetch(`http://127.0.0.1:${port}/api/ext/tabs`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(await stamp({ ...rep })),
      });
      if (r2.ok) {
        const j2 = await r2.json().catch(() => null);
        if (j2 && j2.open_now != null) await setSt({ aoNow: j2.open_now });
      }
    } catch (e) { /* 程序刚好挂了：下一轮心跳还会来，状态服务端自己攒着 */ }
  }
}

/* 你手动关掉了程序开的标签页 → 立刻回报。不说的话服务端一直以为它还开着，
   「同时最多开几个」的名额就这样一点点漏光，最后看着像「自动开帖坏了」。 */
chrome.tabs.onRemoved.addListener(async (tabId) => {
  const opened = await getOpened();
  const tid = Object.keys(opened).find(k => opened[k] === tabId);
  if (!tid) return;
  delete opened[tid];
  await setOpened(opened);
  try {
    const port = await getPort();
    await fetch(`http://127.0.0.1:${port}/api/ext/tabs`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(await stamp({ closed: [tid] })),
    });
  } catch (e) { /* 程序不在就不在了：服务端那边闲置自关会兜底把这笔勾掉 */ }
});

/* ---------- 消息路由 ---------- */
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (!msg) return false;
  if (msg.type === "dcwatch") {                    // 内容脚本来的消息/心跳
    post(msg.payload).then(reply).catch(e => reply({ ok: false, error: String(e) }));
    return true;
  }
  if (msg.type === "dcwatch-status") {             // 弹窗要状态
    health().then(reply).catch(e => reply({ serverOk: false, serverErr: String(e) }));
    return true;
  }
  if (msg.type === "dcwatch-port") {               // 弹窗改端口
    chrome.storage.local.set({ port: msg.port }).then(health).then(reply);
    return true;
  }
  if (msg.type === "dcwatch-test") {               // 弹窗里的「发测试消息」
    post({ messages: [{
      msg_id: "t" + Date.now(), guild_id: "", channel_id: "0", channel_name: "dcwatch-自检",
      account: msg.account || "",
      author: "dcwatch 自检", author_id: "", is_bot: false,
      content: "这是扩展发的测试消息。看到它就说明「网页 → 扩展 → dcwatch → 规则 → 通知」整条路是通的。",
    }] }).then(reply).catch(e => reply({ ok: false, error: String(e) }));
    return true;
  }
  return false;
});

chrome.alarms.create("hc", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(a => { if (a.name === "hc") { protectAll(); pullTabs().finally(health); tabOrders(); } });
chrome.runtime.onStartup.addListener(() => { protectAll(); health(); tabOrders(); });
chrome.runtime.onInstalled.addListener(() => { protectAll(); health(); });
protectAll();
health();
