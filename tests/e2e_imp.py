"""规则导入 / 导出的回归（v1.9.0）。跑法见 RUN.md：要求 server 在 :8777、库是干净的。

重点盯三件事：
  ① 导出的包能被自己导回去，且认得出「没变」——否则用户每次导入都被告知要覆盖一堆；
  ② 预览（dry_run）**绝不能写库**，这是这个功能唯一的安全网；
  ③ 导入不校验 ID 存不存在。导出方的频道 ID 在导入方库里当然查不到，
     照 sanitize_draft 那套 known_ids 过滤会把规则洗空 —— 那才是最难查的 bug。
"""
import json, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8777"
P = F = 0


def call(path, body=None, method=None, raw=False, headers=False):
    req = urllib.request.Request(BASE + path, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            if headers:
                return dict(r.headers)
            t = r.read().decode()
            return t if raw else json.loads(t)
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode()[:300]}


def ok(name, cond, extra=""):
    global P, F
    if cond:
        P += 1; print("  ok  ", name)
    else:
        F += 1; print("  FAIL", name, extra)


def emsg(x):
    """400 的 body 是转义过的 JSON，直接在原始串里搜中文永远搜不到 —— 先解出来。"""
    try:
        return json.loads(x.get("_body") or "{}").get("error", "")
    except Exception:
        return str(x.get("_body") or "")


def names():
    return [r["name"] for r in call("/api/state")["rules"]]


def clear_rules():
    for r in call("/api/state")["rules"]:
        call("/api/rules/" + str(r["id"]), method="DELETE")


CH = "112233445566778899"        # 本机消息库里绝对没有这个频道的 ID
print("0. 准备：清空规则，建两条本机规则")
clear_rules()
call("/api/rules", {"name": "盯空投", "keywords_any": ["空投"], "channel_ids": [CH],
                    "min_len": 4, "enabled": 1})
call("/api/rules", {"name": "本机独有", "keywords_any": ["只有我有"], "enabled": 1})
ok("两条规则都在", names() == ["盯空投", "本机独有"], names())

print("1. 导出")
h = call("/api/rules/export", headers=True)
ok("是附件下载", "attachment" in (h.get("Content-Disposition") or ""), h.get("Content-Disposition"))
ok("文件名带版本号", "-v" in (h.get("Content-Disposition") or ""), h.get("Content-Disposition"))
ok("不许被缓存", (h.get("Cache-Control") or "") == "no-store", h.get("Cache-Control"))
pack = call("/api/rules/export")
ok("有 schema 名", pack.get("schema") == "dcwatch.rules/1", pack.get("schema"))
ok("带程序版本号", bool(pack.get("version")) and pack["version"][0].isdigit(), pack.get("version"))
ok("带导出时间和条数", bool(pack.get("exported_at")) and pack.get("count") == 2, pack.get("count"))
r0 = [x for x in pack["rules"] if x["name"] == "盯空投"][0]
ok("规则字段全在", r0["keywords_any"] == ["空投"] and r0["channel_ids"] == [CH] and r0["min_len"] == 4, r0)
ok("带 enabled", r0.get("enabled") == 1, r0.get("enabled"))
ok("不导出 id 和命中数（那是本机的账）", "id" not in r0 and "hits" not in r0, sorted(r0)[:4])

print("2. 原样导回去 = 一条都没变（导出/导入是可逆的）")
p = call("/api/rules/import", {"data": pack})
ok("预览成功", p.get("ok") and p.get("dry_run") is True, p)
ok("两条都算「没变」", p["summary"] == {"new": 0, "overwrite": 0, "same": 2, "remove": 0}, p["summary"])
ok("认出来自哪个版本", p.get("from_version") == pack["version"], p.get("from_version"))
ok("合并模式不列删除", p["removes"] == [], p["removes"])

print("3. 预览绝不写库")
pack2 = json.loads(json.dumps(pack))
pack2["rules"] = [dict(r0, keywords_any=["空投", "白名单"], min_len="12"),
                  {"name": "文件里新来的", "keywords_any": ["新"], "enabled": 0}]
p = call("/api/rules/import", {"data": pack2, "mode": "merge"})
ok("算出 1 新增 1 覆盖", p["summary"]["new"] == 1 and p["summary"]["overwrite"] == 1, p["summary"])
ok("库里还是原来两条", names() == ["盯空投", "本机独有"], names())
ok("现有规则没被改", call("/api/state")["rules"][0]["keywords_any"] == ["空投"],
   call("/api/state")["rules"][0]["keywords_any"])

print("4. 预览把「会覆盖掉什么」说清楚")
it = [x for x in p["plan"] if x["name"] == "盯空投"][0]
ok("覆盖项标 overwrite", it["act"] == "overwrite", it["act"])
ok("指到本机那条的 id", it.get("target_id") == call("/api/state")["rules"][0]["id"], it.get("target_id"))
ch = "；".join(it["changes"])
ok("列出关键词的变化", "关键词" in ch and "白名单" in ch, ch)
ok("列出字数的变化 4 → 12", "4 → 12" in ch, ch)
ok("字段名是人话不是 keywords_any", "keywords_any" not in ch, ch)
nw = [x for x in p["plan"] if x["name"] == "文件里新来的"][0]
ok("新增项标 new 且没有 changes", nw["act"] == "new" and nw["changes"] == [], nw)
ok("新增项带 enabled 状态", nw["enabled"] == 0, nw)

print("5. 替换模式才列出「会删掉谁」")
p = call("/api/rules/import", {"data": pack2, "mode": "replace"})
ok("列出本机多出来的那条", [x["name"] for x in p["removes"]] == ["本机独有"], p["removes"])
ok("summary 里也有 remove 计数", p["summary"]["remove"] == 1, p["summary"])
ok("替换预览同样不写库", names() == ["盯空投", "本机独有"], names())

print("6. 真导入（合并）")
p = call("/api/rules/import", {"data": pack2, "mode": "merge", "dry_run": False})
ok("dry_run 标记为假", p.get("dry_run") is False, p.get("dry_run"))
ok("返回最新规则列表", [x["name"] for x in p["rules"]] == ["盯空投", "本机独有", "文件里新来的"],
   [x["name"] for x in p["rules"]])
db = {x["name"]: x for x in call("/api/state")["rules"]}
ok("覆盖生效：关键词更新了", db["盯空投"]["keywords_any"] == ["空投", "白名单"], db["盯空投"])
ok("字符串数字转成了 int", db["盯空投"]["min_len"] == 12, db["盯空投"]["min_len"])
ok("本机独有的规则留着（合并不删）", "本机独有" in db, list(db))
ok("新规则按文件里的开关停用", db["文件里新来的"]["enabled"] == 0, db["文件里新来的"]["enabled"])
lg = " ".join(x["text"] for x in call("/api/logs")["logs"])
ok("运行日志里记了这次导入", "导入规则" in lg and "覆盖" in lg, lg[:120])

print("7. 导入方库里查不到的频道 ID 必须保留（不能照 known_ids 洗）")
ok("频道 ID 还在", db["盯空投"]["channel_ids"] == [CH], db["盯空投"]["channel_ids"])
t = call("/api/rules/test", {"rule": db["盯空投"],
                             "sample": {"channel_id": CH, "content": "有空投名额，谁要谁来，速度点别错过"}})
ok("导进来的规则真能命中", t.get("match") is True, t)
t2 = call("/api/rules/test", {"rule": db["盯空投"], "sample": {"channel_id": "999", "content": "有空投"}})
ok("别的频道不命中（条件确实生效了）", t2.get("match") is False, t2)

print("8. 覆盖保留本机的命中数，不把统计清零")
rid = db["盯空投"]["id"]
call("/api/ingest", {"messages": [{"msg_id": "imp-1", "channel_id": CH, "author": "u",
                                   "author_id": "5", "content": "有空投名额，谁要谁来，速度点别错过", "ts": 0}]})
time.sleep(0.8)
hits = {x["id"]: x["hits"] for x in call("/api/state")["rules"]}.get(rid)
ok("先命中一次", hits == 1, hits)
p = call("/api/rules/import", {"data": pack2, "mode": "merge", "dry_run": False})
ok("再导一次后命中数还在", {x["id"]: x["hits"] for x in call("/api/state")["rules"]}.get(rid) == 1,
   {x["id"]: x["hits"] for x in call("/api/state")["rules"]})
ok("重复导入不会造出重复规则", len(call("/api/state")["rules"]) == 3, names())
it = [x for x in call("/api/rules/import", {"data": pack2})["plan"] if x["name"] == "盯空投"][0]
ok("预览里能看到本机已命中多少", it.get("hits") == 1, it.get("hits"))

print("9. 脏数据洗干净，并且说清丢了什么")
dirty = {"schema": "dcwatch.rules/1", "rules": [
    {"name": "脏的", "action": "没这个动作", "channel_ids": ["abc", CH], "lol": 1,
     "kinds": ["msg", "外星人"], "ignore_bots": "yes"}]}
p = call("/api/rules/import", {"data": dirty})
n = "；".join(p["plan"][0]["notes"])
ok("非法动作退回 notify 并说了", "notify" in n or "只提醒" in n, n)
ok("不像 ID 的值被丢掉并说了", "abc" in n, n)
ok("不认识的字段被丢掉并说了", "lol" in n, n)
p = call("/api/rules/import", {"data": dirty, "dry_run": False})
d = [x for x in call("/api/state")["rules"] if x["name"] == "脏的"][0]
ok("落库后动作是 notify", d["action"] == "notify", d["action"])
ok("落库后只留合法 ID", d["channel_ids"] == [CH], d["channel_ids"])
ok("落库后 kinds 只留合法值", d["kinds"] == ["msg"], d["kinds"])
ok("没有 lol 这种字段", "lol" not in d, sorted(d)[:5])
call("/api/rules/" + str(d["id"]), method="DELETE")

print("10. 没名字的规则不会变成一片空白")
p = call("/api/rules/import", {"data": [{"keywords_any": ["x"]}]})
ok("补了个默认名字", bool(p["plan"][0]["name"].strip()), p["plan"][0]["name"])
ok("裸数组也能读，但要提示没有 schema 头", any("schema" in x for x in p["notes"]), p["notes"])

print("11. 读不了的文件要说人话，不是 500")
bad = call("/api/rules/import", {"data": "这不是 json"})
ok("坏 JSON 回 400", bad.get("_status") == 400, bad)
ok("并且告诉他该选哪个文件", "导出规则" in emsg(bad), emsg(bad))
bad = call("/api/rules/import", {"data": {"schema": "other.app/1", "rules": [{"name": "x"}]}})
ok("别人家的规则包回 400", bad.get("_status") == 400 and "dcwatch" in emsg(bad), bad)
bad = call("/api/rules/import", {"data": {"schema": "dcwatch.rules/1", "rules": []}})
ok("空包回 400 并说清", bad.get("_status") == 400 and "一条" in emsg(bad), bad)
bad = call("/api/rules/import", {"data": 42})
ok("根本不是规则包回 400", bad.get("_status") == 400, bad)
bad = call("/api/rules/import", {"data": pack, "mode": "zzz"})
ok("mode 只认 merge / replace", bad.get("_status") == 400, bad)
p = call("/api/rules/import", {"data": {"schema": "dcwatch.rules/9", "rules": [{"name": "未来的"}]}})
ok("大版本相同的未来 schema 收下但提醒", p.get("ok") and any("认不出" in x for x in p["notes"]), p.get("notes"))

print("12. 替换模式真的会删")
only = {"schema": "dcwatch.rules/1", "rules": [{"name": "唯一保留", "keywords_any": ["a"]}]}
p = call("/api/rules/import", {"data": only, "mode": "replace", "dry_run": False})
ok("库里只剩文件里那一条", names() == ["唯一保留"], names())
ok("summary 报了删除条数", p["summary"]["remove"] == 3, p["summary"])

print("13. 导入来的规则要留下痕迹（诊断包里看得见，但不跟着导出走）")
clear_rules()
call("/api/rules", {"name": "手填的", "keywords_any": ["手填"], "enabled": 1})
mk = {"schema": "dcwatch.rules/1", "version": "9.9.9",
      "rules": [{"name": "手填的", "keywords_any": ["被覆盖"]},
                {"name": "导入的", "keywords_any": ["新来的"]}]}
call("/api/rules/import", {"data": mk})                      # 先预览一次
rs = {r["name"]: r for r in call("/api/state")["rules"]}
ok("预览不盖戳", "imported_at" not in rs["手填的"], sorted(rs["手填的"])[:6])
call("/api/rules/import", {"data": mk, "dry_run": False})
rs = {r["name"]: r for r in call("/api/state")["rules"]}
ok("新增的那条盖了戳", bool(rs["导入的"].get("imported_at")), rs["导入的"].get("imported_at"))
ok("覆盖的那条也盖了戳", bool(rs["手填的"].get("imported_at")), rs["手填的"].get("imported_at"))
ok("戳里记了包的版本", rs["导入的"].get("imported_from") == "v9.9.9", rs["导入的"].get("imported_from"))
call("/api/rules", {"name": "自己填的第二条", "keywords_any": ["b"], "enabled": 1})
rs = {r["name"]: r for r in call("/api/state")["rules"]}
ok("自己填的没有戳", not rs["自己填的第二条"].get("imported_at"), rs["自己填的第二条"])

exp = json.loads(call("/api/rules/export", raw=True))
ok("导出的包里不带本机的戳", all("imported_at" not in r and "imported_from" not in r
                                 for r in exp["rules"]), exp["rules"][0])
p = call("/api/rules/import", {"data": exp})
allnotes = "；".join(n for x in p["plan"] for n in x["notes"]) + "；".join(p["notes"])
ok("自己导出再导回去不会抱怨未知字段", "不认识的字段" not in allnotes, allnotes)
ok("自己导出再导回去全是「没变」", p["summary"]["same"] == len(exp["rules"]), p["summary"])

r = rs["导入的"]
body = {k: v for k, v in r.items() if k not in ("imported_at", "imported_from")}
body["keywords_any"] = ["改过了"]
call("/api/rules", body)                                      # 界面提交里没有这两个字段
r2 = {x["name"]: x for x in call("/api/state")["rules"]}["导入的"]
ok("界面编辑后戳还在", r2.get("imported_at") == r["imported_at"], r2.get("imported_at"))
ok("界面编辑真的改到了内容", r2["keywords_any"] == ["改过了"], r2["keywords_any"])

d = call("/diagnose.txt", raw=True)
seg = d.split("[4] 规则")[1].split("[4.5]")[0]
ok("[4] 段印了这条是导入来的", "从规则包导入" in seg, seg[:200])
ok("[4] 段印了包的版本", "v9.9.9" in seg, seg[:400])
ok("[4] 段有一行总结最近一次导入", "最近一次导入" in seg, seg[:200])
ok("总结报对了导入 / 总条数", "2/3 条是导入来的" in seg, seg[:200])
ok("手填的那条不印来路", seg.count("从规则包导入") == 2, seg.count("从规则包导入"))

print(f"\n通过 {P} / 失败 {F}")
