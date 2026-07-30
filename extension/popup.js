/* 弹窗：四项自检 + 一条「该怎么办」。逻辑全在 render()，方便单独测。 */
const $ = id => document.getElementById(id);
const ago = t => {
  if (!t) return "从来没有";
  const s = Math.round((Date.now() - t) / 1000);
  return s < 60 ? `${s} 秒前` : s < 3600 ? `${Math.round(s / 60)} 分钟前` : `${Math.round(s / 3600)} 小时前`;
};
const set = (n, cls, txt) => { $("i" + n).className = "ico " + cls;
  $("i" + n).textContent = cls === "ok" ? "✓" : cls === "bad" ? "✕" : "!";
  $("v" + n).textContent = txt; };

function render(st) {
  const port = st.port || 8777;
  $("port").value = port;
  $("stamp").textContent = "检测于 " + new Date(st.checkedAt || Date.now()).toLocaleTimeString();

  // 1 程序在不在
  if (st.serverOk) set(1, "ok", `在跑，端口 ${port}`);
  else set(1, "bad", `连不上：${st.serverErr || "未知"}`);

  // 2 有没有 Discord 标签页在旁听
  if (st.tabFresh) set(2, "ok", `在旁听${st.where ? "  #" + st.where : ""}，心跳 ${ago(st.lastTabPing)}`);
  else set(2, st.lastTabPing ? "bad" : "warn", st.lastTabPing ? `断了，最后心跳 ${ago(st.lastTabPing)}` : "没有标签页在旁听");

  // 3 投递
  if (st.sendErr) set(3, "bad", "失败：" + st.sendErr);
  else if (st.sent) set(3, "ok", `已上报 ${st.sent} 条，最近 ${ago(st.lastMsgAt)}`);
  else set(3, st.tabFresh && st.serverOk ? "ok" : "warn", "还没有新消息（有人说话才会上报）");

  // 4 服务端那边怎么看
  if (st.serverOk) {
    const bits = [st.srvOnline ? "左下角绿灯亮着" : "左下角还是灰的",
                  `累计收到 ${st.srvCount || 0} 条`,
                  `启用规则 ${st.rules || 0} 条`];
    if (!st.srcOn) bits.push("⚠ 浏览器旁听开关是关的");
    set(4, st.srvOnline && st.srcOn ? "ok" : "warn", bits.join("，"));
  } else set(4, "bad", "问不到");

  // 顶部结论 + 怎么办
  let cls, title, sum, fix = "";
  if (!st.serverOk) {
    [cls, title] = ["bad", "连不上 dcwatch"];
    sum = "扩展这边没问题，是本机程序不通";
    fix = `<b>这样修：</b><ol>
      <li>确认 dcwatch 在跑：任务栏里那个黑窗口还在吗？双击 <code>启动.bat</code> 或 <code>dcwatch.exe</code> 起一个</li>
      <li>浏览器直接开 <code>http://127.0.0.1:${port}</code>，能打开说明程序在，那就是端口填错了</li>
      <li>程序启动时用了 <code>--port</code> 的话，把左下角那个端口改成一样的再保存</li></ol>`;
  } else if (!st.tabFresh) {
    [cls, title] = ["warn", "程序在跑，但没在旁听"];
    sum = "扩展没在任何 Discord 页面上工作";
    fix = `<b>九成是这个原因：装完扩展之后没刷新 Discord 页面。</b><ol>
      <li>切到 Discord 标签页，<b>按 F5 刷新一次</b>（内容脚本只在页面加载时注入，装扩展前就开着的页面是没有它的）</li>
      <li>地址必须是 <code>discord.com/channels/…</code> 这种频道页，<code>@me</code> 好友列表页也算</li>
      <li>用的是 Discord 桌面客户端的话不行，必须是浏览器里的网页版</li>
      <li>刷新完回来点「重新检测」</li></ol>`;
  } else if (st.sendErr) {
    [cls, title] = ["bad", "投递失败"];
    sum = st.sendErr;
    fix = `<b>标签页是活的，但消息发不进去。</b>把这行错误发我，同时看一眼 dcwatch 界面的「日志」页。`;
  } else if (!st.srcOn) {
    [cls, title] = ["warn", "旁听开关被关了"];
    sum = "心跳能通，但消息会被丢弃";
    fix = `去 dcwatch 界面<b>左下角，把「浏览器旁听」的开关打开</b>。`;
  } else {
    [cls, title] = ["ok", "一切正常"];
    sum = `正在旁听${st.where ? " #" + st.where : ""}，等新消息就行`;
    fix = `<b>接下来：</b>让那个频道来一条新消息试试。<br>
      注意只有<b>你打开着的那个频道</b>会被看到，想盯多个频道就开多个标签页，各停在一个频道。
      历史消息不会上报，只报你挂上之后的新消息。`;
  }
  $("dot").className = "dot " + cls;
  $("title").textContent = title;
  $("sum").textContent = sum;
  $("fix").innerHTML = `<div class="fix ${cls === "ok" ? "good" : ""}">${fix}</div>`;
}

async function refresh() {
  const st = await chrome.runtime.sendMessage({ type: "dcwatch-status" });
  render(st || { serverOk: false, serverErr: "后台脚本没响应" });
}

$("again").onclick = refresh;
$("test").onclick = async () => {
  $("msg").textContent = "正在发…";
  const r = await chrome.runtime.sendMessage({ type: "dcwatch-test" });
  $("msg").textContent = r && r.ok
    ? "发出去了 — 去 dcwatch 界面「收信箱」看有没有一条 dcwatch-自检"
    : "没发出去：" + ((r && r.error) || "后台脚本没响应");
  refresh();
};
$("open").onclick = async () => {
  const p = $("port").value || 8777;
  chrome.tabs.create({ url: `http://127.0.0.1:${p}/` });
};
$("save").onclick = async () => {
  const p = Math.max(1, Math.min(65535, parseInt($("port").value, 10) || 8777));
  render(await chrome.runtime.sendMessage({ type: "dcwatch-port", port: p }));
};
refresh();
