# 给接手这个项目的 AI

这个文件是**项目记忆**。`AGENTS.md` 这个名字会被 Claude Code / BrowserCode / Cursor 之类的
工具自动读取，所以你现在大概是被动读到的 —— 很好，这就是它存在的目的。

**刚被换到新账号 / 新窗口的，先读 `START_HERE.md`**（5 分钟，写清了头四件事该干什么、
凭据怎么要、这个运行环境有哪些坑）。然后：先读这份（项目怎么运作、哪些坑别再踩），
再读 `HANDOFF.md`（上一轮做到哪、下一步做什么），`CLAUDE.md` 是用户定的工作协议
（会话开始/结束要做什么），照着执行。

**仓库里的每一份东西都是为了「不需要聊天记录也能接上」而存在的**：
`NOW.md` 手停在哪一步 · `START_HERE.md` 换号第一页 · 这份 项目记忆 · `HANDOFF.md` 进度与坑 · `CLAUDE.md` 协议 ·
`tests/` 496 条回归（代码说不了谎的那部分）· `release/` 一份现成的成品 zip
（用户没有 AI 也能自己下）。发现有什么只存在于聊天里，立刻补进来。

## 这是什么

dcwatch：盯 Discord 指定频道的消息，按规则筛，命中了本机弹窗 + 转发到微信/Telegram/Webhook，
可选让大模型判分和打标签。单进程 aiohttp + SQLite + 单文件网页界面 + 一个 MV3 浏览器扩展。
用户是 Windows 11，**不用命令行**，双击 `启动.bat` 是唯一可靠的启动路径。

## 用户情况（决定了很多设计）

- 普通 Discord 账号，**不是服主也不是管理员**，拿不到能进那个服务器的 bot。
  所以默认走**浏览器旁听**：MV3 扩展读 discord.com 的 DOM，POST 到 `/api/ingest`。
  Bot Token / User Token 是备选路线（后者违反 Discord ToS，已加二次确认且只读）。
- 他盯的频道是白嫖 AI API key 的社群，关心的是「谁发了 key / 邀请码 / 名额 / 开了新帖」。
- 界面要 Claude 风格：`#faf9f5` 底、`#c96442` 强调色、衬线标题、深浅双色。
- 程序要轻量，**不用 Electron / Node**。
- 他会把原始报错和诊断包原样贴过来。先看编码/解析问题，再猜别的。
- 报错要给「症状 → 怎么办」对照表，不要给他命令行让他自己敲。

## 文件地图

```
NOW.md         **进行中**的状态，粒度到"步"（HANDOFF 粒度到"轮"）。换窗口后第一个读
agent/save.py  存盘：commit + 推 GitHub + 把 .md 同步回工作区快照。不进交付 zip
START_HERE.md  换号 / 换窗口后第一个读的东西（给 AI 的 5 分钟上手页）
release/       最新一个打好的交付 zip，供用户直接从 GitHub 网页下载（只留最新一个）
server.py      单进程后端。Gateway 监听 / 规则引擎 / 模型调用 / SQLite / 所有 HTTP 接口
ui.html        整个界面就这一个文件（收信箱、AI 工作台、批量提取、监听规则、模型接入、
               通知与转发、设置、运行日志）。没有构建步骤，改完刷新即可
extension/     MV3 扩展：content.js 解析 DOM 并上报，background.js 发请求 + 体检，
               popup 自检，页面右下角常驻状态药丸
freeport.py    独立无依赖。启动前清掉赖着端口的僵尸进程（启动.bat 会先跑它）
启动.bat 停止.bat build.bat   给不用命令行的人的入口；build.bat 调 PyInstaller 打 exe
tests/         回归套件，见下。改代码前先跑一遍，改完再跑一遍
sounds/        提示音 wav
```

## 怎么跑、怎么测

```bash
pip install aiohttp          # 唯一依赖
python3 server.py            # → http://127.0.0.1:8777
DCWATCH_DB=/tmp/x.db python3 server.py    # 测试时用，别污染真配置
```

回归（**改 server.py 必跑**，共 566 条）：

```bash
cd tests && ./runall.sh e2e.py e2e_ai.py           # 一次两三套，别全塞一批（会超时）
./runall.sh e2e_multi.py e2e_wiz.py
./runall.sh e2e_v17.py e2e_diag.py
./runall.sh e2e_wb.py e2e_imp.py     # wb 94 条 / imp 74 条
./runall.sh e2e_ext.py               # 提取模板 53 条
```

改 `extension/content.js` 必跑浏览器侧回归（43 + 16 条，需要一个 CDP 会话）：

```js
const m = await import(process.cwd()+"/tests/content_test.mjs?t="+Date.now())
await m.run(session)        // 43 条：解析、折叠消息、子区、私信、去重、合批、心跳
await m.runFresh(session)   // 16 条：靠消息 ID 里的时间戳判新旧，别把翻出来的历史当新消息
```

细节和每套覆盖什么在 `tests/RUN.md`，**踩坑清单也在那里，动手前扫一眼能省一小时**。

## 怎么出交付包给用户

用户拿到的是一个 zip，他解压后双击 `启动.bat`。**交付包里不含 `tests/`、`AGENTS.md`、
`CLAUDE.md`、`HANDOFF.md` 和两个点文件** —— 那些是给你和给 git 的，塞进去只会让他困惑。
干净的 27 个文件，400 KB 左右（v1.10.0 起多了 presets/，服务端 /api/presets 运行时读它，不能删）（`README.md` 要留下，那是给用户看的说明书）：

```bash
V=$(grep -oP 'VERSION = "\K[^"]+' server.py)          # 版本号只有一个来源：server.py
rm -rf /tmp/pack && mkdir -p /tmp/pack/dcwatch
cp -r ./. /tmp/pack/dcwatch/
cd /tmp/pack/dcwatch && rm -rf .git tests release .gitignore .gitattributes __pycache__ \
    AGENTS.md CLAUDE.md HANDOFF.md START_HERE.md NOW.md agent
find . -type f | wc -l                                # 必须是 27（v1.10.0 起；少了就是同步把 .bat 弄丢了）
cd /tmp/pack && python3 -m zipfile -c "dcwatch-v$V.zip" dcwatch   # 环境里没有 zip 命令
```

扩展单独还有一个包（10 个文件）：`cd extension && python3 -m zipfile -c ../dcwatch-extension-v$V.zip .`，
**只在扩展真改了的时候才出**（见硬规矩 2）。两个包都放进 `outputs/`，把旧版本的 zip 删掉。**顺手把 `release/` 里那份也换成新的**
（那是用户在没有 AI 的时候唯一的下载入口，见 `release/README.md`）。

打包前必做两件事：`rm -rf __pycache__`（`py_compile` 会留 .pyc，混进过交付包），
以及确认 `.bat` 还是 **GBK + CRLF**：

```bash
python3 -c "d=open('启动.bat','rb').read(); print(b'\r\n' in d, d.decode('gbk')[:20])"
```

## 硬规矩（违反过，代价都付过了）

1. **界面绝不能拿假数据冒充成功**。早期版本把任何请求失败吞成 null 然后编 6 个假模型显示
   「演示：6 个模型」，用户选了假模型后全线报错。现在 `S.live` 一旦为真永不回退，失败必须
   toast 出真实状态码和响应体。演示数据必须带「（演示）」前缀。
2. **改了 `extension/` 就必须 bump `manifest.json` 的 version 和 server.py 的 `EXT_MIN`**，
   并在回复里告诉用户该看到几点几。反过来，扩展没动就别碰这两个 —— 白让他重装一遍比不修还糟。
3. **交付物一律带版本号**（`dcwatch-v1.8.0.zip`），旧版删掉。他拿错版本这件事发生过三次，
   来源分别是：GitHub 上没推的 commit、界面里下载到的是他自己磁盘上的旧 extension 文件夹、
   Chrome 的 load unpacked 记的是老文件夹路径。
4. **所有出网调用必须走 `App.rq()`**（`trust_env=True` + `net.proxy` 配置）。
   否则国内直连 api.openai.com 必超时，用户只看到「拉不到模型」。
5. **新增任何规则字段，同时问：诊断包里印出来了吗？** 有个字段进了 `match()` 却没进
   `/diagnose.txt` 的 `[4]` 段，结果排查时只能靠猜规则名字。
6. **每一条调用大模型的路径都要单独检查有没有系统提示词**。工作台曾经一句身份提示都没有，
   用户贴频道链接说「帮我监听」，模型回「我无法访问第三方平台，建议你用 Zapier」。
7. **微信只走 Server酱 / 企业微信官方通道**。itchat / wechaty 直推个人微信＝封号风险，已否决。
8. **只支持 Chrome / Edge**。火狐标准版禁止长期安装未签名扩展，且不支持 MV3 的
   `background.service_worker`，已彻底放弃，别再加回来。
9. **Windows 的 .bat 必须存成 GBK + CRLF**。存 UTF-8 的话中文控制台按 GBK 解析，乱码里会蹦出
   `&`，cmd 当成命令分隔符，用户看到的报错是「'aiohttp' 不是内部或外部命令」。
   echo 文本里的 `>` 要写成 `^>`。`.gitattributes` 里 `*.bat -text` 别删。
10. **任何会覆盖或删除用户手填内容的操作，先预览再落库**（也是「工作台的模型没有 import_rules
    这只手」的原因：模型自己吃一个文件就绕过了那道闸）。`/api/rules/import` 的 `dry_run`
    默认就是真，界面必须先把「哪几条会被覆盖、覆盖掉什么、哪几条会被删」摆给他看，
    点确认才第二次请求真写。他填规则填了一晚上，不能被一个文件默默盖掉。
11. **PyInstaller 打 exe 只能在用户本机做**（不能在 Linux 交叉编译），而且它很重：
    `build.bat` 有防重复运行的锁，因为用户曾经「没反应」就双击第二次，两个 PyInstaller
    同时啃内存把整机卡死一小时。

## 排查用户报「它没提醒我」的固定顺序

1. 让他导诊断包（界面「运行日志」页 → 导出诊断），看 `[0] 一眼结论` 和
   `[4.5] 拿真实消息试算` 两段。前者是程序自己查出来的常见原因，后者拿库里真消息逐条跑
   `match()` 并把卡点译成人话（指到具体哪个输入框）。**别靠肉眼比对规则条件**。
2. 规则第 0 项 `kinds`：「有人发消息」和「有人开新帖」是两个独立勾选。只勾后者，
   频道里聊翻天也不会响。用户真踩过这个。
3. 扩展重载后**必须回 Discord 页面按 F5**。不按的话页面里跑的是旧脚本，它还会送心跳，
   看着像在工作 —— 最坑的状态。程序里叫 `stale_ctx`，会被标记出来。
4. 规则默认 `dm=False`，私信不命中；这不是 bug。
5. 三个版本号必须一致：程序、`EXT_MIN`、磁盘上那份扩展。`http://127.0.0.1:8777/version`
   是**不依赖界面、不依赖 JS、不查库**的自证页，界面坏了它也说实话。旧版打开是 404，
   这本身就是「你启动的是另一个文件夹里的旧程序」的证据。

## 交接给下一个人之前

`CLAUDE.md` 要求：会话结束前用固定结构覆盖写 `HANDOFF.md`，然后连代码一起提交推送。
**推送凭据不在仓库里，也不该在**（用户手上的 GitHub PAT，让他现给 —— 换账号后老的一定拿不到，
别翻工作区找，直接要）。
`.gitignore` 排除了 `*.db` —— 配置和 API key 全在 `dcwatch.db` 里，别让它进仓库。
已经核对过：全仓库历史零密钥泄露（`git rev-list --all` 上 grep 过 `sk-` / `Bearer` / `api_key`）。

## 双模型流程（2026-07-31 起）
思考和实现分开：Claude 窗口只写方案（`PLAN_<任务>.md`，精确到行号和代码片段），
实现窗口（Kimi K2 等快模型）**只照 PLAN 施工**，不重构、不顺手改、PLAN 没写的不做。
你若是实现窗口：NOW.md 状态会指明当前 PLAN 文件，读它，按它的验收命令自测，全绿才算完。
你若是 Claude：写 PLAN 时把「不许做的事」也写死，实现者会严格照办。
