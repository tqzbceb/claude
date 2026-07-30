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
    const r = await fetch(`http://127.0.0.1:${port}/api/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const txt = await r.text().catch(() => "");
    if (!r.ok) throw new Error(`dcwatch 返回 ${r.status} ${txt.slice(0, 80)}`);
    const patch = { lastTabPing: t0, sendErr: "", lastOkAt: t0 };
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
      srvLast: j.status?.browser?.last || 0,
      srvCount: j.status?.browser?.count || 0,
      srvOnline: j.status?.browser?.state === "online",
      srcOn: j.config?.sources?.browser !== false,
      mode: j.config?.discord?.mode || "",
      rules: (j.rules || []).filter(x => x.enabled).length,
    });
  } catch (e) {
    const msg = String((e && e.name) === "AbortError" ? "超时没响应" : (e && e.message) || e);
    await setSt({ serverOk: false, serverErr: msg, checkedAt: Date.now(), port });
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
    [text, color, tip] = ["ON", "#3f7d58", `正常旁听中${st.where ? "：#" + st.where : ""}，已上报 ${st.sent || 0} 条`];
  }
  await chrome.action.setBadgeText({ text });
  await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setTitle({ title: "dcwatch — " + tip });
  return { ...st, tabFresh, badge: text, tip };
}

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
      author: "dcwatch 自检", author_id: "", is_bot: false,
      content: "这是扩展发的测试消息。看到它就说明「网页 → 扩展 → dcwatch → 规则 → 通知」整条路是通的。",
    }] }).then(reply).catch(e => reply({ ok: false, error: String(e) }));
    return true;
  }
  return false;
});

chrome.alarms.create("hc", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(a => { if (a.name === "hc") health(); });
chrome.runtime.onStartup.addListener(health);
chrome.runtime.onInstalled.addListener(health);
health();
