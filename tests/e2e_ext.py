"""批量提取模板：存储 + 导出 / 导入的回归（v1.9.3）。跑法见 RUN.md（server 在 :8777，库干净）。

模板走的是跟规则包同一套路子，所以这里盯的也是同样那三件事，外加两件模板独有的：
  ① 预览（dry_run）**绝不能写库** —— 这是硬规矩 10 那道闸唯一的安全网；
  ② 自己导出再导回去，必须全是「没变」，不能让用户每次导入都被吓一跳；
  ③ 导入**不校验频道 ID 存不存在**（对方的频道 ID 在本机库里当然查不到），只要求纯数字；
  ④ 本机戳 imported_at / imported_from **不能跟着导出去**（规则那边 e2e_imp 第 13 节同款）；
  ⑤ 把**规则包**喂给模板导入口必须被明确挡下并指路，反过来也一样 —— 两个包长得太像了。
"""
import json, urllib.request, urllib.error

BASE = "http://127.0.0.1:8777"
P = F = 0


def call(path, body=None, method=None, raw=False):
    req = urllib.request.Request(BASE + path, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
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


def tpls():
    return call("/api/state")["config"].get("extract_templates") or []


def setts(items):
    return call("/api/config", {"extract_templates": items})


def names():
    return [t["name"] for t in tpls()]


CH = "112233445566778899"        # 本机消息库里绝对没有这个频道的 ID


print("1. 存模板：形状不对的进不来")
setts([])
ok("一开始一个都没有", tpls() == [], tpls())
r = setts([{"name": "挑密钥", "want": "所有人发的兑换码/密钥", "channel_id": CH, "limit": 800},
           {"name": "", "want": "没名字的"},
           {"name": "没说要什么", "want": "   "},
           "这不是对象",
           {"name": "只看命中", "want": "命中过的里面有什么", "only_matched": True,
            "author_contains": "admin"}])
ok("保存返回 ok", r.get("ok"), r)
ok("空名字 / 空 want / 不是对象的三条都被丢掉", names() == ["挑密钥", "只看命中"], names())
t0 = tpls()[0]
ok("每条自动有 id", bool(t0.get("id")), t0)
ok("字段原样存下来", (t0["want"].startswith("所有人发的") and t0["channel_id"] == CH
                     and t0["limit"] == 800), t0)
ok("布尔和字符串都没走样", (tpls()[1]["only_matched"] is True
                          and tpls()[1]["author_contains"] == "admin"), tpls()[1])

print("\n2. 越界的值被夹回来，不是报错")
setts([{"name": "夹一下", "want": "x", "limit": 999999, "channel_id": "不是数字"},
       {"name": "夹一下2", "want": "y", "limit": 0}])
ok("limit 上限夹到 2000", tpls()[0]["limit"] == 2000, tpls()[0]["limit"])
ok("limit 下限夹到 1 以上", tpls()[1]["limit"] >= 1, tpls()[1]["limit"])
ok("不像 ID 的频道被清空成「不限」", tpls()[0]["channel_id"] == "", tpls()[0])

print("\n3. 导出：schema 头对、本机的账不跟着走")
setts([{"name": "挑密钥", "want": "所有人发的兑换码/密钥", "channel_id": CH, "limit": 800,
        "note": "每周五跑一次"},
       {"name": "只看命中", "want": "命中过的里面有什么", "only_matched": True}])
exp = json.loads(call("/api/extract/export", raw=True))
ok("schema 是 dcwatch.extract/1", exp["schema"] == "dcwatch.extract/1", exp.get("schema"))
ok("带程序版本号", bool(exp.get("version")), exp.get("version"))
ok("count 和条数对得上", exp["count"] == len(exp["templates"]) == 2, exp["count"])
ok("导出的模板里没有 id", all("id" not in t for t in exp["templates"]), exp["templates"][0])
ok("备注跟着走", exp["templates"][0]["note"] == "每周五跑一次", exp["templates"][0])

print("\n4. 预览绝不写库（这是那道闸唯一的安全网）")
pack = {"schema": "dcwatch.extract/1", "version": "9.9.9", "templates": [
    {"name": "挑密钥", "want": "改成别的了", "limit": 100},          # 同名 → 覆盖
    {"name": "别人的新模板", "want": "找所有邀请码", "channel_id": "998877665544332211"},
]}
before = json.dumps(tpls(), sort_keys=True)
pv = call("/api/extract/import", {"data": pack})                      # 不传 dry_run
ok("dry_run 默认就是真", pv.get("dry_run") is True, pv.get("dry_run"))
ok("预览完库一点没动", json.dumps(tpls(), sort_keys=True) == before)
ok("算出 1 新增 1 覆盖", (pv["summary"]["new"], pv["summary"]["overwrite"]) == (1, 1), pv["summary"])
ov = [p for p in pv["plan"] if p["act"] == "overwrite"][0]
ok("覆盖项列出了人话 diff", any("要提取什么" in c for c in ov["changes"]), ov["changes"])
ok("diff 里也说了条数会变", any("最多读几条" in c for c in ov["changes"]), ov["changes"])
nw = [p for p in pv["plan"] if p["act"] == "new"][0]
ok("新增项提醒了频道是对方的", any("对方机器上的频道" in n for n in nw["notes"]), nw["notes"])
ok("merge 模式不删本机多出来的", pv["removes"] == [], pv["removes"])

print("\n5. 确认后才真写，且盖上本机戳")
ap = call("/api/extract/import", {"data": pack, "dry_run": False})
ok("落库成功", ap.get("ok") and ap.get("dry_run") is False, ap.get("dry_run"))
ok("本机独有的那条还在", "只看命中" in names(), names())
by = {t["name"]: t for t in tpls()}
ok("同名的被覆盖了", by["挑密钥"]["want"] == "改成别的了", by["挑密钥"])
ok("覆盖没换 id（界面上不会跳位）", by["挑密钥"]["id"] == json.loads(before)[0]["id"], by["挑密钥"]["id"])
ok("新的那条进来了", by["别人的新模板"]["channel_id"] == "998877665544332211", by.get("别人的新模板"))
ok("导入来的盖了戳", by["别人的新模板"].get("imported_from") == "v9.9.9", by["别人的新模板"])
ok("没动过的那条不盖戳", "imported_at" not in by["只看命中"], by["只看命中"])

print("\n6. 本机戳不跟着导出去（导给别人，别把「我从谁那儿导的」也发过去）")
exp2 = json.loads(call("/api/extract/export", raw=True))
flat = json.dumps(exp2["templates"], ensure_ascii=False)
ok("导出的包里没有 imported_at", "imported_at" not in flat, flat[:200])
ok("导出的包里没有 imported_from", "imported_from" not in flat, flat[:200])

print("\n7. 自己导出再导回去 = 全是「没变」")
back = call("/api/extract/import", {"data": exp2})
ok("一条都没变", back["summary"]["same"] == len(exp2["templates"]), back["summary"])
ok("不抱怨未知字段", all("不认识的字段" not in n for p in back["plan"] for n in p["notes"]),
   [p["notes"] for p in back["plan"]])
ok("原始文本（字符串形式）也能吃", call("/api/extract/import",
   {"data": json.dumps(exp2, ensure_ascii=False)})["summary"]["same"] == len(exp2["templates"]))

print("\n8. replace 会删本机多出来的，但也得先预览")
rp = call("/api/extract/import", {"data": pack, "mode": "replace"})
ok("预览里列出了会被删的", [x["name"] for x in rp["removes"]] == ["只看命中"], rp["removes"])
ok("预览没真删", "只看命中" in names(), names())
call("/api/extract/import", {"data": pack, "mode": "replace", "dry_run": False})
ok("确认后真删了", "只看命中" not in names(), names())
ok("包里的两个都在", sorted(names()) == sorted(["别人的新模板", "挑密钥"]), names())

print("\n9. 频道 ID 只要求纯数字，不校验本机有没有")
r = call("/api/extract/import", {"data": {"schema": "dcwatch.extract/1", "templates": [
    {"name": "带个怪频道", "want": "x", "channel_id": "#公告频道"}]}, "dry_run": False})
got = {t["name"]: t for t in tpls()}["带个怪频道"]
ok("不像 ID 的被清空", got["channel_id"] == "", got)
ok("而且说清楚了为什么", any("不像频道 ID" in n for p in r["plan"] for n in p["notes"]),
   [p["notes"] for p in r["plan"]])
r2 = call("/api/extract/import", {"data": {"schema": "dcwatch.extract/1", "templates": [
    {"name": "对方的频道", "want": "y", "channel_id": CH}]}, "dry_run": False})
ok("本机查不到的纯数字 ID 照样留着（洗空才是最难查的 bug）",
   {t["name"]: t for t in tpls()}["对方的频道"]["channel_id"] == CH)

print("\n10. 喂错文件：说清楚是哪一种，并指路")
bad = call("/api/extract/import", {"data": "这不是 json"})
ok("不是 JSON → 400 且教他选哪个文件", bad.get("_status") == 400 and "导出模板" in emsg(bad), emsg(bad))
rules_pack = json.loads(call("/api/rules/export", raw=True))
wrong = call("/api/extract/import", {"data": rules_pack})
ok("把规则包喂进来 → 400", wrong.get("_status") == 400, wrong)
ok("而且明说这是规则包", "规则包" in emsg(wrong), emsg(wrong))
ok("并指到「监听规则」页", "监听规则" in emsg(wrong), emsg(wrong))
empty = call("/api/extract/import", {"data": {"schema": "dcwatch.extract/1", "templates": []}})
ok("空包 → 400", empty.get("_status") == 400 and "一个模板都没有" in emsg(empty), emsg(empty))
badmode = call("/api/extract/import", {"data": pack, "mode": "覆盖"})
ok("mode 写错 → 400", badmode.get("_status") == 400, badmode)
nohead = call("/api/extract/import", {"data": [{"name": "裸数组", "want": "z"}]})
ok("没 schema 头的裸数组也能吃，但会说一句", nohead.get("ok") and
   any("没有 schema 头" in n for n in nohead["notes"]), nohead.get("notes"))

print("\n11. 诊断包 [5.5] 段（硬规矩 5：新字段必须印出来）")
setts([])
call("/api/extract/import", {"data": {"schema": "dcwatch.extract/1", "version": "9.9.9",
     "templates": [{"name": "诊断里的模板", "want": "找兑换码", "channel_id": CH,
                    "limit": 123, "only_matched": True}]}, "dry_run": False})
d = call("/diagnose.txt", raw=True)
seg = d.split("[5.5]")[1].split("[6]")[0]
ok("诊断包有 [5.5] 段", "批量提取的模板" in d, d[:80])
ok("印了模板名", "诊断里的模板" in seg, seg[:200])
ok("印了要提取什么", "找兑换码" in seg, seg[:200])
ok("印了条数和频道", "123" in seg and CH in seg, seg[:200])
ok("印了这条是导入来的", "从模板包导入" in seg and "v9.9.9" in seg, seg[:300])
setts([])
seg2 = call("/diagnose.txt", raw=True).split("[5.5]")[1].split("[6]")[0]
ok("一个模板都没有时也说人话", "一个都没有" in seg2, seg2[:120])

print(f"\n通过 {P} / 失败 {F}")
