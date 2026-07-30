/* dcwatch bridge 的后台脚本：只干一件事——把内容脚本解析好的消息发给本机 dcwatch。
   为什么不让内容脚本自己发：内容脚本的请求算在 discord.com 名下，Chrome 会拿
   CORS + 「公网页面访问本地网络」那套规则拦它。后台脚本带 host_permissions 发请求
   不受这些限制，所以这条路稳得多。 */
const ENDPOINT = "http://127.0.0.1:8777/api/ingest";

async function post(body) {
  const r = await fetch(ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { ok: r.ok, status: r.status };
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (!msg || msg.type !== "dcwatch") return false;
  post(msg.payload).then(reply).catch(e => reply({ ok: false, error: String(e) }));
  return true;                       // 异步回复，必须返回 true
});
