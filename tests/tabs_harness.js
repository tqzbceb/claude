(async () => {
  const checks = [];
  const t = (name, cond, extra) => checks.push({ name, ok: !!cond, ...(cond ? {} : { got: extra }) });

  let nextId = 100;
  let nextWinId = 900;
  const liveTabs = new Map();
  const liveWindows = new Map();
  const storage = { opened: {}, st: {}, port: 8777, bridge: "test-br" };
  const fetchLog = [];
  let hbResp = null;
  let tabsResp = { ok: true, open_now: 0 };
  let createShouldFail = null;
  let createWinShouldFail = null;
  let removeShouldFail = null;

  const chrome = {
    runtime: { getManifest: () => ({ version: "1.11.0" }) },
    storage: {
      local: {
        get: async (k) => {
          if (typeof k === "string") return { [k]: storage[k] };
          if (Array.isArray(k)) {
            const o = {}; for (const x of k) o[x] = storage[x]; return o;
          }
          return { ...storage };
        },
        set: async (obj) => { Object.assign(storage, obj); },
      },
    },
    tabs: {
      create: async ({ url, active, pinned }) => {
        if (createShouldFail) {
          const err = createShouldFail;
          createShouldFail = null;
          throw new Error(err);
        }
        const id = nextId++;
        const tab = { id, url, active: !!active, pinned: !!pinned };
        liveTabs.set(id, tab);
        return tab;
      },
      remove: async (id) => {
        if (removeShouldFail) {
          const err = removeShouldFail;
          removeShouldFail = null;
          throw new Error(err);
        }
        if (!liveTabs.has(id)) throw new Error("No tab with id: " + id);
        const tab = liveTabs.get(id);
        liveTabs.delete(id);
        // 模仿真 Chrome：窗口里最后一个标签页被关掉，窗口跟着没
        if (tab.windowId != null) {
          const w = liveWindows.get(tab.windowId);
          if (w) {
            w.tabs = w.tabs.filter(x => x.id !== id);
            if (!w.tabs.length) liveWindows.delete(tab.windowId);
          }
        }
      },
      get: async (id) => {
        if (!liveTabs.has(id)) throw new Error("No tab with id: " + id);
        return liveTabs.get(id);
      },
      query: async (q) => {
        let arr = [...liveTabs.values()];
        if (q && q.url) arr = arr.filter(x => /discord\.com/.test(x.url || ""));
        return arr;
      },
      update: async (id, props) => {
        if (!liveTabs.has(id)) throw new Error("No tab with id: " + id);
        Object.assign(liveTabs.get(id), props);
        return liveTabs.get(id);
      },
      reload: async (id) => {
        if (!liveTabs.has(id)) throw new Error("No tab with id: " + id);
        liveTabs.get(id).discarded = false;
        liveTabs.get(id).frozen = false;
        liveTabs.get(id).reloaded = (liveTabs.get(id).reloaded || 0) + 1;
      },
      onRemoved: {
        _ls: [],
        addListener(fn) { this._ls.push(fn); },
        fire(id) { for (const fn of this._ls) fn(id); },
      },
      onCreated: {
        _ls: [],
        addListener(fn) { this._ls.push(fn); },
        fire(tab) { for (const fn of this._ls) fn(tab); },
      },
      onUpdated: {
        _ls: [],
        addListener(fn) { this._ls.push(fn); },
        fire(id, info, tab) { for (const fn of this._ls) fn(id, info, tab); },
      },
    },
    action: {
      setBadgeText: async () => {},
      setBadgeBackgroundColor: async () => {},
      setTitle: async () => {},
    },
    windows: {
      create: async ({ url, state, focused }) => {
        if (createWinShouldFail) {
          const err = createWinShouldFail;
          createWinShouldFail = null;
          throw new Error(err);
        }
        const winId = nextWinId++;
        const tab = { id: nextId++, url, active: true, windowId: winId };
        const w = { id: winId, state: state || "normal", focused: focused !== false, tabs: [tab] };
        liveWindows.set(winId, w);
        liveTabs.set(tab.id, tab);
        return w;
      },
    },
  };

  globalThis.fetch = async (url, opt) => {
    const body = opt && opt.body ? JSON.parse(opt.body) : null;
    fetchLog.push({ url: String(url), body });
    if (String(url).includes("/api/ext/hb")) {
      return { ok: true, json: async () => hbResp, text: async () => JSON.stringify(hbResp) };
    }
    if (String(url).includes("/api/ext/tabs")) {
      return { ok: true, json: async () => tabsResp, text: async () => JSON.stringify(tabsResp) };
    }
    return { ok: true, json: async () => ({ ok: true }), text: async () => "{}" };
  };

  const getPort = async () => storage.port || 8777;
  const getOpened = async () => (await chrome.storage.local.get("opened")).opened || {};
  const setOpened = m => chrome.storage.local.set({ opened: m });
  const getSt = async () => (await chrome.storage.local.get("st")).st || {};
  const setSt = async (patch) => {
    const st = { ...(await getSt()), ...patch };
    await chrome.storage.local.set({ st });
    return st;
  };
  const stamp = async (body) => ({
    ...body, bridge: storage.bridge, ver: chrome.runtime.getManifest().version,
    browser: "Chrome", account: "", account_id: "",
  });

  async function tabOrders() {
    const port = await getPort();
    let j = null;
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/ext/hb`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(await stamp({})),
      });
      if (r.ok) j = await r.json().catch(() => null);
    } catch (e) { return; }
    if (!j || !j.ok) return;
    const lim = j.limits || {};
    await setSt({ aoOn: !!lim.auto_open, aoNow: lim.open_now || 0 });
    const opened = await getOpened();
    const rep = { opened: [], closed: [], failed: [] };

    for (const c of (j.close || []).slice(0, 3)) {
      try {
        let tabId = opened[c.tid], alive = false;
        if (tabId != null) {
          try { await chrome.tabs.get(tabId); alive = true; } catch (e) { delete opened[c.tid]; }
        }
        if (!alive) {
          const tabs = await chrome.tabs.query({ url: "*://discord.com/*" });
          const hit = tabs.find(tt => tt.url && tt.url.includes("/" + c.tid));
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
          try { await chrome.tabs.get(opened[o.tid]); rep.opened.push(o.tid); continue; }
          catch (e) { delete opened[o.tid]; }
        }
        const tab = await openPostTab(o.url);      // D2：新开最小化窗口，镜像 background.js
        opened[o.tid] = tab.id;
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
      } catch (e) {}
    }
    return rep;
  }

  /* ---- C6 防冻结/防回收（background.js 的镜像，改动必须两边同步） ---- */
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

  /* ---- D2 开帖开新窗口（background.js 的镜像，改动必须两边同步） ---- */
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
    } catch (e) {}
  });

  const T1 = "710000000000000001";
  const T2 = "710000000000000002";
  const T3 = "710000000000000003";
  const T4 = "710000000000000004";
  const url = (tid) => `https://discord.com/channels/9/${tid}`;
  let rep;

  // 1) 心跳带 open → create + opened + 回报
  fetchLog.length = 0; liveTabs.clear(); liveWindows.clear(); storage.opened = {}; storage.st = {};
  hbResp = { ok: true, open: [{ tid: T1, url: url(T1), why: "新帖" }], close: [],
             limits: { auto_open: true, open_now: 0, max_tabs: 6 } };
  tabsResp = { ok: true, open_now: 1 };
  rep = await tabOrders();
  t("心跳带 open 时调了 create", liveTabs.size === 1 && [...liveTabs.values()][0].url === url(T1),
    { size: liveTabs.size, tabs: [...liveTabs.values()] });
  t("D2 开帖走的是新开窗口，不是当前窗口加标签页",
    liveWindows.size === 1 && [...liveWindows.values()][0].tabs[0].id === [...liveTabs.values()][0].id,
    { wins: [...liveWindows.values()], tabs: [...liveTabs.values()] });
  t("D2 新窗口最小化且不抢焦点",
    [...liveWindows.values()][0].state === "minimized" && [...liveWindows.values()][0].focused === false,
    [...liveWindows.values()][0]);
  t("storage.local 记了 opened[tid]", storage.opened[T1] != null, storage.opened);
  t("回报 opened 含该 tid", rep.opened.includes(T1), rep);
  t("POST /api/ext/tabs 发出去了", fetchLog.some(f => f.url.includes("/api/ext/tabs")
    && f.body && (f.body.opened || []).includes(T1)), fetchLog);
  t("st.aoOn 被写成 true", storage.st && storage.st.aoOn === true, storage.st);
  t("回报后 st.aoNow 更新", storage.st && storage.st.aoNow === 1, storage.st);

  // 2) 幂等：同一 tid 不重复开
  const before = liveTabs.size;
  const oldId = storage.opened[T1];
  hbResp = { ok: true, open: [{ tid: T1, url: url(T1) }], close: [],
             limits: { auto_open: true, open_now: 1 } };
  fetchLog.length = 0;
  rep = await tabOrders();
  t("已开的 tid 不重复 create", liveTabs.size === before && storage.opened[T1] === oldId,
    { size: liveTabs.size, opened: storage.opened });
  t("幂等回报仍含 opened", rep.opened.includes(T1), rep);

  // 3) create 失败 → failed（D2：窗口和退回的标签页两条路都失败才算失败）
  fetchLog.length = 0; liveTabs.clear(); liveWindows.clear(); storage.opened = {};
  createWinShouldFail = "User gesture required";
  createShouldFail = "User gesture required";
  hbResp = { ok: true, open: [{ tid: T2, url: url(T2) }], close: [],
             limits: { auto_open: true, open_now: 0 } };
  rep = await tabOrders();
  t("create 失败进 failed", rep.failed.some(f => f.tid === T2 && /gesture|Error|required/i.test(f.err)), rep);
  t("create 失败不写 opened", storage.opened[T2] == null, storage.opened);
  t("失败也 POST 回报", fetchLog.some(f => f.url.includes("/api/ext/tabs")
    && f.body && (f.body.failed || []).some(x => x.tid === T2)), fetchLog);

  // 4) close：对照表有 → remove
  liveTabs.clear(); liveWindows.clear(); storage.opened = {};
  const tabA = await chrome.tabs.create({ url: url(T3), active: false, pinned: false });
  storage.opened = { [T3]: tabA.id };
  hbResp = { ok: true, open: [], close: [{ tid: T3, why: "闲置" }],
             limits: { auto_open: true, open_now: 1 } };
  fetchLog.length = 0;
  rep = await tabOrders();
  t("close 调了 remove", !liveTabs.has(tabA.id), { live: [...liveTabs.keys()], rep });
  t("close 后 opened 清掉", storage.opened[T3] == null, storage.opened);
  t("回报 closed 含该 tid", rep.closed.includes(T3), rep);

  // 5) close：URL 兜底
  liveTabs.clear(); liveWindows.clear(); storage.opened = {};
  const tabB = await chrome.tabs.create({ url: url(T4), active: false, pinned: false });
  hbResp = { ok: true, open: [], close: [{ tid: T4, why: "闲置" }],
             limits: { auto_open: true, open_now: 0 } };
  rep = await tabOrders();
  t("close 按 URL 兜底找到并关掉", !liveTabs.has(tabB.id) && rep.closed.includes(T4), rep);

  // 6) close：已不在 → failed.gone
  liveTabs.clear(); liveWindows.clear(); storage.opened = {};
  hbResp = { ok: true, open: [], close: [{ tid: "710000000000000099", why: "闲置" }],
             limits: { auto_open: true, open_now: 0 } };
  rep = await tabOrders();
  t("标签页已不在 → failed.gone",
    rep.failed.some(f => f.tid === "710000000000000099" && f.gone === true), rep);

  // 7) 一次最多开 3 条
  liveTabs.clear(); liveWindows.clear(); storage.opened = {};
  const five = [1, 2, 3, 4, 5].map(i => ({
    tid: "71000000000000010" + i, url: url("71000000000000010" + i)
  }));
  hbResp = { ok: true, open: five, close: [], limits: { auto_open: true, open_now: 0 } };
  rep = await tabOrders();
  t("一次最多开 3 条", liveTabs.size === 3 && rep.opened.length === 3,
    { size: liveTabs.size, opened: rep.opened });

  // 8) 手动关 → onRemoved 立刻回报
  liveTabs.clear(); liveWindows.clear(); storage.opened = {};
  const tabC = await chrome.tabs.create({ url: url(T1), active: false, pinned: false });
  storage.opened = { [T1]: tabC.id };
  fetchLog.length = 0;
  liveTabs.delete(tabC.id);
  await chrome.tabs.onRemoved.fire(tabC.id);
  await new Promise(r => setTimeout(r, 50));
  t("手动关标签页清 opened", storage.opened[T1] == null, storage.opened);
  t("手动关立刻 POST closed", fetchLog.some(f => f.url.includes("/api/ext/tabs")
    && f.body && (f.body.closed || []).includes(T1)), fetchLog);

  // 9) auto_open=false → st.aoOn false
  hbResp = { ok: true, open: [], close: [], limits: { auto_open: false, open_now: 0 } };
  await tabOrders();
  t("limits.auto_open=false → st.aoOn false", storage.st && storage.st.aoOn === false, storage.st);

  // 10) hb ok=false → 不开不关
  const sizeBefore = liveTabs.size;
  const openedBefore = JSON.stringify(storage.opened);
  hbResp = { ok: false };
  fetchLog.length = 0;
  await tabOrders();
  t("hb ok=false 不开不关", liveTabs.size === sizeBefore
    && JSON.stringify(storage.opened) === openedBefore
    && !fetchLog.some(f => f.url.includes("/api/ext/tabs")),
    { size: liveTabs.size, fetchLog });

  // 11) 回报 stamp 带 bridge + ver
  liveTabs.clear(); liveWindows.clear(); storage.opened = {};
  hbResp = { ok: true, open: [{ tid: T1, url: url(T1) }], close: [],
             limits: { auto_open: true, open_now: 0 } };
  fetchLog.length = 0;
  await tabOrders();
  const tabsPost = fetchLog.find(f => f.url.includes("/api/ext/tabs"));
  t("回报带 bridge 和 ver", tabsPost && tabsPost.body
    && tabsPost.body.bridge === "test-br" && tabsPost.body.ver === "1.11.0", tabsPost);

  // 12) 桩基本行为 query/get/remove
  liveTabs.clear(); liveWindows.clear();
  const tq1 = await chrome.tabs.create({ url: "https://discord.com/channels/1/2", active: false });
  const tq2 = await chrome.tabs.create({ url: "https://example.com/", active: true });
  const q = await chrome.tabs.query({ url: "*://discord.com/*" });
  t("tabs.query 只返回 discord", q.length === 1 && q[0].id === tq1.id, q);
  const got = await chrome.tabs.get(tq1.id);
  t("tabs.get 拿得到", got && got.id === tq1.id, got);
  await chrome.tabs.remove(tq1.id);
  let gone = false;
  try { await chrome.tabs.get(tq1.id); } catch (e) { gone = true; }
  t("tabs.remove 后 get 抛错", gone, null);
  t("remove 只关目标 tab", liveTabs.has(tq2.id), [...liveTabs.keys()]);

  // 13) C6：onCreated  Discord 频道页 → autoDiscardable=false
  liveTabs.clear(); liveWindows.clear(); storage.opened = {}; storage.st = {};
  const td1 = await chrome.tabs.create({ url: "https://discord.com/channels/1/2", active: false });
  await chrome.tabs.onCreated.fire(td1);
  await new Promise(r => setTimeout(r, 30));
  t("新开 Discord 频道页被设 autoDiscardable=false",
    liveTabs.get(td1.id).autoDiscardable === false, liveTabs.get(td1.id));

  // 14) C6：非 Discord 页 / 非频道页 不动
  const td2 = await chrome.tabs.create({ url: "https://example.com/", active: false });
  await chrome.tabs.onCreated.fire(td2);
  const td3 = await chrome.tabs.create({ url: "https://discord.com/login", active: false });
  await chrome.tabs.onCreated.fire(td3);
  await new Promise(r => setTimeout(r, 30));
  t("非频道页不被设 autoDiscardable",
    liveTabs.get(td2.id).autoDiscardable == null && liveTabs.get(td3.id).autoDiscardable == null,
    { ex: liveTabs.get(td2.id), login: liveTabs.get(td3.id) });

  // 15) C6：onUpdated 在已有标签页里打开 Discord 频道 → 补设
  const td4 = await chrome.tabs.create({ url: "https://example.com/", active: false });
  liveTabs.get(td4.id).url = "https://discord.com/channels/1/2";
  await chrome.tabs.onUpdated.fire(td4.id, { status: "loading" }, liveTabs.get(td4.id));
  await new Promise(r => setTimeout(r, 30));
  t("老标签页导航到频道页也补设", liveTabs.get(td4.id).autoDiscardable === false,
    liveTabs.get(td4.id));

  // 16) C6：protectAll 全量设 + 回收/冻结的救回
  liveTabs.clear(); liveWindows.clear(); storage.st = {};
  const tp1 = await chrome.tabs.create({ url: "https://discord.com/channels/1/2", active: false });
  const tp2 = await chrome.tabs.create({ url: "https://ptb.discord.com/channels/1/3", active: false });
  const tp3 = await chrome.tabs.create({ url: "https://discord.com/channels/1/4", active: false });
  liveTabs.get(tp2.id).discarded = true;
  liveTabs.get(tp3.id).frozen = true;
  await protectAll();
  t("protectAll 把所有频道页都设了 autoDiscardable=false",
    [tp1, tp2, tp3].every(x => liveTabs.get(x.id).autoDiscardable === false),
    [...liveTabs.values()]);
  t("被回收/冻结的被 reload 救回",
    liveTabs.get(tp2.id).reloaded === 1 && liveTabs.get(tp3.id).reloaded === 1
    && !liveTabs.get(tp2.id).discarded && !liveTabs.get(tp3.id).frozen,
    { tp2: liveTabs.get(tp2.id), tp3: liveTabs.get(tp3.id) });
  t("没被回收的不 reload", liveTabs.get(tp1.id).reloaded == null, liveTabs.get(tp1.id));
  t("救回计数进了 st.rescued", storage.st && storage.st.rescued === 2, storage.st);

  // 17) C6：ptb 也匹配；再次 protectAll 无事发生不重复救
  await protectAll();
  t("再次巡检不重复救回", storage.st.rescued === 2 && liveTabs.get(tp2.id).reloaded === 1,
    { st: storage.st, tp2: liveTabs.get(tp2.id) });

  // 18) D2：开帖建最小化窗口；建完就上防回收；关掉唯一标签页窗口跟着没
  liveTabs.clear(); liveWindows.clear(); storage.opened = {};
  hbResp = { ok: true, open: [{ tid: T1, url: url(T1) }], close: [],
             limits: { auto_open: true, open_now: 0 } };
  rep = await tabOrders();
  const w1 = [...liveWindows.values()][0];
  t("D2 开帖建了一个最小化窗口", w1 && w1.state === "minimized" && w1.focused === false,
    [...liveWindows.values()]);
  t("D2 opened 记的是窗口里那个标签页", storage.opened[T1] === w1.tabs[0].id, storage.opened);
  t("D2 建完立刻上了防回收保护", liveTabs.get(w1.tabs[0].id).autoDiscardable === false,
    liveTabs.get(w1.tabs[0].id));
  await chrome.tabs.remove(w1.tabs[0].id);
  t("D2 关掉唯一标签页，窗口跟着没", liveWindows.size === 0 && liveTabs.size === 0,
    { wins: liveWindows.size, tabs: liveTabs.size });

  // 19) D2：windows.create 抛错 → 退回 tabs.create，帖照样开
  liveTabs.clear(); liveWindows.clear(); storage.opened = {};
  createWinShouldFail = "minimized not supported on this platform";
  hbResp = { ok: true, open: [{ tid: T2, url: url(T2) }], close: [],
             limits: { auto_open: true, open_now: 0 } };
  rep = await tabOrders();
  t("D2 windows.create 失败退回 tabs.create 开成",
    liveTabs.size === 1 && liveWindows.size === 0 && rep.opened.includes(T2) && !rep.failed.length,
    { tabs: liveTabs.size, wins: liveWindows.size, rep });
  t("D2 退回路径不抢焦点且上了防回收",
    [...liveTabs.values()][0].active === false && [...liveTabs.values()][0].autoDiscardable === false,
    [...liveTabs.values()][0]);

  return {
    pass: checks.filter(c => c.ok).length,
    fail: checks.filter(c => !c.ok).length,
    failed: checks.filter(c => !c.ok),
    total: checks.length,
  };
})()
