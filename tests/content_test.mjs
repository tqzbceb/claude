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
  /* 回复消息：预览里是被回复人的头像和原文，都排在真作者之前 —— 不许被骗走 */
  reply: (chan, mid, name, avatarId, refMid, refName, refAvatarId, refText, text) => `
    <li id="chat-messages-${chan}-${mid}">
      <div class="repliedMessage-1">
        <img src="https://cdn.discordapp.com/avatars/${refAvatarId}/r.png">
        <span class="username-2">${refName}</span>
        <div id="message-content-${refMid}">${refText}</div>
      </div>
      <img src="https://cdn.discordapp.com/avatars/${avatarId}/x.png">
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
  await push(MK.reply(CHAN, "1008", "Replier", "444100000000002",
    "1001", "Marcus", "444100000000001", "@我 明天提前到 10 点", "回复：收到"));

  // v1.8.0：四种「没文字」的消息，都要变成看得懂的正文上报
  await push(MK.media(CHAN, "1010", "Pic", `<div class="imageWrapper-1">
    <img alt="截图.png" src="https://cdn.discordapp.com/attachments/1/2/a.png"></div>`, { noBody: true }));
  await push(MK.media(CHAN, "1011", "Stk", `<div class="clickableSticker-1">
    <img class="stickerAsset-1" alt="猫猫点头"></div>`, { noBody: true }));
  await push(MK.media(CHAN, "1012", "Emo", `<img class="emoji-1" alt=":kekw:">`));
  await push(MK.media(CHAN, "1013", "Fil", `<div class="attachment-1">
    <div class="filename-1">keys.txt</div></div>`, { noBody: true }));

  // D1：行内表情（文字中间夹的 <img>）不能丢；Discord 给同名表情加的 ~N 后缀要剥掉
  await push(MK.media(CHAN, "1014", "Mix", `看 <img class="emoji-1" alt=":kekw:"> 这个`));
  await push(MK.media(CHAN, "1015", "Dup", `<img class="emoji-1" alt=":cat_cry~1:">`));

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
  /* D1：正文里的行内表情是 <img>，innerText 把它整个丢掉（「看 :kekw: 这个」变「看  这个」）。
     现在提取时把表情 img 换成代码文字，行内表情留在正文里；~N 后缀剥掉，不然像乱码 */
  t("行内表情留在正文里", by["1014"] && by["1014"].content.includes(":kekw:"), by["1014"]);
  t("行内表情两边的文字也都在", by["1014"] && by["1014"].content.includes("看") && by["1014"].content.includes("这个"), by["1014"]);
  t("表情码剥掉 ~N 后缀", by["1015"] && by["1015"].content.includes(":cat_cry:") && !by["1015"].content.includes("~1"), by["1015"]);
  t("纯文字消息的 media 是空的", by["1001"] && (by["1001"].media || []).length === 0, by["1001"]);
  t("默认头像 author_id 留空", by["1005"] && !by["1005"].author_id, by["1005"]);
  t("默认头像也有作者名", by["1005"] && by["1005"].author === "NoAvatar", by["1005"]);
  t("回复消息作者是发言人不是被回复人", by["1008"] && by["1008"].author === "Replier", by["1008"]);
  t("回复消息 author_id 不取回复预览头像", by["1008"] && by["1008"].author_id === "444100000000002", by["1008"]);
  t("回复消息正文不取回复预览原文", by["1008"] && by["1008"].content === "回复：收到", by["1008"]);
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

/* ============ 第三套：C2 自动开帖（chrome.tabs 桩） ============
   background.js 的 tabOrders() 要 create/remove/query 标签页，并把 opened 记进
   storage.local、执行完 POST /api/ext/tabs 回报。这里不真装扩展：把 background
   里 tabOrders 相关的逻辑抽成「在假 chrome 桩上跑」的同等代码，断言 ≥10 条。
   加了新 chrome API 就得在桩里补 —— tests/RUN.md 写着的坑。 */
export async function runTabs(session) {
  const fs = await import("fs/promises");
  const harnessPath = new URL("./tabs_harness.js", import.meta.url).pathname;
  const harness = await fs.readFile(harnessPath, "utf8");

  const tid = (await session.Target.getTargets({})).targetInfos
    .find(t => t.type === "page" && !t.url.startsWith("chrome://")).targetId;
  await session.use(tid);
  await session.Page.enable();
  await session.Runtime.enable();

  const loaded = session.waitFor("Page.loadEventFired"); loaded.catch(() => {});
  await session.Page.navigate({ url: "https://usercontent.browser-use.tools" });
  await Promise.race([loaded, new Promise(r => setTimeout(r, 8000))]);
  await session.Page.setDocumentContent({ frameId: tid, html:
    "<!doctype html><html><body><pre id=out></pre></body></html>" });

  const r = await session.Runtime.evaluate({
    awaitPromise: true,
    returnByValue: true,
    expression: harness,
  });
  if (r.exceptionDetails) {
    const ed = r.exceptionDetails;
    return { boot: "FAIL", error: (ed.text || "") + " " +
      ((ed.exception && ed.exception.description) || "") +
      " @" + (ed.lineNumber + 1) };
  }
  return r.result.value;
}

/* ============ 第四套：F1 名录收割（侧栏名字 → ID） ============
   为什么单独一套：F1 让用户「打名字就能建规则」，而名录**全靠扩展逛 Discord 时
   从侧栏抄**。这块只读 DOM，一条消息都不用来，所以前三套（都是喂消息）碰不到它。
   F1 落地时只在云浏览器里对合成侧栏临时验过一次，脚本没留下 —— content.js 的
   收割器一改就没人守，用户那边的表现是「打名字查不到东西」，还会以为是模型笨。

   造的是一份「像 Discord 那样」的侧栏：左边服务器图标列（data-dnd-name）、
   频道列（分类头 + 频道，**外面套一层 <nav>**，Discord 就是这么套的）、
   论坛帖子卡片、右边成员栏、左下私信列表。断言四类都抄到、面包屑对得上、
   括号说明和 # 前缀剥干净、抠不到 ID 的人不瞎报、切频道能补扫。 */
const NG1 = "900000000000000001";   // 服务器：测试服（当前所在）
const NG2 = "900000000000000002";   // 服务器：交易群（切过去用）
const NCAT = "600000000000000010";  // 分类：信息区
const NCH_ANN = "800000000000000011";   // 频道：公告（在信息区下面）
const NCH_ARC = "800000000000000012";   // 频道：公告归档（只有 aria-label，带括号说明）
const NCH_CHAT = "800000000000000013";  // 频道：闲聊（可见文字带 # 前缀）
const NCH_FORUM = "810000000000000014"; // 频道：论坛（URL 当前就在这儿）
const NCH_NEW = "800000000000000015";   // 切服务器后才出现的频道
const NTH = "700000000000000021";       // 帖子：登录 500 怎么修
const NU1 = "500000000000000031";       // 成员：小明（有自定义头像 → 抠得到 ID）
const NU2 = "500000000000000032";       // 私信：Ana
const NDM = "820000000000000041";       // 私信频道 ID（人的 ID 不在这儿，在头像里）

const FAKE_SIDEBAR = `<!doctype html><html><head><title>(3) #论坛 | 测试服</title></head>
<body>
  <section class="panels-abc panels">
    <img src="https://cdn.discordapp.com/avatars/777000111/abc.png">
    <div class="nameTag-xyz"><span class="hovered-1">alice#1234</span></div>
  </section>

  <!-- 左边一列服务器图标 -->
  <nav data-list-id="guildsnav" aria-label="服务器" class="guilds-1">
    <div data-dnd-name="测试服" class="listItem-1"><a href="/channels/${NG1}"><div class="blob-1"></div></a></div>
    <div data-dnd-name="交易群 (2 条未读)" class="listItem-1"><a href="/channels/${NG2}"></a></div>
    <a href="/channels/@me" aria-label="私信">私信</a>
  </nav>

  <!-- 频道列：Discord 外面套了一层 nav，别让它被当成服务器 -->
  <div class="sidebar-1">
    <nav aria-label="频道" class="container-1">
      <ul data-list-id="channels" class="list-1">
        <li data-list-item-id="channels___categoryid_${NCAT}" class="containerDefault-1">
          <div class="containerDefault-1"><h3 class="headerContent-1">信息区</h3></div>
        </li>
        <li data-list-item-id="channels___${NCH_ANN}" class="containerDefault-2">
          <a href="/channels/${NG1}/${NCH_ANN}" aria-label="公告 (文字频道)" class="link-1">
            <div class="name-1">公告</div></a>
        </li>
        <li data-list-item-id="channels___${NCH_ARC}" class="containerDefault-2">
          <a href="/channels/${NG1}/${NCH_ARC}" aria-label="公告归档 (文字频道, 已静音)" class="link-1"></a>
        </li>
        <li data-list-item-id="channels___${NCH_CHAT}" class="containerDefault-2">
          <a href="/channels/${NG1}/${NCH_CHAT}" aria-label="闲聊 (文字频道)" class="link-1">
            <div class="name-1">#闲聊</div></a>
        </li>
        <li data-list-item-id="channels___${NCH_FORUM}" class="containerDefault-2">
          <a href="/channels/${NG1}/${NCH_FORUM}" aria-label="论坛 (论坛频道)" class="link-1">
            <div class="name-1">论坛</div></a>
        </li>
      </ul>
    </nav>
  </div>

  <!-- 论坛里的帖子卡片 -->
  <div class="forumPostList-1">
    <div data-item-id="${NTH}" class="postCard-1">
      <div class="titleText-1">登录 500 怎么修</div>
      <div class="messageContent-1">我这边也是</div>
    </div>
  </div>

  <!-- 右边成员栏：小明抠得到 ID，小花用默认头像抠不到 -->
  <div aria-label="成员" class="membersWrap-1">
    <div class="member-1"><img src="https://cdn.discordapp.com/avatars/${NU1}/m.png">
      <div class="name-1">小明</div></div>
    <div class="member-1"><img src="https://cdn.discordapp.com/embed/avatars/3.png">
      <div class="name-1">小花</div></div>
  </div>

  <!-- 左下私信列表 -->
  <div class="privateChannels-1">
    <a href="/channels/@me/${NDM}" class="channel-1">
      <img src="https://cdn.discordapp.com/avatars/${NU2}/d.png">
      <div class="name-1">Ana</div></a>
  </div>

  <ol id="feed"></ol>
</body></html>`;

export async function runNames(session, contentJsPath) {
  const fs = await import("fs/promises");
  const path = contentJsPath || process.env.DCW_CONTENT_JS ||
    new URL("../extension/content.js", import.meta.url).pathname;
  const code = await fs.readFile(path, "utf8");

  const tid = (await session.Target.getTargets({})).targetInfos
    .find(t => t.type === "page" && !t.url.startsWith("chrome://")).targetId;
  await session.use(tid);
  await session.Page.enable();
  await session.Page.bringToFront();      // 后台标签页的定时器被节流，3 秒那次开机扫描会迟到
  await session.Runtime.enable();

  const loaded = session.waitFor("Page.loadEventFired"); loaded.catch(() => {});
  await session.Page.navigate({ url: "https://usercontent.browser-use.tools" });
  await Promise.race([loaded, new Promise(r => setTimeout(r, 8000))]);
  await session.Page.setDocumentContent({ frameId: tid, html: FAKE_SIDEBAR });
  await session.Runtime.evaluate({ expression: `
    history.replaceState({}, "", "/channels/${NG1}/${NCH_FORUM}");
    window.__sent = [];       // 所有 sendMessage 都记下来：要验名录没混进消息投递
    window.__direct = [];     // 名录退回页面直连时走 fetch，也记下来
    window.chrome = { runtime: {
      id: "stub-ext-id",
      getManifest: () => ({ version: "1.12.0" }),
      sendMessage: (m, cb) => { window.__sent.push(m); if (cb) cb({ ok: true, st: {} }); },
      onMessage: { addListener: (fn) => { window.__onMsg = fn; } },
      lastError: null } };
    window.fetch = (u, o) => { try { window.__direct.push(JSON.parse(o.body)); } catch (e) {}
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) }); };
  ` });
  const ev = await session.Runtime.evaluate({ expression: code });
  if (ev.exceptionDetails) return { boot: "FAIL", error: ev.exceptionDetails.text };

  /* 名录的开机扫描排在 3 秒（等页面渲染完），轮询到有货为止 ——
     单次 sleep 读一把是 content_test 老抖动的根因，RUN.md 里写着。 */
  const names = async () => {
    const r = await session.Runtime.evaluate({ expression:
      `JSON.stringify(window.__sent.filter(s => s.type === "dcwatch-names"))`,
      returnByValue: true });
    return JSON.parse(r.result.value);
  };
  const waitNames = async (pred, ms = 8000) => {
    const t0 = Date.now();
    for (;;) {
      const pk = await names();
      if (pred(pk) || Date.now() - t0 > ms) return pk;
      await new Promise(r => setTimeout(r, 400));
    }
  };
  const flat = (pk) => pk.flatMap(p => (p.payload && p.payload.names) || []);
  const pick = (pk, kind, id) => flat(pk).find(n => n.kind === kind && n.id === id);

  const packs = await waitNames(pk => flat(pk).length >= 8);
  const all = flat(packs);

  const checks = [];
  const t = (name, cond, extra) => checks.push({ name, ok: !!cond, ...(cond ? {} : { got: extra }) });

  t("抄到名录并上报了", packs.length > 0 && all.length > 0, { packs: packs.length, n: all.length });
  t("名录走自己的口子 dcwatch-names", packs.every(p => p.type === "dcwatch-names"), packs.map(p => p.type));
  t("上报带扩展版本和在盯的频道",
    packs[0] && packs[0].payload.ver === "1.12.0" && packs[0].payload.where === "论坛", packs[0]);
  /* 名录和「有没有新消息」无关：混进消息批次会把诊断里的「上报了几条」搅乱 */
  const msgPacks = await session.Runtime.evaluate({ expression:
    `JSON.stringify(window.__sent.filter(s => s.type === "dcwatch")
       .map(s => Object.keys(s.payload || {})))`, returnByValue: true });
  t("名录没混进消息投递", !JSON.parse(msgPacks.result.value).some(k => k.includes("names")),
    msgPacks.result.value);

  // ---- 四类都要抄到 ----
  const g1 = pick(packs, "guild", NG1), g2 = pick(packs, "guild", NG2);
  t("服务器抄到了（当前这个）", g1 && g1.name === "测试服", g1);
  t("侧栏别的服务器也抄到", g2 && g2.name === "交易群", g2);
  /* data-dnd-name 上 Discord 会挂「(2 条未读)」这种说明，得剥掉，
     不然用户打「交易群」查不着 */
  t("服务器名剥掉括号里的未读说明", g2 && !/未读/.test(g2.name), g2);
  /* 频道列外面也套着 <nav>，频道链接的 href 同样是 /channels/<服务器ID>/... ——
     按 nav 里的链接认服务器，会把频道名当成服务器名写进名录，
     用户打服务器名就查不到、或者查出来是个频道 */
  t("服务器名不被频道名顶掉",
    !all.some(n => n.kind === "guild" && ["公告", "闲聊", "论坛", "公告归档"].includes(n.name)),
    all.filter(n => n.kind === "guild"));
  t("@me 不当服务器抄", !all.some(n => n.kind === "guild" && /私信/.test(n.name)),
    all.filter(n => n.kind === "guild"));

  const cat = pick(packs, "category", NCAT);
  t("分类抄到了", cat && cat.name === "信息区", cat);
  t("分类挂在服务器下", cat && cat.guild_id === NG1 && cat.guild_name === "测试服", cat);

  const ann = pick(packs, "channel", NCH_ANN);
  t("频道抄到了", ann && ann.name === "公告", ann);
  /* 名字重了（好几个服务器都有 #公告）时，用户就靠这条面包屑认出是哪个 */
  t("频道带面包屑：服务器 › 分类",
    ann && ann.guild_name === "测试服" && ann.parent_name === "信息区", ann);
  t("频道 ID 是频道自己的不是服务器的", ann && ann.id === NCH_ANN, ann);
  const arc = pick(packs, "channel", NCH_ARC);
  t("只有 aria-label 的频道也抄到，括号说明剥掉", arc && arc.name === "公告归档", arc);
  const chat = pick(packs, "channel", NCH_CHAT);
  t("频道名前面的 # 剥掉", chat && chat.name === "闲聊", chat);

  const th = pick(packs, "thread", NTH);
  t("帖子抄到了", th && th.name === "登录 500 怎么修", th);
  t("帖子挂在当前频道下", th && th.parent_id === NCH_FORUM && th.parent_name === "论坛", th);
  t("帖子正文不当标题", th && !/我这边也是/.test(th.name || ""), th);

  const u1 = pick(packs, "user", NU1);
  t("成员抄到了", u1 && u1.name === "小明", u1);
  t("人的 ID 从头像地址抠出来", u1 && u1.id === NU1, u1);
  /* 用默认头像的人抠不到 ID —— 宁可不报，也不能瞎猜一个 ID 塞进名录：
     名录错一条，用户按名字建的规则就盯错人 */
  t("默认头像的人不瞎报", !all.some(n => n.kind === "user" && n.name === "小花"),
    all.filter(n => n.kind === "user"));
  const u2 = pick(packs, "user", NU2);
  t("私信列表里的人也抄到", u2 && u2.name === "Ana", u2);

  // ---- 卫生：不许有垃圾条目 ----
  t("每条都有 kind/id/name", all.every(n => n.kind && n.id && n.name), all.filter(n => !(n.kind && n.id && n.name)));
  t("ID 都是 15-25 位 snowflake", all.every(n => /^\d{15,25}$/.test(n.id)),
    all.filter(n => !/^\d{15,25}$/.test(n.id)));
  t("名字不带首尾空白/换行", all.every(n => n.name === n.name.trim() && !n.name.includes("\n")),
    all.filter(n => n.name !== n.name.trim() || n.name.includes("\n")));
  t("一个包最多 500 条（大服务器不打巨包）",
    packs.every(p => (p.payload.names || []).length <= 500), packs.map(p => (p.payload.names || []).length));

  // ---- 切服务器要补扫：侧栏整片换掉了，新服务器的频道名只有这时候看得见 ----
  await session.Runtime.evaluate({ expression: `
    document.title = "#新频道 | 交易群";
    history.replaceState({}, "", "/channels/${NG2}/${NCH_NEW}");
    document.querySelector('[data-list-id="channels"]').insertAdjacentHTML("beforeend",
      '<li data-list-item-id="channels___${NCH_NEW}" class="containerDefault-2">' +
      '<a href="/channels/${NG2}/${NCH_NEW}" aria-label="新频道 (文字频道)" class="link-1">' +
      '<div class="name-1">新频道</div></a></li>');
  ` });
  const after = await waitNames(pk => !!pick(pk, "channel", NCH_NEW), 9000);
  const nw = pick(after, "channel", NCH_NEW);
  t("切服务器后补扫，新频道抄到了", nw && nw.name === "新频道", nw);
  t("新频道挂在新服务器下", nw && nw.guild_id === NG2 && nw.guild_name === "交易群", nw);

  // ---- 标签页重新可见也补扫（切回来时侧栏可能已经改过名了）----
  const before = (await names()).length;
  await session.Runtime.evaluate({ expression:
    `document.dispatchEvent(new Event("visibilitychange"))` });
  const vis = await waitNames(pk => pk.length > before, 6000);
  t("标签页重新可见时补扫", vis.length > before, { before, after: vis.length });

  /* Discord 改版把「服务器图标那一列」的标记换掉了怎么办：认不出那一列时
     只收「光是服务器」的地址（/channels/<id>），宁少不错 —— 少一条用户可以贴链接，
     错一条他会盯错服务器还以为程序坏了。 */
  const fb = await session.Runtime.evaluate({ expression: `(() => {
    const nav = document.querySelector('[data-list-id="guildsnav"]');
    nav.removeAttribute("data-list-id"); nav.className = "xx-1";   // 装成认不出的样子
    const got = []; window.__dcwatch.harvestGuilds(got);
    return JSON.stringify(got);
  })()`, returnByValue: true });
  const fbg = JSON.parse(fb.result.value);
  t("认不出服务器列时也不把频道当服务器",
    !fbg.some(n => ["公告", "闲聊", "论坛", "公告归档", "新频道"].includes(n.name)), fbg);
  t("认不出服务器列时，光是服务器的地址照样收",
    fbg.some(n => n.id === NG1 && n.name === "测试服"), fbg);

  const fail = checks.filter(c => !c.ok);
  return { pass: checks.length - fail.length, fail: fail.length, failed: fail,
           kinds: [...new Set(all.map(n => n.kind))], n: all.length };
}
