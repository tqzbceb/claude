/* content.js 的解析回归。不需要真装扩展。
   用法（browser_execute 里）：
     const m = await import(process.cwd()+"/tests/content_test.mjs?t="+Date.now())
     console.log(JSON.stringify(await m.run(session), null, 1))
   原理：换 realm → 造假 Discord DOM → replaceState 伪造 /channels/<g>/<c> →
   注入 chrome.runtime 桩 → 跑 content.js → 等过 4s 开机静默期 → 塞 li → 读 __sent。 */

const FAKE_PAGE = `<!doctype html><html><head><title>(3) #general | 我的服务器</title></head>
<body>
  <!-- 左下角用户区：myAccount() 从这里认当前登录的号 -->
  <section class="panels-abc panels">
    <img src="https://cdn.discordapp.com/avatars/777000111/abc.png">
    <div class="nameTag-xyz"><span class="hovered-1">alice#1234</span></div>
  </section>
  <div id="thread-panel" class="threadSidebar-1" style="display:none"><h1>登录 500</h1></div>
  <ol id="feed"></ol>
</body></html>`;

const MK = {
  /* 一条普通消息（自带作者头） */
  normal: (chan, mid, name, avatarId, text, opts = {}) => `
    <li id="chat-messages-${chan}-${mid}" class="${opts.mention ? "mentioned-1" : ""}">
      <img src="https://cdn.discordapp.com/avatars/${avatarId}/x.png">
      <h3 id="message-username-${mid}"><span class="username-1">${name}</span>
        ${opts.bot ? '<span class="botTag-1">应用</span>' : ""}</h3>
      <div id="message-content-${mid}">${text}</div>
    </li>`,
  /* 折叠消息：没有作者头，作者要往前一条继承 */
  folded: (chan, mid, text) => `
    <li id="chat-messages-${chan}-${mid}">
      <div id="message-content-${mid}">${text}</div>
    </li>`,
  /* 纯图片：没有 message-content，不该上报 */
  imageOnly: (chan, mid) => `
    <li id="chat-messages-${chan}-${mid}"><img src="https://cdn.discordapp.com/x.png"></li>`,
  /* v1.8.0：没有文字但有附件的消息，要翻译成 [图片] / [贴纸 x] / :emoji: 上报，
     不能再当「空消息」丢掉 —— 否则「所有消息都提醒我」永远收不到表情包 */
  media: (chan, mid, name, inner, opts = {}) => `
    <li id="chat-messages-${chan}-${mid}">
      <img src="https://cdn.discordapp.com/avatars/444100000000009/x.png">
      <h3 id="message-username-${mid}"><span class="username-1">${name}</span></h3>
      ${opts.noBody ? inner : `<div id="message-content-${mid}">${inner}</div>`}
    </li>`,
  /* 默认头像：抠不到 author_id */
  defaultAvatar: (chan, mid, name, text) => `
    <li id="chat-messages-${chan}-${mid}">
      <img src="https://cdn.discordapp.com/embed/avatars/3.png">
      <h3 id="message-username-${mid}"><span class="username-1">${name}</span></h3>
      <div id="message-content-${mid}">${text}</div>
    </li>`,
};

/* Discord snowflake：((ms - 1420070400000) << 22)。造「几分钟前」的历史消息用它 */
const EPOCH = 1420070400000n;
export function sf(msAgo = 0) {
  return String((BigInt(Date.now() - msAgo) - EPOCH) << 22n);
}

const GUILD = "900000000000000001";
const CHAN = "800000000000000002";
const THREAD = "700000000000000003";

export async function run(session, contentJsPath) {
  const fs = await import("fs/promises");
  const path = contentJsPath || process.env.DCW_CONTENT_JS ||
    new URL("../extension/content.js", import.meta.url).pathname;
  const code = await fs.readFile(path, "utf8");

  const tid = (await session.Target.getTargets({})).targetInfos
    .find(t => t.type === "page" && !t.url.startsWith("chrome://")).targetId;
  await session.use(tid);
  await session.Page.enable();
  await session.Page.bringToFront();   // 后台标签页定时器被节流，合批会迟到
  await session.Runtime.enable();

  // 1) 先做纯语法检查：语法错的话下面全是废话
  const loaded = session.waitFor("Page.loadEventFired"); loaded.catch(() => {});
  await session.Page.navigate({ url: "https://usercontent.browser-use.tools" });
  await Promise.race([loaded, new Promise(r => setTimeout(r, 8000))]);
  const comp = await session.Runtime.compileScript({
    expression: code, sourceURL: "content.js", persistScript: false,
  });
  if (comp.exceptionDetails) {
    return { syntax: "FAIL", error: comp.exceptionDetails.text +
      " @" + JSON.stringify(comp.exceptionDetails.lineNumber) };
  }

  // 2) 造假页面 + chrome 桩，跑 content.js
  await session.Page.setDocumentContent({ frameId: tid, html: FAKE_PAGE });
  await session.Runtime.evaluate({ expression: `
    history.replaceState({}, "", "/channels/${GUILD}/${CHAN}");
    window.__sent = [];
    window.chrome = { runtime: {
      id: "stub-ext-id",
      getManifest: () => ({ version: "1.4.0" }),
      sendMessage: (m, cb) => { window.__sent.push(m); if (cb) cb({ ok: true, st: {} }); },
      // 后台脚本每 30 秒来拉一次的兜底心跳：隐藏标签页的 setInterval 会被 Chrome
      // 降频到每分钟一次，逼近服务端 90 秒的失联判定。桩把监听器存下来好直接调。
      onMessage: { addListener: (fn) => { window.__onMsg = fn; } },
      lastError: null } };
    window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  ` });
  const ev = await session.Runtime.evaluate({ expression: code, awaitPromise: false });
  if (ev.exceptionDetails) return { syntax: "ok", boot: "FAIL", error: ev.exceptionDetails.text };

  // 3) 等过开机静默期（content.js 头 4 秒不上报历史消息）
  await new Promise(r => setTimeout(r, 4600));

  // 4) 塞消息，等合批（600ms）
  const push = async (html) => {
    await session.Runtime.evaluate({ expression:
      `document.getElementById("feed").insertAdjacentHTML("beforeend", ${JSON.stringify(html)})` });
    await new Promise(r => setTimeout(r, 900));
  };

  await push(MK.normal(CHAN, "1001", "Marcus", "444100000000001", "@我 明天提前到 10 点"));
  await push(MK.folded(CHAN, "1002", "还有一件事"));
  await push(MK.normal(CHAN, "1003", "CryptoBot", "100100000000001", "Snapshot 公告", { bot: true }));
  await push(MK.imageOnly(CHAN, "1004"));
  await push(MK.defaultAvatar(CHAN, "1005", "NoAvatar", "我用默认头像"));

  // v1.8.0：四种「没文字」的消息，都要变成看得懂的正文上报
  await push(MK.media(CHAN, "1010", "Pic", `<div class="imageWrapper-1">
    <img alt="截图.png" src="https://cdn.discordapp.com/attachments/1/2/a.png"></div>`, { noBody: true }));
  await push(MK.media(CHAN, "1011", "Stk", `<div class="clickableSticker-1">
    <img class="stickerAsset-1" alt="猫猫点头"></div>`, { noBody: true }));
  await push(MK.media(CHAN, "1012", "Emo", `<img class="emoji-1" alt=":kekw:">`));
  await push(MK.media(CHAN, "1013", "Fil", `<div class="attachment-1">
    <div class="filename-1">keys.txt</div></div>`, { noBody: true }));

  // 子区：li 的 chanId 和 URL 里的不一样 → parent_id 用 URL 的
  await session.Runtime.evaluate({ expression:
    `document.getElementById("thread-panel").style.display="block"` });
  await push(MK.normal(THREAD, "1006", "lily.dev", "882300000000001", "登录还是 500"));

  // 私信：URL 变成 /channels/@me/<c>。
  // 注意换路径会重新触发 4 秒静默期（切频道不该重报历史消息），所以要再等一次。
  await session.Runtime.evaluate({ expression:
    `history.replaceState({}, "", "/channels/@me/${CHAN}")` });
  await push(MK.imageOnly(CHAN, "9999"));      // 先踢一下 observer，让它认到新路径
  await new Promise(r => setTimeout(r, 4600));
  await push(MK.normal(CHAN, "1007", "Ana", "771200000000001", "私信一条"));

  // 重复塞同一条：应该被 seen 去重
  await push(MK.normal(CHAN, "1001", "Marcus", "444100000000001", "@我 明天提前到 10 点"));

  // 后台脚本的兜底心跳：模拟 chrome.tabs.sendMessage({type:"dcwatch-pull"})，
  // 内容脚本必须同步 reply 一份和自发心跳一样的 body
  const pull = await session.Runtime.evaluate({ expression: `(() => {
    if (!window.__onMsg) return JSON.stringify({ registered: false });
    let got = null;
    const ret = window.__onMsg({ type: "dcwatch-pull" }, {}, (b) => { got = b; });
    const ignored = window.__onMsg({ type: "什么别的消息" }, {}, () => {});
    return JSON.stringify({ registered: true, ret, body: got, ignored });
  })()`, returnByValue: true });
  const pl = JSON.parse(pull.result.value);

  const r = await session.Runtime.evaluate({ expression: `JSON.stringify({
    sent: window.__sent,
    hasPill: !!document.querySelector('#dcwatch-pill, [id*=dcwatch]') ||
             !!(window.__dcwatch && window.__dcwatch.show),
    api: Object.keys(window.__dcwatch || {}) })`, returnByValue: true });
  const out = JSON.parse(r.result.value);

  // ---- 断言 ----
  const msgs = [];
  for (const s of out.sent) {
    if (s.type === "dcwatch" && s.payload && s.payload.messages) msgs.push(...s.payload.messages);
  }
  const pings = out.sent.filter(s => s.type === "dcwatch" && s.payload && s.payload.ping);
  const batches = out.sent.filter(s => s.type === "dcwatch" && s.payload && s.payload.messages);
  const by = {}; for (const m of msgs) by[m.msg_id] = m;
  const checks = [];
  const t = (name, cond, extra) => checks.push({ name, ok: !!cond, ...(cond ? {} : { got: extra }) });

  t("上报了消息", msgs.length > 0, msgs.length);
  t("普通消息作者正确", by["1001"] && by["1001"].author === "Marcus", by["1001"]);
  t("头像 URL 抠出 author_id", by["1001"] && by["1001"].author_id === "444100000000001", by["1001"]);
  t("@我 被识别", by["1001"] && by["1001"].content.includes("@我"), by["1001"]);
  t("折叠消息继承前一条的作者", by["1002"] && by["1002"].author === "Marcus", by["1002"]);
  t("折叠消息也拿到 author_id", by["1002"] && by["1002"].author_id === "444100000000001", by["1002"]);
  t("bot 标记识别", by["1003"] && by["1003"].is_bot === true, by["1003"]);
  t("真的空消息（连附件都认不出）不上报", !by["1004"], by["1004"]);
  /* v1.8.0：表情包/图片/贴纸/文件也要上报，正文写成占位符。
     以前一律按「没有文字」丢掉，用户勾了「所有消息」也收不到表情包，只会以为规则坏了 */
  t("纯图片上报成 [图片]", by["1010"] && by["1010"].content.includes("[图片"), by["1010"]);
  t("图片带上文件名", by["1010"] && by["1010"].content.includes("截图.png"), by["1010"]);
  t("图片标出附件类型 image", by["1010"] && (by["1010"].media || []).includes("image"), by["1010"]);
  t("贴纸上报成 [贴纸 名字]", by["1011"] && by["1011"].content.includes("[贴纸 猫猫点头]"), by["1011"]);
  t("贴纸标出附件类型 sticker", by["1011"] && (by["1011"].media || []).includes("sticker"), by["1011"]);
  t("表情包上报成 :emoji:", by["1012"] && by["1012"].content.includes(":kekw:"), by["1012"]);
  t("附件消息的作者照样认得出", by["1012"] && by["1012"].author === "Emo", by["1012"]);
  t("文件上报成 [文件 名字]", by["1013"] && by["1013"].content.includes("keys.txt"), by["1013"]);
  t("纯文字消息的 media 是空的", by["1001"] && (by["1001"].media || []).length === 0, by["1001"]);
  t("默认头像 author_id 留空", by["1005"] && !by["1005"].author_id, by["1005"]);
  t("默认头像也有作者名", by["1005"] && by["1005"].author === "NoAvatar", by["1005"]);
  t("频道名取自标题", by["1001"] && by["1001"].channel_name === "general", by["1001"]);
  t("子区标成 is_thread", by["1006"] && by["1006"].is_thread === true, by["1006"]);
  t("子区 parent_id = URL 里的频道", by["1006"] && by["1006"].parent_id === CHAN, by["1006"]);
  t("子区名取自侧栏标题", by["1006"] && by["1006"].channel_name === "登录 500", by["1006"]);
  t("私信 is_dm", by["1007"] && by["1007"].is_dm === true, by["1007"]);
  t("私信 guild_id 为空", by["1007"] && by["1007"].guild_id === "", by["1007"]);
  t("切频道后重新静默：新路径下的历史不重报", !by["9999"], by["9999"]);
  t("重复消息被去重", msgs.filter(m => m.msg_id === "1001").length === 1,
    msgs.filter(m => m.msg_id === "1001").length);
  t("多条合批（批数 < 消息数）", batches.length <= msgs.length, { batches: batches.length, msgs: msgs.length });
  t("心跳发出去了", pings.length > 0, pings.length);
  t("心跳带账号", pings.length && pings[0].payload.account === "alice#1234", pings[0]);
  t("心跳带在盯的频道", pings.length && pings[0].payload.where === "general", pings[0]);
  /* 心跳要顺手把本页诊断带上，服务端的「导出诊断」才有扩展侧实情 */
  const hb = pings[pings.length - 1].payload.stats || {};
  t("心跳带诊断数据", typeof hb.parsed === "number" && typeof hb.sent === "number" && !!hb.skip, hb);
  t("诊断里有跳过分类和最近记录", hb.skip && "history" in hb.skip && "render" in hb.skip
    && Array.isArray(hb.recent), hb.skip);
  t("诊断带上页面路径和消息条数", typeof hb.lis === "number" && typeof hb.url === "string", hb);
  t("批次带账号（多号分流靠它）", batches.length && batches[0].payload.account === "alice#1234", batches[0]);
  t("批次带账号 id", batches.length && batches[0].payload.account_id === "777000111", batches[0]);
  /* 兜底心跳（v1.7.4）：后台脚本拉得到，而且拉到的和自发心跳是同一份内容。
     没有这条，隐藏久了的标签页会被 Chrome 降到每分钟一次定时器，界面写「失联」 */
  t("后台能拉心跳：监听器注册了", pl.registered, pl);
  t("拉心跳同步回了 body", pl.body && pl.body.ping === true, pl.body);
  t("拉到的心跳带频道和账号", pl.body && pl.body.where === "general"
    && pl.body.account === "alice#1234", pl.body);
  t("拉到的心跳也带诊断", pl.body && pl.body.stats && typeof pl.body.stats.parsed === "number", pl.body);
  t("别的消息类型不接管", pl.ignored === false, pl.ignored);
  t("页面药丸挂上了", out.hasPill, out.api);
  t("__dcwatch 调试接口在", out.api.includes("show") && out.api.includes("parseLi"), out.api);

  const fail = checks.filter(c => !c.ok);
  return { syntax: "ok", pass: checks.length - fail.length, fail: fail.length,
           failed: fail, msgIds: Object.keys(by) };
}

/* ============ 第二套：新旧判定（用户实际踩到的四个场景） ============
   帖子/子区在侧栏点开时 URL 不变，老版本靠「加载后 4 秒」判新旧，
   于是把整个帖子的历史当新消息灌出来（通知一条条弹 → 提示音卡成电音），
   而且一批超 25 条还会整批静默丢弃。现在改成看消息 ID 里的时间戳。 */
export async function runFresh(session, contentJsPath) {
  const fs = await import("fs/promises");
  const path = contentJsPath || process.env.DCW_CONTENT_JS ||
    new URL("../extension/content.js", import.meta.url).pathname;
  const code = await fs.readFile(path, "utf8");
  const tid = (await session.Target.getTargets({})).targetInfos
    .find(t => t.type === "page" && !t.url.startsWith("chrome://")).targetId;
  await session.use(tid);
  await session.Page.enable();
  await session.Page.bringToFront();          // 后台标签页定时器会被节流，合批就迟到

  const loaded = session.waitFor("Page.loadEventFired"); loaded.catch(() => {});
  await session.Page.navigate({ url: "https://usercontent.browser-use.tools" });
  await Promise.race([loaded, new Promise(r => setTimeout(r, 8000))]);
  await session.Page.setDocumentContent({ frameId: tid, html: FAKE_PAGE });
  await session.Runtime.evaluate({ expression: `
    history.replaceState({}, "", "/channels/${GUILD}/${CHAN}");
    window.__sent = [];
    window.chrome = { runtime: { id: "stub", getManifest: () => ({ version: "1.5.0" }),
      sendMessage: (m, cb) => { window.__sent.push(m); if (cb) cb({ ok: true, st: {} }); },
      lastError: null } };
    window.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });` });
  const ev = await session.Runtime.evaluate({ expression: code });
  if (ev.exceptionDetails) return { boot: "FAIL", error: ev.exceptionDetails.text };
  await new Promise(r => setTimeout(r, 4600));            // 过掉开机静默期

  const push = async (html, wait = 900) => {
    await session.Runtime.evaluate({ expression:
      `document.getElementById("feed").insertAdjacentHTML("beforeend", ${JSON.stringify(html)})` });
    await new Promise(r => setTimeout(r, wait));
  };
  const sentIds = async () => {
    const r = await session.Runtime.evaluate({ expression:
      `JSON.stringify(window.__sent.filter(s=>s.payload&&s.payload.messages)
         .flatMap(s=>s.payload.messages).map(m=>m.msg_id))`, returnByValue: true });
    return JSON.parse(r.result.value);
  };

  /* 「该上报」的断言一律轮询到超时才算失败：合批本来就有延迟，
     一次性读会随机读空（后台节流时尤其明显），那是测试的错不是代码的错 */
  const waitSent = async (id, ms = 4000) => {
    const dl = Date.now() + ms;
    while (Date.now() < dl) {
      if ((await sentIds()).includes(id)) return true;
      await new Promise(r => setTimeout(r, 250));
    }
    return false;
  };

  const checks = [];
  const t = (name, cond, extra) => checks.push({ name, ok: !!cond, ...(cond ? {} : { got: extra }) });

  // 1) 真正的新消息（刚发的）→ 必须上报
  const fresh1 = sf(2000);
  await push(MK.normal(CHAN, fresh1, "Marcus", "444100000000001", "刚发的新消息"));
  t("刚发的新消息会上报", await waitSent(fresh1), await sentIds());

  // 2) 20 分钟前的消息（在侧栏点开帖子时会整片出现）→ 不该上报
  const old1 = sf(20 * 60 * 1000);
  await push(MK.normal(CHAN, old1, "Marcus", "444100000000001", "20分钟前的旧内容"));
  t("20 分钟前的历史不上报", !(await sentIds()).includes(old1), await sentIds());

  // 3) 侧栏点开一个帖子：URL 不变，一次性冒出 12 条历史 → 一条都不该上报
  const hist = Array.from({ length: 12 }, (_, i) => sf((i + 3) * 60 * 1000));
  await session.Runtime.evaluate({ expression:
    `document.getElementById("thread-panel").style.display="block"` });
  await push(hist.map((id, i) => MK.normal(THREAD, id, "lily.dev", "882300000000001", "帖子历史" + i)).join(""), 1400);
  const after3 = await sentIds();
  t("侧栏点开帖子：整片历史一条都不上报", hist.every(id => !after3.includes(id)),
    hist.filter(id => after3.includes(id)).length + " 条漏出去了");

  // 4) 点开帖子之后，帖子里来一条真的新消息 → 必须上报（老版本这里就死了）
  await new Promise(r => setTimeout(r, 4600));            // 整批渲染会重新计时，等过去
  const fresh2 = sf(1000);
  await push(MK.normal(THREAD, fresh2, "lily.dev", "882300000000001", "帖子里的新消息"));
  t("点开帖子后，帖子里的新消息照样上报", await waitSent(fresh2), await sentIds());

  // 5) 往上滚加载 30 条旧消息 → 不上报，且不能因为「超过 25 条」就让后续瘫掉
  const back = Array.from({ length: 30 }, (_, i) => sf((i + 30) * 60 * 1000));
  await push(back.map((id, i) => MK.normal(CHAN, id, "Ana", "771200000000001", "回滚" + i)).join(""), 1500);
  const after5 = await sentIds();
  t("往上滚加载的 30 条旧消息不上报", back.every(id => !after5.includes(id)),
    back.filter(id => after5.includes(id)).length + " 条漏出去了");
  await new Promise(r => setTimeout(r, 4600));
  const fresh3 = sf(500);
  await push(MK.normal(CHAN, fresh3, "Ana", "771200000000001", "回滚之后的新消息"));
  t("大批回填之后新消息仍然上报", await waitSent(fresh3), await sentIds());

  // 6) 上报的时间戳必须是消息自己的时间，不是我们看到它的时间
  const r6 = await session.Runtime.evaluate({ expression:
    `(()=>{const m=window.__sent.filter(s=>s.payload&&s.payload.messages)
      .flatMap(s=>s.payload.messages).find(m=>m.msg_id==="${fresh1}");
      return JSON.stringify({ts:m&&m.ts, age:m&&m.age_ms});})()`, returnByValue: true });
  const m6 = JSON.parse(r6.result.value);
  t("上报的 ts 是消息自己的时间", m6.ts && Math.abs(m6.ts * 1000 - (Date.now() - 2000)) < 120000, m6);

  // 7) 诊断计数看得见（用户排查时靠它）
  const r7 = await session.Runtime.evaluate({ expression:
    `JSON.stringify({p:__dcwatch.stats.parsed,s:__dcwatch.stats.sent,
      k:__dcwatch.stats.skip,n:__dcwatch.stats.recent.length})`, returnByValue: true });
  const st7 = JSON.parse(r7.result.value);
  t("统计到解析数和上报数", st7.p > 0 && st7.s >= 3, st7);
  t("跳过原因分类记下来了", (st7.k.history + st7.k.render) >= 40, st7.k);
  t("最近记录留了痕迹", st7.n > 0, st7);

  /* 8) 一下真的来了 10 条新消息（开奖/发码时就是这样刷屏的）→ 必须全报。
     v1.7.1 及以前只看条数，>=8 条一律当「整批渲染」丢掉，最该提醒的时刻反而全漏
     （用户的诊断包里就是「解析 22 条 → 上报 0 条，整批渲染 22」）。 */
  await new Promise(r => setTimeout(r, 4600));
  const burst = Array.from({ length: 10 }, (_, i) => sf(1000 + i * 50));
  await push(burst.map((id, i) => MK.normal(CHAN, id, "Marcus", "444100000000001", "开奖 " + i)).join(""), 1600);
  const after8 = await sentIds();
  t("一批 10 条真·新消息全部上报", burst.every(id => after8.includes(id)),
    burst.filter(id => !after8.includes(id)).length + " 条被吞了");

  /* 9) 一批 30 条「新」消息 = 不是人在聊天（页面重挂/时间异常），宁可不报，防刷屏 */
  await new Promise(r => setTimeout(r, 4600));
  const flood = Array.from({ length: 30 }, (_, i) => sf(2000 + i * 10));
  await push(flood.map((id, i) => MK.normal(CHAN, id, "Ana", "771200000000001", "洪水 " + i)).join(""), 1600);
  const after9 = await sentIds();
  t("一批 30 条仍然按渲染丢掉（防刷屏）", flood.filter(id => after9.includes(id)).length === 0,
    flood.filter(id => after9.includes(id)).length + " 条漏出去了");
  await new Promise(r => setTimeout(r, 4600));
  const fresh4 = sf(400);
  await push(MK.normal(CHAN, fresh4, "Ana", "771200000000001", "洪水之后的一条"));
  t("防刷屏之后不影响下一条", await waitSent(fresh4), await sentIds());

  /* 10) 扩展被重载（chrome.runtime.id 消失）→ 退回直连并标 stale_ctx，
     这样 dcwatch 那边能一句话说出「按 F5」，而不是显示一个认不出的桥 */
  await session.Runtime.evaluate({ expression: `
    window.__direct = [];
    window.fetch = (u, o) => { window.__direct.push(JSON.parse(o.body));
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) }); };
    delete window.chrome.runtime.id;` });
  const fresh5 = sf(300);
  await push(MK.normal(CHAN, fresh5, "Ana", "771200000000001", "扩展重载之后发的"));
  await new Promise(r => setTimeout(r, 1200));
  const r10 = await session.Runtime.evaluate({ expression:
    `JSON.stringify(window.__direct || [])`, returnByValue: true });
  const direct = JSON.parse(r10.result.value);
  t("扩展重载后消息改走直连，没丢", direct.some(d => (d.messages || []).some(m => m.msg_id === fresh5)),
    direct);
  t("直连的包标了 stale_ctx", direct.some(d => d.stale_ctx === true), direct);
  t("直连的包自报身份（界面才认得出这个桥）",
    direct.some(d => d.bridge === "page-direct" && d.ver && String(d.browser).includes("F5")), direct);

  const fail = checks.filter(c => !c.ok);
  return { pass: checks.length - fail.length, fail: fail.length, failed: fail };
}
