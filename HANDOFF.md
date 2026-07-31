# HANDOFF

> **第一次接手这个项目？先读根目录的 `AGENTS.md`**（项目怎么运作、用户是谁、十一条硬规矩、
> 排查「它没提醒我」的固定顺序），再读这份看进度，`CLAUDE.md` 是工作协议。
> 回归套件在 `tests/`，跑法 `cd tests && ./runall.sh e2e.py e2e_ai.py`，细节见 `tests/RUN.md`。
> 这三份文件 + tests/ 就是全部交接内容，**不需要上一轮的聊天记录**。

## 最后更新时间
2026-07-31 14:29（北京时间）

## 你可能是「换账号后第一个接手的 AI」——先做这三件事

用户 2026-07-31 说过：**老账号那边的窗口是最后一个了**，之后靠这个仓库续。所以：

1. **别问「上一轮聊了什么」**，聊天记录过不来，也不需要。读 `AGENTS.md` → 这份 → `CLAUDE.md` 就够。
2. **先跑一次回归确认环境是好的**：`pip install aiohttp`，然后
   `cd tests && ./runall.sh e2e.py e2e_imp.py`（应为 46 / 59 全绿）。已在干净 clone 上实测过，
   不绿就是环境问题，不是代码问题。
3. **然后跟用户要一份诊断包**（界面「运行日志」页 → 导出诊断）。下面「未完成」第一项就卡在
   「不知道他机器上现在是什么状况」——诊断包的 `[0] 一眼结论` 和 `[4.5] 拿真实消息试算`
   两段能直接给出答案，别靠追问。要推代码时跟他现要一个 GitHub PAT（凭据不在仓库里）。

## 当前状态
dcwatch **v1.9.0**。**这一版没有改扩展**，扩展留在 v1.8.0，`EXT_MIN` 也留在 1.8.0 ——
用户**不需要重装扩展**，只换程序文件夹即可（硬规矩 2：扩展没动就别让他白重装一遍）。

本轮做的是上一轮 HANDOFF 里「未完成」的**第一项可执行任务**：**规则的导入 / 导出**。
（列表里更前面的两项是「跟用户要诊断包」「等用户装上 v1.8.0 并回执」，都只能等他，
不是能动手的活；`CLAUDE.md` 第 3 条要求挑「第一项当前可以直接执行的」，所以从这项开始。）

回归**服务端 401 条全绿**（e2e 46 + ai 27 + multi 26 + wiz 83 + v17 46 + diag 47 + wb 67 +
**imp 59（本轮新增）**）。`extension/content.js` 一行没动，所以浏览器侧那 43 + 16 条没重跑。
交付 `outputs/dcwatch-v1.9.0.zip`（26 文件 / 352KB，已解压起服务实测过 export + import 都通）。
**没有出扩展 zip**（扩展没改）。

## 已完成
- 单进程 aiohttp 服务端（server.py）：Discord 监听 / 规则引擎 / OpenAI 兼容模型调用 / SQLite 存储
- 单文件 GUI（ui.html，Claude 风格）：收信箱、AI 工作台、批量提取、监听规则、模型接入、通知与转发、设置、运行日志
- MV3 浏览器扩展（extension/）：读 discord.com DOM 上报消息，页面内状态药丸 + popup 自检 + 抓历史
- 三条接入方式：浏览器旁听（默认，不需要 token）/ Bot Token / User Token
- 引导式建规则（/api/rules/wizard）与一句话生成规则（/api/rules/compose）
- 通用出口 hooks：Windows toast、提示音、Discord Webhook、Telegram、Server酱、企业微信、自定义 webhook
- 新帖检测（论坛开帖）、抓历史、批量提取 + CSV 导出
- 自证渠道：/version 独立页面、诊断包 /diagnose.txt（[0] 一眼结论、[4.5] 拿真消息逐条试算）、启动时打印版本与目录
- Windows 双击即用：启动.bat / 停止.bat / build.bat + freeport.py 自动清理占用端口
- v1.7.4：后台标签页不再迟到（第一条消息立刻发 + background 每 30s 主动拉心跳，不需要新权限）
- v1.8.0：工作台的模型能自己动手改规则（`WB_TOOLS` + 函数调用，接口不支持 tools 时退回
  `WB_TEXT_PROTO` 文本指令协议）；「模型接入」页两个开关（允许模型直接改规则 / 流式）；
  表情包图片贴纸翻译成 `[图片]` `[贴纸 xx]` `:emoji:` 上报；旁听模式不再画回复输入框；`e2e_wb.py` 67 条
- 跨会话/跨账号交接：`tests/` 整套进了仓库，根目录 `AGENTS.md` 作为项目记忆（会被
  Claude Code / BrowserCode / Cursor 自动读取），`runall.sh` / `content_test.mjs` 不写死绝对路径
- **v1.9.0（本轮）规则导入 / 导出** —— 用户填错了只能一格格改、给不了别人一份「拿去直接用」的
  规则，这件事到此解决：
  · `GET /api/rules/export`：整包导出成 json 附件，带 `schema: dcwatch.rules/1` + 程序版本号 +
    导出时间 + 条数，文件名 `dcwatch-规则N条-v1.9.0-MMDD-HHMM.json`（`Cache-Control: no-store`）。
    **不导出 id 和 hits**（本机的账），也不含任何 Token / API Key，可以直接发给别人
  · `POST /api/rules/import`：**`dry_run` 默认为真 —— 默认只算不写**。返回逐条计划
    （`new` / `overwrite` / `same`）、覆盖项的**逐字段人话 diff**（「含任一关键词：空投 → 空投、白名单」，
    字段名走 `RULE_LABELS` 翻译）、每条的警告（非法动作、不像 ID 的值、未知字段、要用模型但没配模型）、
    `replace` 模式下会被删掉的规则清单。界面拿这份预览问「确定吗」，点确认才第二次请求真写
  · 两种合法：`merge`（同名覆盖 + 其余新增，本机多出来的留着）/ `replace`（本机变成跟文件完全一样）。
    **重名认 name 不认 id**（两台机器 id 必然不同，名字才是用户看得懂的东西）；
    覆盖走 UPDATE，所以**本机命中数不清零**，重复导入同一个文件也不会造出重复规则
  · `sanitize_import_rule()`：类型强转（字符串数字 → int）、非法 action 退回 notify、非法 kinds 洗掉、
    未知字段丢弃，每一步都写进 notes 给用户看。**故意不校验 ID 存不存在**（见下面注意事项第一条）
  · 界面「监听规则」页顶部两个按钮 + 导入预览抽屉（新增/覆盖/没变/会删各自的样式，
    删除前再 confirm 一次），落库后刷新列表并 toast 真实条数
  · 新回归套件 `tests/e2e_imp.py`（59 条，13 节）；README 第三节新增「规则可以整包导出 / 导入」
    + 故障对照两行；AGENTS.md 新增硬规矩 10（会覆盖用户手填内容的操作必须先预览）
  · 界面这条路**在真浏览器里点过一遍**（CDP 里喂后端真返回的预览 JSON）：抽屉渲染、
    合并/替换切换会重新预览、删除前 confirm、确认后 `dry_run:false` 落库、列表刷新、toast 计数，都对

## 未完成（按优先级排序）
- [ ] **先要一份诊断包**（见最上面第 3 条）。到 2026-07-31 为止仍不知道用户机器上跑的是哪一版、
      规则改对没有 —— 这是唯一真正卡住的事，其余都是可选改进。他不会命令行，别让他敲命令
- [ ] 等用户装上 **v1.9.0**（**扩展不用动，还是 v1.8.0**）并确认两件事：
      ①「我为什么收不到消息」直接在工作台问，它会自己查状态并给结论；②让它改一条规则，看动作行有没有 `✔`。
      不通就要一份新诊断包，先看 [0] 和 [4.5] 两段
- [ ] 诊断包里还没有印「规则是不是导入来的 / 上次导入是什么时候」。用户拿一份别人的规则包导进去以后
      排查会缺这条线索，`/api/rules/import` 已经在运行日志里写了一行，但 `/diagnose.txt` 的
      `[4]` 段没有。要做就在导入时给规则记个 `imported_at` 并在 [4] 段印出来（硬规矩 5）
- [ ] 让工作台的模型也能用导入/导出（`WB_TOOLS` 现在只有列/建/改/启停/删/试算）。
      值得加的是 `export_rules`（让它能把现有规则整包念给用户看）；`import_rules` 要谨慎，
      模型自己吃一个文件会绕过「先预览再确认」那道闸，硬规矩 10 不允许
- [ ] exe 打包只能在用户本机做（PyInstaller 无法在 Linux 交叉编译），dist\dcwatch.exe 从未在真实 Windows 上验证过
- [ ] 扩展始终没能在真实浏览器里 load unpacked 跑过端到端（云环境上传桥不可用），靠假 Discord DOM 的回归兜底
- [ ] `pullTabs()` 会向所有标签页广播 `dcwatch-pull`（无权限方案的代价）。标签页极多时是几十次
      必然失败的 sendMessage，可接受但不优雅；若将来已经要加权限了，可顺手改回按 url 筛

## 注意事项 / 踩过的坑
- **导入规则时绝不能照 `sanitize_draft` 那套 `known_ids` 过滤 ID**。那套是给「模型编 ID」用的：
  编出来的频道 ID 必须丢掉。但导入方的消息库里**本来就查不到**导出方的频道 ID（一条消息都还没收到），
  照那套洗会把规则洗成空条件，用户看到的是「导进来了但什么都不听」—— 比报错难查得多。
  所以 `sanitize_import_rule()` 只要求 ID 是纯数字。`e2e_imp.py` 第 7 节就是钉这件事的，别为了「统一」改回去
- **`/api/rules/import` 的 `dry_run` 默认必须是真**。少写一个 `dry_run` 的调用方不应该把用户的规则改掉
- 测试里断言 400 的原因要先把 body 解出来（`emsg()`）：`_body` 是转义过的 JSON，
  直接在原始串里搜中文永远搜不到（跟 echo.jsonl 那个坑同源）
- `e2e_imp.py` 第 8 节靠 `/api/ingest` 造命中：**ingest 后要 sleep 0.8s** 再读 hits，
  且样本正文要比规则的 `min_len` 长，否则被 `len` 挡下，看着像导入功能坏了
- **跑回归前先确认 8777 没被别的 server 占着**。占着的话 `启动.bat`/server 会礼貌地不启动第二个，
  于是你以为在测新代码，其实测的是上一个进程里的旧代码（本轮冒烟测试踩过一次，
  日志第一行会写「dcwatch 已经在跑了（端口 8777 被占）」）。用 `python3 kill.py server.py`
- **本 workspace 会被整体同步覆盖**：`.bat`、`.gitignore`、`.gitattributes` 会凭空消失。
  改包前 `find /tmp/pack/dcwatch -type f | wc -l`（交付包必须是 **26**），
  少了就从 `git clone` 的远端副本里 cp 回来，并复验 `.bat` 是 **GBK + CRLF**
- **打包前先 `rm -rf __pycache__`**（含 `tests/__pycache__`）：`py_compile` 留下的 .pyc 混进过交付包
- **并行 run 会改同一份文件**：动手前先 `diff` 工作树和远端（`git clone` 到 /tmp 再 `diff -rq`），
  别假设工作树是自己上次留下的样子。本轮就是直接在一份干净 clone 上做的，推荐照做
- **改了扩展就必须 bump manifest version 和 EXT_MIN**，并在回复里告诉用户该看到几点几。
  反过来，扩展没改就别动这两个（v1.9.0 就是这种情况：只动了 server.py / ui.html）
- **测试里不要写死版本号**（e2e_diag 曾把桥的扩展版本写死，EXT_MIN 一 bump 就假红三条）
- 加了 chrome.runtime 的新消息类型，要同时在 content_test.mjs 的 chrome 桩里补 `onMessage`，
  否则 content.js 的 try/catch 会静默吞掉，测试看着全绿其实那段代码根本没跑
- **「有人发消息」和「有人开新帖」是规则里两个独立勾选（kinds）**，只勾后者时频道里聊翻天也不会响
- 排查「填好了为什么不响」不能只看规则条件本身，要拿真消息跑 match()。诊断包 [4.5] 段就是干这个的
- messages 表**没有 mentions_me 列**，所以 [4.5] 里勾了「只在@我」的规则一定算不中，已单独注一句
- Windows 的 .bat 必须 GBK + CRLF；echo 文本里的 `>` 要转义成 `^>`
- 交付 zip 一律带版本号，且旧版 zip 删掉，避免他拿错
- 回归一套一套跑（`tests/runall.sh`，每套之间清 /tmp/p.db* 并重启 server），一次喂两三套，
  bash 工具 120s 会超时。**`e2e_wiz.py` 单套就要 40 秒左右，别再跟别的套挤一批**
- 所有出网调用必须走 `App.rq()`（trust_env + net.proxy），否则国内直连 api.openai.com 必超时
- 界面绝不能拿假数据冒充成功；`S.live` 一旦为真永不回退。导出/导入按钮在没连上后端时
  会明说「后端没连上」，不会假装成功
- 微信只走 Server酱 / 企业微信官方通道；火狐已彻底放弃，只支持 Chrome / Edge
- **推送凭据不在仓库里，也不该在**。换账号接手时直接跟用户要一个新的 GitHub PAT，用一次性 URL 推：
  `git push "https://x-access-token:<PAT>@github.com/tqzbceb/claude.git" HEAD:main`，
  输出过一遍 `sed 's/github_pat_[A-Za-z0-9_]*/***TOKEN***/g'`，推完 `grep -rl github_pat_ .git/` 确认无残留。
  用户 2026-07-31 明确要求「每次任务做完都推」。推前先 `git ls-remote` 看远端到哪了，别信记忆里的 sha
- **交付给用户的 zip 里不含 `tests/`、`AGENTS.md`、`CLAUDE.md`、`HANDOFF.md`**（26 个文件，
  只有他要跑的东西）。所以让接手的 AI **从 GitHub clone**，不要拿用户手上的 zip —— zip 里没有交接内容
