# HANDOFF

> **换了账号 / 新窗口第一次来？先读 `START_HERE.md`**（5 分钟，头四件事写死在那儿），
> 再读 `AGENTS.md`（项目怎么运作、用户是谁、十一条硬规矩、排查「它没提醒我」的固定顺序），
> 然后读这份看进度，`CLAUDE.md` 是工作协议。
> 回归套件在 `tests/`，跑法 `cd tests && ./runall.sh e2e.py e2e_imp.py`，细节见 `tests/RUN.md`。
> 这四份文件 + `tests/` + `release/` 就是全部交接内容，**不需要上一轮的聊天记录**。

## 最后更新时间
2026-07-31 23:45（北京时间）

## 新账号 / 新窗口先看这三行
- 用户的账号是「10x10」：**每个窗口 10 条消息，用完换窗口**。进行中的状态在 `NOW.md`（粒度到步），
  接手先读它，别问用户上次做到哪。每做完一步就用 `.bcode/agent-workspace/save.py` 存盘推送。
- 接手后照 `START_HERE.md` 第 5.5 节把项目指针写进**新工作区根目录的 `AGENTS.md`**，
  别往后拖 —— 换窗口丢的从来不是代码，是「该去哪读」这个指针。
- 工作区里的 `claude/` 是**死快照**（没有 `.git`）。干活永远 `git clone` 到 `/tmp` 上改，改完推。

## 当前状态
dcwatch 代码侧最新是 **v1.9.3 + A2/A3/A5 修复（未发版）**。

**A3「重复通知」+ A5「关了开关还弹」已修**（施工图 `PLAN_A3A5.md`）。根因是同一个：
ui.html 的「网页通知」`new Notification` 在 Windows 上也进系统通知中心，用户分不出它和原生 toast——
A3：每个开着的 dcwatch 标签页各持一条 SSE 各弹一条（无 tag 不合并）+ server toast 一条 → 3 条；
A5：`S.cfg` 只在 boot 取一次，`/api/config` 存 sinks 后服务端不广播 → 旧标签页拿着 `browser:true` 永远弹。
修法三处：① server.py setcfg 存 sinks 后 `bus.push("sinks", …)` 广播（前端处理器本来就有）；
② ui.html Notification 加 `tag:'dcw-'+msg_id`，同源同 tag Chrome 只显示一条；
③ 帮助文案写明「网页通知与系统通知是两条通道」。服务端 msg_id 去重、合并队列查过是好的，没动。
回归：服务端 **500 条全绿**（e2e 46→48，新增 SSE 广播 2 条），content_test 46 + 16 重跑仍全绿。
这轮没动 extension/。真机多标签页场景（两个页面各收一条变成一条）发版后值得让用户验一次。

**A2「检测某个用户」失效已修**：
四处改动（施工图 `PLAN_A2.md`）——
① test_rule 工具加 `author_id` 参数，留空默认取规则 author_ids[0]（模型试算盯人规则不再必挂）；
② content.js 取头像跳过回复预览容器（`[class*="repliedMessage"]`），回复消息 author_id 不再记成被回复人；
③ content.js 正文按精确 id `#message-content-<msg_id>` 取，不吃回复预览里的原文；
④ gateway 作者名优先 `member.nick`（服务器昵称）再 global_name。
回归：服务端 498 条全绿（wb 94→96），content_test 46 + 16 条全绿（新增回复消息 3 条）。
**没发 zip、没升版本**：A2 动了 `extension/content.js`，下轮发版要记得 `EXT_MIN` 跟着提、
用户需要重装扩展（硬规矩 2）。扩展在真 Discord 的回复消息上还没实测过，发版前值得让用户验一条。

上一轮（v1.9.3，已发版）：**扩展没改**，留在 v1.8.0，`EXT_MIN` 也留在 1.8.0 ——
用户**不需要重装扩展**，只换程序文件夹即可（硬规矩 2）。

本轮做的是上一轮「未完成」里第一件能自己动手的事：**批量提取的模板 + 整包导出 / 导入**
（「要诊断包」「等装新版回执」仍只能等用户）。照规则包那一套：schema `dcwatch.extract/1`、
`dry_run` 默认真的预览闸、认 name 不认 id、导入戳不外泄。

回归**服务端 496 条全绿**（e2e 46 + ai 27 + multi 26 + wiz 83 + v17 46 + diag 47 + wb 94 +
imp 74 + **ext 53，本轮新增**）。`extension/` 一行没动，浏览器侧那 43 + 16 条没重跑。
交付 `outputs/dcwatch-v1.9.3.zip`，同一份也换进了 `release/`（旧的 1.9.2 删了）。
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
- v1.7.4：后台标签页不再迟到（第一条消息立刻发 + background 每 30s 主动拉心跳）
- v1.8.0：工作台的模型能自己动手改规则（`WB_TOOLS` + 函数调用，接口不支持 tools 时退回
  `WB_TEXT_PROTO` 文本指令协议）；「模型接入」页两个开关（允许模型直接改规则 / 流式）；
  表情包图片贴纸翻译成 `[图片]` `[贴纸 xx]` `:emoji:` 上报；旁听模式不再画回复输入框
- v1.9.0：规则整包**导出 / 导入**（`/api/rules/export`、`/api/rules/import`）。`dry_run` 默认为真
  ——默认只算不写，界面拿预览（新增 / 覆盖 + 逐字段人话 diff / 没变 / replace 会删掉谁）问过用户
  才第二次请求真写。重名认 name 不认 id；覆盖走 UPDATE 所以本机命中数不清零
- v1.9.1：导入来的规则**留痕**（`imported_at` / `imported_from`，不在 `DEFAULT_RULE` 里所以不会被
  导出去）。界面列表一个「导入」小标签，诊断包 `[4]` 段印「来路 … 从规则包导入」，
  界面编辑时会把戳从库里捞回来续上
- 跨会话 / 跨账号交接：`START_HERE.md` 上手页、`AGENTS.md` 项目记忆、`tests/` 整套、
  `release/` 里一份现成成品 zip（用户不靠 AI 也能从 GitHub 网页拿到能跑的程序）
- v1.9.2：工作台能把规则整包导出念给用户 ——
  · `WB_TOOLS` 新增 `export_rules`（可选 `ids` 只导几条），走 `wb_export_pack()`。
    包的口径跟界面「⇡ 导出规则」按钮一致（`rule_for_export()`，不带 id / hits / 导入戳），
    另加三条差别：**转发地址打码**成 `WB_MASK`、超长 `prompt` 截断、整包超过
    `WB_PACK_LIMIT`（3500 字）时退化成「只有名字和动作」的清单
  · 打码的理由写在代码注释里：webhook_url 是个能往外发东西的**密址**，不该跟着对话上传到
    模型厂商那边。完整可导入的文件永远只有一条路 —— 用户自己点界面上的按钮
  · 返回体里塞了 `notes`（打码了几条 / 哪个 id 不存在 / 截断了）、`how_to_get_the_file`
    （把用户指到「导出规则」按钮）、`how_to_import`（导入必须由人点）
  · `WB_TOOLS_HOWTO` 加纪律第 7 条、`WB_TEXT_PROTO` 加一行工具说明（两条路都要写，
    不支持函数调用的模型走的是后者）、`READ_HUMAN` 加一句人话
  · **刻意没有 `import_rules` 工具**，代码里就写着为什么（硬规矩 10）
  · `tests/e2e_wb.py` 新增第 14 节（27 条，67 → 94）；README 第三节 + 工作台一节 +
    故障对照表各补一条；`tests/RUN.md` 补 e2e_wb 覆盖清单和两个坑
- **v1.9.3（本轮）批量提取的模板 + 整包导出 / 导入** ——
  · 模板存在 **cfg 的 `extract_templates`** 里（照 `quick_actions` 的样子，不新建表）：
    `DEFAULT_TPL`（name / want / channel_id / limit / only_matched / note）+ `norm_tpls()` 洗，
    `/api/config` 认它；limit 夹 1–2000，不像 ID 的频道清空
  · `/api/extract/export`：`dcwatch.extract/1` 包（`EXTRACT_SCHEMA`），`tpl_for_export()`
    照 `DEFAULT_TPL` 的键导出 —— 不带 id、不带导入戳
  · `/api/extract/import`：**跟规则包同一道预览闸**（`dry_run` 默认真、merge / replace、
    认 name 不认 id、`diff_tpl()` 出人话 diff、replace 先列 removes）。频道 ID 只要求纯数字，
    **不按本机 known_ids 洗**（导出方的 ID 本机必然查不到，洗了模板就空了）
  · 导入戳 `imported_at` / `imported_from`（`TPL_MARKS`）**不进 `DEFAULT_TPL`** 所以不外泄
  · 界面（ui.html 批量提取页）：模板 chips 一点就填四个框 / 「存为模板」/ ⇡ 导出 ⇣ 导入 +
    预览抽屉（已在浏览器目检）；诊断包新增 `[5.5]` 段印模板清单
  · `tests/e2e_ext.py` 新套 53 条；README 批量提取一节 + 故障对照表各补一条；
    `tests/RUN.md` 补 e2e_ext 覆盖清单和三条铁律

## 未完成（按优先级排序）
- [ ] **先要一份诊断包**。到 2026-07-31 为止仍不知道用户机器上跑的是哪一版、规则改对没有
      —— 这是唯一真正卡住的事。要法：界面「运行日志」页 → 导出诊断 → 把 txt 原样发来。
      他不会命令行，别让他敲命令
- [ ] 等用户装上 **v1.9.3**（**扩展不用动，还是 v1.8.0**）并确认四件事：
      ①「我为什么收不到消息」直接在工作台问，它会自己查状态给结论；②让它改一条规则，
      看动作行有没有 `✔`；③跟它说「把我的规则整包给我一份」，看它是不是真念出来了；
      ④批量提取页填好一次点「存为模板」，下次点 chip 是不是四个框自动填好。
      不通就要一份新诊断包，先看 [0] 和 [4.5] 两段（模板看 [5.5] 段）
- [ ] **下一件能直接动手的：读根目录 `BACKLOG.md`**——用户 2026-07-31 一次性反馈了
      5 个 bug + 一批 AI 能力需求（工作台聊天丢失/多开、按用户检测失效、重复通知、
      转发出口锁死、关不掉的系统通知、规则接 AI 复核、提示词可编辑、模型参数等），
      已排好优先级。旧的可选方向（export_extract_templates 等）并进了 BACKLOG B6
- [ ] exe 打包只能在用户本机做（PyInstaller 无法在 Linux 交叉编译），dist\dcwatch.exe 从未在真实 Windows 上验证过
- [ ] 扩展始终没能在真实浏览器里 load unpacked 跑过端到端（云环境上传桥不可用），靠假 Discord DOM 的回归兜底
- [ ] `pullTabs()` 会向所有标签页广播 `dcwatch-pull`（无权限方案的代价）。标签页极多时是几十次
      必然失败的 sendMessage，可接受但不优雅；若将来已经要加权限了，可顺手改回按 url 筛

## 注意事项 / 踩过的坑
- **别给工作台加 `import_rules` 工具**（硬规矩 10）。导入会覆盖、甚至删掉用户手填了一晚上的规则，
  那道「先预览再落库」的闸在界面上（`dry_run` 预览 → 他点确认）。模型自己吃一个文件就绕过闸了。
  server.py 里 `WB_TOOLS` 结尾就写着这段，`e2e_wb.py` 第 14 节钉了「模型硬调 import_rules 必须失败」
- **`export_rules` 给模型的包里 webhook_url 必须打码**。它是密址，不该随对话外发；
  但按钮导出的文件里是完整的（不然导过去就废了）。`e2e_wb.py` 第 14 节断言 `SECRET123` 不出现
- **`WB_PACK_LIMIT` 是 3500，改它要一起看两个上限**：服务端把工具结果截到 6000 字，
  mockllm 的 `tool_out` 只留 4000 字。包比 4000 大，测试里就解不出 JSON（看着像功能坏了）
- **`e2e_wb.py` 第 6 节之后 `mock-1` 被记成「不支持函数调用」**（`App.no_tools` 是进程内内存，
  没接口能清）。之后要断言「工具结果真喂回给模型了」的小节必须换个模型名
  （第 14 节换 `mock-2`），不换的话工具结果是当 role=user 喂回去的，`tool_out` 永远是空的
- **`imported_at` / `imported_from` 别为了「统一」塞进 `DEFAULT_RULE`**。`rule_for_export()` 是照
  `DEFAULT_RULE` 的键导出的，塞进去就等于把本机的导入记录发给别人。`e2e_imp.py` 第 13 节钉了这件事
- **导入规则时绝不能照 `sanitize_draft` 那套 `known_ids` 过滤 ID**。那套是给「模型编 ID」用的。
  导入方的消息库里**本来就查不到**导出方的频道 ID，照那套洗会把规则洗成空条件，
  用户看到「导进来了但什么都不听」。`sanitize_import_rule()` 只要求 ID 是纯数字。`e2e_imp.py` 第 7 节钉着
- **`/api/rules/import` 的 `dry_run` 默认必须是真**
- 测试里断言 400 的原因要先把 body 解出来（`emsg()`）：`_body` 是转义过的 JSON，
  直接在原始串里搜中文永远搜不到
- `e2e_imp.py` 第 8 节靠 `/api/ingest` 造命中：**ingest 后要 sleep 0.8s** 再读 hits，
  且样本正文要比规则的 `min_len` 长
- **跑回归前先确认 8777 没被别的 server 占着**（`python3 tests/kill.py server.py`）。
  占着的话你以为在测新代码，其实测的是上一个进程里的旧代码
- **本 workspace 会被整体同步覆盖**，且**同步过来的目录里没有 `.git`**：`.bat`、`.gitignore`、
  `.gitattributes` 会凭空消失。所以**别在同步过来的那份里改代码** —— `git clone` 一份到 `/tmp` 上干活
  （本轮就是这么做的），做完推上去。改包前 `find /tmp/pack/dcwatch -type f | wc -l` 必须是 **26**，
  并复验 `.bat` 是 **GBK + CRLF**
- **打包前先 `rm -rf __pycache__`**（含 `tests/__pycache__`）
- **并行 run 会改同一份文件**：动手前先 `diff` 工作树和远端（`git clone` 到 /tmp 再 `diff -rq`）
- **改了扩展才 bump manifest version 和 EXT_MIN**，并在回复里告诉用户该看到几点几。
  没改就别动（v1.9.0 / v1.9.1 / v1.9.2 都是这种情况：只动 server.py / ui.html / tests / 文档）
- **测试里不要写死版本号**（e2e_diag 曾把桥的扩展版本写死，EXT_MIN 一 bump 就假红三条）
- 加了 chrome.runtime 的新消息类型，要同时在 content_test.mjs 的 chrome 桩里补 `onMessage`
- **「有人发消息」和「有人开新帖」是规则里两个独立勾选（kinds）**，只勾后者时频道里聊翻天也不会响
- 排查「填好了为什么不响」不能只看规则条件本身，要拿真消息跑 match()。诊断包 [4.5] 段就是干这个的
- messages 表**没有 mentions_me 列**，所以 [4.5] 里勾了「只在@我」的规则一定算不中，已单独注一句
- Windows 的 .bat 必须 GBK + CRLF；echo 文本里的 `>` 要转义成 `^>`
- 交付 zip 一律带版本号，旧版 zip 删掉（`outputs/` 和 `release/` 都只留最新一个）
- **bash 工具超时会把 nohup 起的 server 一起杀掉**（同一个进程组）。手动起服务做冒烟测试要用
  `DCWATCH_DB=/tmp/smoke.db setsid nohup python3 server.py >/tmp/smoke.log 2>&1 < /dev/null &`
- 回归一套一套跑（`tests/runall.sh`，每套之间清 /tmp/p.db* 并重启 server），一次喂两三套，
  bash 工具 120s 会超时。**`e2e_wiz.py` 单套就要 40 秒左右，别再跟别的套挤一批**
- 所有出网调用必须走 `App.rq()`（trust_env + net.proxy），否则国内直连 api.openai.com 必超时
- 界面绝不能拿假数据冒充成功；`S.live` 一旦为真永不回退
- 微信只走 Server酱 / 企业微信官方通道；火狐已彻底放弃，只支持 Chrome / Edge
- **推送凭据不在仓库里，也不该在**。2026-07-31 这一轮用的是**这个 workspace 的私有目录**里那份
  （`.bcode/agent-workspace/secrets/github_pat_tqzbceb.txt`，在工作区里但不在仓库里）。
  换账号 / 换工作区就拿不到了 —— 那时候直接跟用户要一个新 PAT。用一次性 URL 推：
  `git push "https://x-access-token:<PAT>@github.com/tqzbceb/claude.git" HEAD:main`，
  输出过一遍 `sed 's/github_pat_[A-Za-z0-9_]*/***TOKEN***/g'`，推完 `grep -rl github_pat_ .git/` 确认无残留。
  用户明确要求「**任务做完直接提交并推送，别停下来问**」。推前先 `git ls-remote` 看远端到哪了
- **换号真正会丢的不是代码，是「该去哪读」这个指针**。代码在远端，交接在仓库里，但新工作区
  根目录的 `AGENTS.md` 是空的 —— 用户复制一段提示词能救**第一个**会话，救不了第二个。
  所以接手后第一件事：照 `START_HERE.md` 第 5.5 节把指针追加进 `./AGENTS.md`（工作区根那份，
  每个新会话都会被自动读取），别让用户每开一个会话都当一次搬运工。
- **交付给用户的 zip 里不含 `tests/`、`release/`、`AGENTS.md`、`CLAUDE.md`、`HANDOFF.md`、
  `START_HERE.md`**（26 个文件，`README.md` 要留）。所以让接手的 AI **从 GitHub clone**，
  不要拿用户手上的 zip —— zip 里没有交接内容

## review 盖章（2026-07-31，Claude 窗口）
A2 + A3+A5 的 diff 对照 PLAN 逐行过了一遍：与施工图一致、无越界改动、
「不许动」清单全部未动。回归亲测复跑：服务端 500/500 + content_test 46/16 全绿。
结论：**通过，无返工项**。下一件 A4（先出 PLAN_A4.md）。
