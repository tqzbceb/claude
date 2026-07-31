"""假 webhook 接收端：把收到的东西按行写进 /tmp/bcode/echo.jsonl。"""
import json
from aiohttp import web

async def any_(req):
    body = await req.text()
    with open("/tmp/bcode/echo.jsonl", "a") as f:
        f.write(json.dumps({"path": req.path, "body": body[:8000]}, ensure_ascii=False) + "\n")
    if "fail" in req.path:
        return web.json_response({"error": "boom"}, status=500)
    return web.json_response({"ok": True, "code": 0, "errcode": 0})

app = web.Application()
app.router.add_route("*", "/{tail:.*}", any_)
web.run_app(app, host="127.0.0.1", port=8898, print=None)
