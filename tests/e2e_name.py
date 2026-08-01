"""v1.12.0 回归：名字→ID 名录（F1）。

覆盖 扩展上报 /api/ext/names（学 / 不学 / 改名更新 / 不擦好数据 / 参数校验）、
/api/lookup（排序档位 / 大小写 / kind 限定 / limit 钳制 / 空名录和查不到两种话术 / 面包屑）、
/api/names（列表 + 统计 + kind 过滤）、
**名录不被收信闸和旁听开关饿死**（没规则时消息不入库，但名字照学 —— 否则第一条规则没处抄 ID）、
工作台 find_target 工具（多候选 / 单候选 / 零候选各自的话术，一律要求先跟用户确认）。

跑法见 RUN.md：mockllm 在 :8899，server 在 :8777，库必须干净（第 0 节要断言「名录还是空的」）。
"""
import json, time, urllib.parse, urllib.request, urllib.error

BASE = "http://127.0.0.1:8777"
MOCK = "http://127.0.0.1:8899"
P = F = 0

G1 = "800000000000000001"          # 服务器：旅途
G2 = "800000000000000002"          # 服务器：学习小组
CAT1 = "810000000000000001"        # 分类：公共区（G1）
CH1 = "820000000000000001"         # 频道：教程分享区（G1 › 公共区）
CH2 = "820000000000000002"         # 频道：教程分享区（G2）—— 同名，靠面包屑辨认
CH3 = "820000000000000003"         # 频道：闲聊
TH1 = "830000000000000001"         # 帖子：教程合集
U1 = "840000000000000001"          # 人：老王
U2 = "840000000000000002"          # 人：老王（另一个顶同名的）


def http(url, body=None, method=None, timeout=60):
    req = urllib.request.Request(url, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode()[:300]}


def call(path, body=None, **kw):
    return http(BASE + path, body, **kw)


def ok(name, cond, extra=""):
    global P, F
    if cond:
        P += 1; print("  ok  ", name)
    else:
        F += 1; print("  FAIL", name, str(extra)[:400])


def look(q, kind="", limit=None):
    p = f"/api/lookup?q={urllib.parse.quote(q)}"
    if kind:
        p += f"&kind={urllib.parse.quote(kind)}"
    if limit is not None:
        p += f"&limit={urllib.parse.quote(str(limit))}"
    return call(p)


def ids(r):
    return [h["id"] for h in r.get("hits", [])]


def report(names, **extra):
    return call("/api/ext/names", dict({"names": names}, **extra))


def names_of(kind=""):
    return call("/api/names" + (f"?kind={kind}" if kind else ""))


print("0. 空名录：说「还是空的」，别让用户以为功能坏了")
r = look("教程")
ok("空名录也 ok", r.get("ok") is True, r)
ok("一条候选都没有", r.get("hits") == [], r)
ok("统计是 0", r.get("stat", {}).get("total") == 0, r.get("stat"))
ok("话术点名「名录还是空的」+ 怎么攒", "名录还是空的" in (r.get("note") or "")
   and "扩展" in (r.get("note") or ""), r.get("note"))

print("\n1. 扩展上报：侧栏抄来的名字进名录")
r = report([
    {"kind": "guild", "id": G1, "name": "旅途"},
    {"kind": "guild", "id": G2, "name": "学习小组"},
    {"kind": "category", "id": CAT1, "name": "公共区", "guild_id": G1},
    {"kind": "channel", "id": CH1, "name": "教程分享区", "guild_id": G1, "parent_id": CAT1},
    {"kind": "channel", "id": CH2, "name": "教程分享区", "guild_id": G2},
    {"kind": "channel", "id": CH3, "name": "闲聊", "guild_id": G1, "parent_id": CAT1},
    {"kind": "thread", "id": TH1, "name": "教程合集", "guild_id": G1, "parent_id": CH1},
], bridge="ext-name-1", ver="1.12.0")
ok("上报成功", r.get("ok") is True, r)
ok("七条都学进去了", r.get("learned") == 7, r)
st = r.get("stat") or {}
ok("按类型统计对（服务器 2）", st.get("guild") == 2, st)
ok("按类型统计对（频道 3 / 分类 1 / 帖子 1）",
   st.get("channel") == 3 and st.get("category") == 1 and st.get("thread") == 1, st)
ok("总数 7", st.get("total") == 7, st)
ok("顺手把服务端版本和扩展底线报回来（扩展自己判要不要提示更新）",
   r.get("server_ver") and r.get("ext_min"), r)

print("\n2. 脏数据不许进名录（空名字查不出东西，只会污染候选）")
r = report([
    {"kind": "channel", "id": "820000000000000099", "name": ""},          # 空名字
    {"kind": "channel", "id": "", "name": "没有 id"},                     # 空 id
    {"kind": "什么鬼", "id": "820000000000000098", "name": "野类型"},       # 不认的 kind
    {"kind": "channel", "id": "  ", "name": "只有空格的 id"},
    "这不是对象",
    {"kind": "user", "id": U1, "name": "  老王  "},                       # 前后空格要削掉
])
ok("只学了那一条干净的", r.get("learned") == 1, r)
ok("脏数据没进统计", r.get("stat", {}).get("total") == 8, r.get("stat"))
hit = look("老王")["hits"]
ok("名字前后空格削掉了", bool(hit) and hit[0]["name"] == "老王", hit)
ok("野类型一条都查不到", look("野类型")["hits"] == [], look("野类型"))
ok("空名字那条没混进来", not any(h["id"] == "820000000000000099"
                                for h in look("教程")["hits"]), look("教程"))

r = report("不是数组")
ok("names 不是数组 → 400", r.get("_status") == 400, r)
r = call("/api/ext/names", [1, 2, 3])
ok("body 不是对象 → 400", r.get("_status") == 400, r)
r = report([])
ok("空数组不报错，学了 0 条", r.get("ok") is True and r.get("learned") == 0, r)

print("\n3. 同一个 ID 再来：更新名字（人会改昵称、频道会改名），不新增行")
before = len(names_of("channel")["names"])
r = report([{"kind": "channel", "id": CH3, "name": "闲聊灌水", "guild_id": G1, "parent_id": CAT1}])
ok("改名当作更新", r.get("learned") == 1, r)
ok("行数没变（不是又插一条）", len(names_of("channel")["names"]) == before,
   names_of("channel")["names"])
ok("新名字能查到", ids(look("闲聊灌水")) == [CH3], look("闲聊灌水"))
ok("查旧名字回的也是新名字（库里只有最新那个）",
   [h["name"] for h in look("闲聊")["hits"]] == ["闲聊灌水"], look("闲聊"))
row = [x for x in names_of("channel")["names"] if x["id"] == CH3][0]
ok("见过次数累加", row.get("hits") >= 2, row)

r = report([{"kind": "channel", "id": CH1, "name": "教程分享区"}])   # 这次没带 guild/parent
row = [x for x in names_of("channel")["names"] if x["id"] == CH1][0]
ok("没带的 guild/parent 不许把好数据擦成空", "旅途" in (row.get("where") or ""), row)

print("\n4. 按名字查 ID：给候选，不替用户猜")
r = look("教程分享区")
ok("同名的两个频道都列出来", set(ids(r)) >= {CH1, CH2}, r)
ok("没有 note（查到了就别啰嗦）", not r.get("note"), r)
c1 = [h for h in r["hits"] if h["id"] == CH1][0]
c2 = [h for h in r["hits"] if h["id"] == CH2][0]
ok("面包屑分得清是哪个：服务器 › 分类", c1["where"] == "旅途 › 公共区", c1)
ok("另一个只有服务器", c2["where"] == "学习小组", c2)
ok("类型说人话", c1["kind_cn"] == "频道" and c1["kind"] == "channel", c1)
ok("带上什么时候见过的", bool(c1.get("seen_ago")), c1)
ok("带上来源（侧栏抄的）", c1.get("src") == "sidebar", c1)
ok("带上所属服务器 ID（建规则要用）", c1.get("guild_id") == G1, c1)

r = look("教程")
ok("只写一部分也能找（前缀）", set(ids(r)) >= {CH1, CH2, TH1}, r)
ok("完全同名的排在只沾一半的前面", ids(look("教程合集"))[0] == TH1, look("教程合集"))
report([{"kind": "user", "id": U2, "name": "LaoWang", "guild_id": G2}])
ok("英文名大小写不敏感", ids(look("laowang")) == [U2], look("laowang"))
ok("中间命中也算（包含）", U2 in ids(look("aowan")), look("aowan"))

print("\n5. 限定类型 / 条数 / 空关键字")
r = look("教程分享区", kind="channel")
ok("限定频道就只回频道", all(h["kind"] == "channel" for h in r["hits"]) and len(r["hits"]) == 2, r)
ok("限定帖子查不到频道", ids(look("教程分享区", kind="thread")) == [],
   look("教程分享区", kind="thread"))
ok("限定人只回人", all(h["kind"] == "user" for h in look("老王", kind="user")["hits"]),
   look("老王", kind="user"))
ok("野 kind 当没限定处理（别 500）", look("教程分享区", kind="乱写")["ok"] is True,
   look("教程分享区", kind="乱写"))
ok("limit=1 只回一条", len(look("教程", limit=1)["hits"]) == 1, look("教程", limit=1))
ok("limit=0 当没填处理，照回默认那批（别回空让人以为没查到）",
   len(look("教程", limit=0)["hits"]) >= 1, look("教程", limit=0))
ok("limit 大过上限也钳住", len(look("教程", limit=999)["hits"]) <= 50, look("教程", limit=999))
ok("limit 乱填不炸", look("教程", limit="abc")["ok"] is True, look("教程", limit="abc"))
r = look("")
ok("关键字空 → 空结果不报错", r["ok"] is True and r["hits"] == [], r)
r = look("这个名字肯定没人叫")
ok("查不到时的话术是「名录里没有」而不是「名录空的」",
   "名录里没有" in (r.get("note") or "") and "名录还是空的" not in (r.get("note") or ""), r.get("note"))
ok("查不到时给出路（贴链接 / 让扩展见一面）",
   "链接" in (r.get("note") or ""), r.get("note"))

print("\n6. 名录列表（界面「名录里有什么」）")
r = names_of()
ok("列表带统计", r["stat"]["total"] == 9, r["stat"])
ok("每条都带面包屑字段", all("where" in x for x in r["names"]), r["names"][:2])
ok("kind 过滤生效", all(x["kind"] == "user" for x in names_of("user")["names"]),
   names_of("user")["names"])
ok("过滤后统计还是全量（界面要显示总数）", names_of("user")["stat"]["total"] == 9,
   names_of("user")["stat"])

print("\n7. 收信闸拦下的消息也照学名字（否则第一条规则没处抄 ID）")
for x in call("/api/state")["rules"]:                       # 确保零规则：闸全关
    call(f"/api/rules/{x['id']}", None, method="DELETE")
m0 = len(call("/api/messages")["messages"])
r = call("/api/ingest", {"account": "tester", "bridge": "ext-name-1", "messages": [
    {"msg_id": "nm1", "channel_id": "820000000000000010", "channel_name": "新来的频道",
     "guild_id": G1, "author": "新来的人", "author_id": "840000000000000010",
     "content": "第一条消息", "ts": time.time()}]})
time.sleep(0.8)
ok("消息收下了（接口层面）", r.get("ok") is True, r)
ok("但收信闸没让它入库（零规则=零收信）", len(call("/api/messages")["messages"]) == m0,
   call("/api/messages")["messages"][:1])
ok("频道名字还是学到了", ids(look("新来的频道", kind="channel")) == ["820000000000000010"],
   look("新来的频道"))
ok("说话的人也学到了", ids(look("新来的人", kind="user")) == ["840000000000000010"],
   look("新来的人"))

r = call("/api/ingest", {"messages": [
    {"msg_id": "nm2", "channel_id": "830000000000000020", "channel_name": "帖子标题",
     "guild_id": G1, "parent_id": CH1, "is_thread": True, "author": "?",
     "author_id": "840000000000000011", "content": "帖内一条", "ts": time.time()}]})
time.sleep(0.5)
h = look("帖子标题")["hits"]
ok("帖子记成 thread 不是 channel", bool(h) and h[0]["kind"] == "thread", h)
ok("帖子的面包屑带上父频道", bool(h) and "教程分享区" in (h[0]["where"] or ""), h)
ok("作者名是「?」时不学（那是没解析出来，不是人名）",
   look("?", kind="user")["hits"] == [], look("?", kind="user"))

call("/api/ingest", {"messages": [
    {"msg_id": "nm3", "channel_id": "820000000000000030", "channel_name": "820000000000000030",
     "author": "张三", "author_id": "840000000000000012", "content": "名字就是 id",
     "ts": time.time()}]})
time.sleep(0.5)
ok("频道名等于 ID 时不学（那不是名字）",
   look("820000000000000030")["hits"] == [], look("820000000000000030"))

print("\n8. 旁听开关关掉时，名字照学（学在闸和开关前面）")
call("/api/source/toggle", {"source": "browser", "on": False})
r = call("/api/ingest", {"messages": [
    {"msg_id": "nm4", "channel_id": "820000000000000040", "channel_name": "关着也认的频道",
     "guild_id": G1, "author": "李四", "author_id": "840000000000000013",
     "content": "被丢弃的一条", "ts": time.time()}]})
ok("消息确实被丢了", r.get("ok") is False and "旁听" in (r.get("error") or ""), r)
ok("但名字学到了", ids(look("关着也认的频道")) == ["820000000000000040"], look("关着也认的频道"))
call("/api/source/toggle", {"source": "browser", "on": True})

print("\n9. 工作台 find_target：模型自己查名录，念候选让用户认")
call("/api/config", {"providers": [{"name": "mock", "base_url": "http://127.0.0.1:8899/v1",
                                   "api_key": "sk-test"}],
                     "default_model": {"provider": "mock", "model": "mock-1"},
                     "ai": {"stream": True, "tools": True}})


def tool_once(args, prompt="帮我盯着教程分享区"):
    http(MOCK + "/__reset", {})
    http(MOCK + "/__script", {"queue": [{"tools": [{"name": "find_target", "args": args}]},
                                        {"content": "是这个吗？"}]})
    r = call("/api/ask", {"prompt": prompt})
    c = http(MOCK + "/__calls")["calls"]
    return r, json.loads(c[-1]["tool_out"])


r, out = tool_once({"name": "教程分享区"})
ok("请求成功", r.get("ok") is True, r)
ok("查名录不算改动", r.get("changed") is False, r)
ok("干过的事列出来了，且标成只读", (r.get("acts") or [{}])[0].get("wrote") is False, r.get("acts"))
ok("说的是人话不是函数名", "名字" in ((r.get("acts") or [{}])[0].get("human") or ""), r.get("acts"))
ok("两个同名候选都喂给模型", out.get("count") == 2, out)
ok("候选里带面包屑，模型才念得清是哪个",
   any(h.get("where") == "旅途 › 公共区" for h in out.get("candidates", [])), out)
ok("多个候选时明确「不许自己挑」", "不许自己挑" in (out.get("note") or ""), out)
ok("顺手告诉模型名录里有多少东西", out.get("names_known", {}).get("total", 0) > 0, out)

r, out = tool_once({"name": "教程合集"})
ok("唯一候选也要求先问一句「是这个吗」", "是这个吗" in (out.get("note") or ""), out)
ok("唯一候选把 id 直接给到位", out["candidates"][0]["id"] == TH1, out)

r, out = tool_once({"name": "根本没有的频道"})
ok("查不到就 count=0", out.get("count") == 0, out)
ok("查不到时让用户贴链接（链接里带 ID）", "链接" in (out.get("note") or ""), out)
ok("并说明名录只认浏览器见过的", "见过" in (out.get("note") or ""), out)

r, out = tool_once({"name": "教程分享区", "kind": "thread"})
ok("kind 限定透传给名录（频道不会混进帖子候选）", out.get("count") == 0, out)
r, out = tool_once({"name": "教程", "limit": 1})
ok("limit 透传", out.get("count") == 1, out)
r, out = tool_once({"name": "   "})
ok("名字没给就直说要什么，别空查一遍", "name" in (out.get("error") or ""), out)

print("\n10. 提示词里写清楚了（硬规矩 6：新功能必须同步功能清单）")
pr = {x["key"]: x["text"] for x in call("/api/prompts")["builtin"]}
ok("函数版提示词点了 find_target", "find_target" in pr["wb_tools"], pr["wb_tools"][:200])
ok("文本指令协议里也有（不支持 tools 的模型走这条）", "find_target" in pr["wb_text"],
   pr["wb_text"][:200])
ok("身份提示词里告诉模型「用户不用自己抄 ID」，说名字就查名录",
   "抄 ID" in pr["workbench"] and "find_target" in pr["workbench"], pr["workbench"][:400])
ok("明说候选要等用户确认再建规则", "确认" in pr["wb_tools"], pr["wb_tools"][:400])
ok("find_target 归在只读工具里（查名字不改配置）",
   "find_target" in pr["wb_tools"].split("只读")[-1][:200] or "只读：find_target" in pr["wb_tools"],
   pr["wb_tools"][-800:])

print("\n11. F2：ID 是人还是频道 —— 填错栏位不许保存")


def wb_tool(name, args, prompt="照我说的建规则"):
    """让模型只调一次某个工具，回 (接口响应, 工具自己的返回)。"""
    http(MOCK + "/__reset", {})
    http(MOCK + "/__script", {"queue": [{"tools": [{"name": name, "args": args}]},
                                        {"content": "好了"}]})
    r = call("/api/ask", {"prompt": prompt})
    c = http(MOCK + "/__calls")["calls"]
    return r, json.loads(c[-1]["tool_out"])


def rules_now():
    return call("/api/state")["rules"]


for x in rules_now():                                  # 从干净的规则表开始
    call(f"/api/rules/{x['id']}", None, method="DELETE")

# 11a 数字反查：一串数字丢进 find_target，得说清是人还是频道 + 该填哪栏
r, out = wb_tool("find_target", {"name": U1}, f"{U1} 这是谁")
ok("名录见过的 ID 反查得出类型", out.get("known") is True and out["is"][0]["kind"] == "user", out)
ok("反查顺手点名该填哪个栏位", "只听这些人" in (out["is"][0].get("该填的栏位") or ""), out)
ok("反查的人话里带类型", "用户" in (out.get("note") or ""), out)
r, out = wb_tool("find_target", {"name": "849999999999999999"}, "这个 ID 是谁")
ok("名录没见过就承认判断不出来", out.get("known") is False and "判断不出" in (out.get("note") or ""), out)
ok("并要求直接问用户", "必须问用户" in (out.get("note") or ""), out)

# 11b 人填进「听哪里」→ 拒绝保存，点名正确栏位
n0 = len(rules_now())
r, out = wb_tool("create_rule", {"rule": {"name": "盯老王", "want": "老王说话就叫我",
                                          "channel_ids": [U1]}})
ok("填错栏位：明确没保存", out.get("ok") is False and out.get("saved") is False, out)
ok("规则表里确实没多出东西", len(rules_now()) == n0, rules_now())
ok("点名这是【用户】", any("用户" in e for e in out.get("填错栏位了") or []), out)
ok("点名该填 author_ids", any("author_ids" in e for e in out.get("填错栏位了") or []), out)
ok("说了为什么会错（人和频道 ID 长相一样）", "长相" in (out.get("为什么会错") or ""), out)
ok("给的出路是查清楚或问用户，且禁止猜着重试",
   "find_target" in (out.get("怎么办") or "") and "不许猜" in (out.get("怎么办") or ""), out)
ok("这一步没写库（changed=False）", r.get("changed") is False, r)

# 11c 频道填进「只听这些人」→ 同样拦
r, out = wb_tool("create_rule", {"rule": {"name": "盯教程区", "want": "有人发教程",
                                          "author_ids": [CH1]}})
ok("反过来填错也拦（频道填进人栏）", out.get("saved") is False
   and any("频道" in e and "author_ids" in e for e in out.get("填错栏位了") or []), out)

# 11d 分类不能直接填（消息的 channel_id 永远不等于分类 ID）
r, out = wb_tool("create_rule", {"rule": {"name": "盯公共区", "want": "公共区有动静",
                                          "channel_ids": [CAT1]}})
ok("分类填进频道栏也拦", out.get("saved") is False, out)
ok("并解释分类要展开成一个个频道",
   any("一个个填" in e or "分类" in e for e in out.get("填错栏位了") or []), out)

# 11e 栏位填对 → 存下来，而且 ID 不许被「防编 ID」的闸门洗掉
#     CH2/U2 只在名录里（扩展从侧栏抄的），一条消息都没发过 —— 这是最容易被洗空的情况
ok("前提：CH2 只在名录里、消息库没见过", not any(
    m.get("channel_id") == CH2 for m in call("/api/messages")["messages"]), CH2)
r, out = wb_tool("create_rule", {"rule": {"name": "盯学习小组的教程区",
                                          "want": "有人发教程就叫我",
                                          "channel_ids": [CH2], "author_ids": [U2]}})
ok("栏位填对就保存", out.get("ok") is True and out.get("id"), out)
saved = [x for x in rules_now() if x.get("name") == "盯学习小组的教程区"]
ok("规则真的进库了", len(saved) == 1, rules_now())
ok("名录里的频道 ID 留住了（没被当成模型编的丢掉）",
   bool(saved) and saved[0].get("channel_ids") == [CH2], saved)
ok("名录里的人 ID 也留住了", bool(saved) and saved[0].get("author_ids") == [U2], saved)
ok("所以没被洗成「全部消息」",
   bool(saved) and "没限定频道" not in " ".join(out.get("notes") or []), out)
ok("回话里把听的对象翻成人话", "教程分享区" in json.dumps(out, ensure_ascii=False), out)

# 11f 真编出来的 ID（名录和消息库都没有）照旧丢掉
r, out = wb_tool("create_rule", {"rule": {"name": "编的", "want": "随便",
                                          "channel_ids": ["829999999999999999"]}})
made = [x for x in rules_now() if x.get("name") == "编的"]
ok("编的 ID 还是要丢（这道闸没被拆掉）", bool(made) and made[0].get("channel_ids") == [], made)
ok("并告诉用户怎么让程序认识它（进一次那个服务器）",
   any("名录" in n for n in out.get("notes") or []), out.get("notes"))

# 11g 界面这一侧：/api/rules 保存前过闸，另给一条「名录认错了仍要保存」的路
r = call("/api/rules", {"name": "界面填错的", "want": "x", "channel_ids": [U1]})
ok("界面保存也拦", r.get("ok") is False and r.get("saved") is False, r)
ok("拦下时把错处列给界面画红字", bool(r.get("slot_errs")), r)
ok("拦下就没写库", not any(x.get("name") == "界面填错的" for x in rules_now()), rules_now())
r = call("/api/rules", {"name": "界面强制保存的", "want": "x", "channel_ids": [U1],
                        "force_slots": True})
ok("确认「名录认错了」可以强制保存", r.get("ok") is True and r.get("forced") is True, r)
ok("强制保存的确实进库了", any(x.get("name") == "界面强制保存的" for x in rules_now()), rules_now())

# 11h 界面要能把一串数字显示成人话
lab = r.get("idlabels") or {}
ok("保存响应带 idlabels（界面拿它把 ID 翻人话）", U1 in lab, lab)
ok("标签里有类型和名字", lab.get(U1, {}).get("kind_cn") == "用户"
   and lab[U1].get("name") == "老王", lab.get(U1))
ok("标签里带「本该填哪栏」（界面据此画红线）", lab.get(U1, {}).get("slot") == "author_ids", lab.get(U1))
st = call("/api/state")
ok("/api/state 也带 idlabels（刷新页面后照样翻人话）", U1 in (st.get("idlabels") or {}), st.get("idlabels"))
lab2 = call(f"/api/rules/{[x for x in rules_now() if x.get('name') == '界面强制保存的'][0]['id']}",
            None, method="DELETE").get("idlabels")
ok("删除响应也带 idlabels", isinstance(lab2, dict), lab2)

# 11i 提示词纪律：拿不准必须问用户（硬规矩 6：功能清单也要同步）
ok("建规则字段说明里写了栏位对应关系",
   "author_ids" in pr["wb_tools"] and "人" in pr["wb_tools"], pr["wb_tools"][:600])
ok("工作台身份提示词写死「拿不准先问用户」",
   "问用户" in pr["workbench"] or "先问" in pr["workbench"], pr["workbench"][-600:])
ok("向导提示词也写了（走向导那条路一样不许猜）",
   "问" in pr["wizard"] and ("人还是频道" in pr["wizard"] or "author_ids" in pr["wizard"]),
   pr["wizard"][-600:])

print(f"\n通过 {P} / 失败 {F}")
