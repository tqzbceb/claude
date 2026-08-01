# HANDOFF

> **换了账号 / 新窗口第一次来？先读 `START_HERE.md`**（5 分钟，头四件事写死在那儿），
> 再读 `AGENTS.md`（项目怎么运作、用户是谁、十一条硬规矩、排查「它没提醒我」的固定顺序），
> 然后读这份看进度，`CLAUDE.md` 是工作协议。
> 回归套件在 `tests/`，跑法 `cd tests && ./runall.sh e2e.py e2e_imp.py`，细节见 `tests/RUN.md`。
> 这四份文件 + `tests/` + `release/` 就是全部交接内容，**不需要上一轮的聊天记录**。


## v1.12.1（AN/AO 窗口，2026-08-02）：修 F1 的服务器名录 —— 打服务器名搜不到候选

**怎么发现的**：AN 窗口在给 `content_test.mjs` 补第四套 `runNames()`（32 条，把「扩展从侧栏
抄名字→ID」变成常驻回归）时，测试直接红了 —— 不是测试写错，是 v1.12.0 已发布的真 bug。

**根因**：Discord 左边那列服务器图标和频道列表用的是同一种 `<a href>`，`harvestGuilds()`
的选择器把频道链接也收了进来，于是**频道名被写到服务器 ID 那条记录上**。表现：规则页
「服务器」栏打服务器名一个候选都不弹（频道/子区/用户三栏各有自己的收割器，不受影响）。

**修法**：`extension/content.js` 里把两者分开认（`0f0e6ac`/`7013b48`）；
`__dcwatch` 暴露 `harvestNames`/`harvestGuilds` 方便测试直接调；`tests/RUN.md` 记下这个坑
和「动了收割器就跑 runNames()」。

**出包（AO 窗口）**：三处钉齐 v1.12.1；服务端 14 件 **870 条全绿**（顺手把文档里的旧计数
按实测改正：e2e.py 60 不是 48、e2e_diag 50 不是 47，总数 858→870）；
扩展侧 CDP **4 套 139 条全绿**（run 49 / runFresh 16 / runTabs 42 / runNames 32）；
双 zip 在 `release/`（程序 27 文件、扩展 11 条目，逐条跟 1.12.0 清单对齐，旧包已删）+ `outputs/`；
全新解压冒烟：启动首行 `dcwatch v1.12.1`、`/version`、`/api/presets`、`/api/names`、`/api/lookup`
全过。**这一版扩展真改了，用户必须重装扩展。**

**教训（新的）**：AN 之后有一个窗口把 1.12.1 的 bump + 回归做完了但**没推送就被额度掐断**，
AO 窗口只能重做一遍。硬规矩「每做完一步立刻 commit+push」不是客套话 —— 这已经是第二次了。

## v1.12.0（AK/AL/AM 窗口，2026-08-02）：F1 名字→ID 名录（建规则不用再手抄 ID）

**用户原话**：「我输入用户名字或者频道名字他没法监听啊，必须要我给 id，难道不能他自己根据名字
去找一下，再找我确认是不是？」

**根因**：规则的 channel_id / user_id / guild_id 四个字段一直只吃数字 ID，程序自己手里
**没有任何名字→ID 的映射**，所以只能把「去 Discord 开发者模式右键复制 ID」这活儿推给用户。
用户不写代码，这一步就是他建规则的最大门槛。而且撞上收信闸的鸡生蛋：没规则不收信 →
不收信就没有消息 → 第一条规则的 ID 没处抄。

**修法（五步，每步一次 commit）**：
1. **服务端名录**（`5909a9c`）：`names` 表（kind/id/name/guild/parent/src/seen_at）+ `learn()`
   挂在 **ingest 路径最前面**（收信闸拦下也照学，破鸡生蛋）+ `/api/lookup` 模糊查（候选带
   面包屑 `测试服 › 信息区` 和「多久前见过」）+ `/api/names`、`/api/ext/names` 上报入口。
2. **扩展抄名录**（`e0dd24b`）：`content.js` 从侧栏抓服务器/分类/频道/子区（链接里带
   `/channels/<guild>/<channel>`，17–19 位 snowflake 校验、aria-label 清洗、分类归属），
   消息头抓作者名；`background.js` 加上报路由。**不额外请求 Discord，只抄屏幕上已有的东西**。
3. **工作台 `find_target` 工具**（`bf879a1`）：模型能自己查名录、把候选列给用户确认；
   `READ_HUMAN`/`WB_TOOLS_HOWTO`/功能清单同步（硬规矩 6）。
4. **规则页四栏可搜索下拉**（`44d7c53`）：频道/用户/服务器/子区都能直接打名字弹候选，
   chip 上同时显示名字和 ID。**名字不唯一时永远让用户挑，绝不替他猜。**
5. **测试**（`39dcaf8`、`578d764`）：新增 `tests/e2e_name.py` 82 条（学习/排序/限定 kind/
   面包屑/改名更新/闸拦下照学/find_target 三情形）+ e2e_wb 补 find_target 断言；
   顺手修真 bug：`find_target` 里误写 `a.get` 每次调用必崩（NameError）；
   `mockllm` 只记 system 前 6000 字导致 e2e_wiz 8 条假红 → 上限提到 40000。

**两个限制已写进界面和 README**：① 名字不唯一 → 必须确认；② 只认**浏览器见过的**，
没在侧栏出现过的查不到，这时仍然贴链接或 ID。

**出包（AM 窗口）**：服务端 14 件 **858 条全绿**（48+27+26+83+46+50+192+74+53+42+53+56+26+82），
扩展侧 CDP 回归 107 条全绿（run 49 / runFresh 16 / runTabs 42）；三处钉齐 v1.12.0；
双 zip 在 `release/`（程序 27 文件、扩展 11 条目，逐条跟 1.11.7 清单对齐，旧包已删）+ `outputs/`；
全新解压冒烟：启动首行 `dcwatch v1.12.0`、`/version` 自证页、`/api/presets`、
`/api/ext/names` 学习、`/api/lookup` 面包屑全过。**这一版扩展真改了，用户必须重装扩展。**

## v1.11.7（AG/AH/AI/AJ 窗口，2026-08-02）：E7 输入框 + E8 扩展误报 + E9 工作台的手补写工具

**用户原话**：「这个光标问题还是会出现啊，我发一条消息后，他输入框灰底字消失，然后变大，然后又
出现两处可以点出光标了，而且我需要的不是你把这个拉长输入框删了，我需要你修复这个问题」
「扩展是旧版 v1.11.4（程序要求 v1.11.6）——但是我明明扩展已经更新到同版本了，是不是检测出问题了，
记得修好，而不是把这个删了」「模型：这个我没法直接帮你加……就很弱智，这个模型权限太低了。
我想要什么都跟模型说，他都能帮我配置好，而不是要我手动去操作」。

**E7 根因**：v1.11.5 那轮为了消掉「两处光标」直接把 `resize` 禁了 —— 治了症状没治病，
而且**把用户想要的手动拉高也一起删了**（用户明确反对）。真正的病是发送后 textarea 的
值/高度没归零重算：灰底 placeholder 消失、盒子留在上一次的高度，于是"文字区 + 空白区"
又变成两处可点。**修法**：发送后值与高度一起归零重算；输入法 Enter 不再漏换行；
`resize:vertical` 还给用户。

**E8 根因**：判「扩展旧版」时拿的是**桥上报的运行时版本**，而旧 Discord 标签页里跑的是
装扩展前注入的老 `content.js` —— 用户明明已经装了新扩展，只要有一个没刷新的标签页就被点名
「扩展是旧版」，还被误导去重装。**修法**：跟**磁盘上的实际扩展版本**比；真有旧脚本标签页时
说清「扩展本身没问题，是哪个页面在跑旧脚本，按 F5 就行」。

**E9 根因**：`WB_TOOLS` 里的写工具只有规则那四个，开服监听/自动开帖/通知开关/默认模型
全是只读或压根没有 —— 模型不是笨，是**手被绑着**，只能念步骤。**修法**：补写工具
（开服监听 CRUD + 立刻现探一次、本机提醒开关/免打扰/分数门槛、自动开帖开关与上限、
从已拉到的模型里选默认模型），同步 `WB_WRITE_TOOLS` 确认闸 / `WB_TOOLS_HOWTO` /
`WORKBENCH_SYS` 功能清单。**红线不动**：API Key、转发出口密址、导入规则永不给模型写。

**经验**：①用户反对的是「删功能来消症状」——症状消了但功能少了，他会立刻发现并要求还回来，
所以宁可多花一轮找真根因；②凡「版本号对不上」类误报，先分清**磁盘版本 / 运行时版本**两回事，
别急着放宽判定；③功能上线必须同一提交更新 `WORKBENCH_SYS`（硬规矩 6），否则 AI 会当着用户
的面否认自己的能力，比没这功能更伤信任。

**交付**：13 件 785 条全绿（e2e_wb 涨到 189，E9 专项只认配置真的变了）；三处钉齐 v1.11.7；
双 zip 在 `release/`（旧 1.11.6 已删）；扩展 JS 零改动，只跟着 bump manifest。

## v1.11.6（AE/AF 窗口，2026-08-01）：E5 提示词功能清单 + E6 开服监听多目标

**用户原话**：「我问他你能监听服务器是否开启吗？他回复这个程序做不到……但是我上面不是叫你加了
这个功能吗？这模型都不知道自己能干啥还咋工作？」「开服监听为啥只能监听一个，做事做周到一点」
「这个名字不要写旅途啊，又不是所有服务器都叫旅途」。

**根因**：
- E5 是**提示词欠账**，不是模型笨：`WORKBENCH_SYS` 停在 v1.8.0 时代，D3/C2/B1/A7/D1 一个字
  都没写进去。模型照老提示词老实回答「做不到」——它没说谎，是我们没告诉它。
  **教训写进 AGENTS 硬规矩：新增任何用户可见功能，必须同步 `WORKBENCH_SYS` 的功能清单一节。**
- E6 是**观感 bug**：后端 `watch` 表 + `watch_save` 本来就支持任意多条、无上限，界面却写
  「监视目标 · 盯一个网址开没开」，表单又摆在页面顶部，读起来像单目标配置面板。
  用户对「能不能加多个」的判断完全来自文案和布局，不看后端。

**修法**：E5 给系统提示词加按侧栏逐条的功能清单 + 硬话（不确定不许断言做不到）+ 实况带
开服监听目标数/状态；E6 全是文案与计数（标题、说明、列表顶部计数、空态、placeholder）。

**经验**：
1. 功能上线 ≠ 模型知道。凡是"AI 说它做不到"的投诉，先查提示词，别怀疑模型。
2. 「只能加一个」这类投诉先去后端查有没有真限制 —— 这次后端没限制，改文案就够，
   千万别急着"实现多目标"把已有代码重写一遍。

## 最后更新时间
2026-08-01 · v1.11.4 出包轮（Z+AA+收尾窗口）—— E1 多行错误块 / E2 拉模型四层兜底，代码侧清零

## 新账号 / 新窗口先看这三行
- 用户的账号是「10x10」：**每个窗口 10 条消息，用完换窗口**。进行中的状态在 `NOW.md`（粒度到步），
  接手先读它，别问用户上次做到哪。每做完一步就用 `.bcode/agent-workspace/save.py` 存盘推送。
- 接手后照 `START_HERE.md` 第 5.5 节把项目指针写进**新工作区根目录的 `AGENTS.md`**，
  别往后拖 —— 换窗口丢的从来不是代码，是「该去哪读」这个指针。
- 工作区里的 `claude/` 是**死快照**（没有 `.git`）。干活永远 `git clone` 到 `/tmp` 上改，改完推。

## 当前状态
**v1.11.4 已出包（2026-08-01，Z+AA+收尾窗口）**：E1（报错多行块不撑破页）+ E2（中转站拉模型
ClientPayloadError 四层兜底）落盘并出包。程序 / EXT_MIN / manifest 三处钉齐 **v1.11.4**（硬规矩 2）。
程序 zip（27 文件）+ 扩展 zip（10 文件）在 `release/`，旧 1.11.3 两个包已删。
回归：e2e 53→60（mockllm 新增 4 种不规范中转站桩 + 7 条断言），全套件此轮跑过全绿。

下一件按顺序：
1. **等用户装 v1.11.4 真机验收 + 要新诊断包**（**程序扩展都要换**：扩展版本对齐，必须重装）。
   主验两条：①两台模型服务（DeepSeek 官方 + youzi.today 中转）「拉取模型列表」能拉到（E2）；
   ②拉失败时红字是多行块、不再把页面往右撑（E1）。
   顺验 v1.11.3 三条：行内表情留在正文 / 开帖开最小化新窗口 / 开服监听。
   注意他诊断包里：一个桥还是 v1.11.2 旧扩展（有旧 Discord 标签页没刷）、规则 0 条
   （收信闸拦 10 条＝他自己要的"没规则不收信"，不是 bug）。
2. 用户新反馈（验收后大概率有）。
**代码侧 BACKLOG 已清零。**

## 已完成

### v1.11.4：E1 报错多行块 + E2 拉模型四层兜底（2026-08-01，Z+AA+收尾窗口）

- **E1 报错不再撑破页面**（`8a7924e`）：拉模型失败的红字原来塞在 `span.tag`（nowrap）里、
  又待在 flex 行中，一长串把整页向右撑开、还得悬停看全文。改为服务卡片下的独立多行错误块：
  可换行可选中、长 URL 断得开、不截断不靠悬停；toast 同步限宽换行。
- **E2 中转站拉不到模型根治**（`8a7924e`+`829ec2e`）：`ClientPayloadError`＝对方压缩体
  aiohttp 解不开（别的软件容忍不规范所以能拉到，不是用户网络问题）。四层兜底：出网调用显式
  `Accept-Encoding: identity`+UA → 兜底宽容读原始字节（容截断、gzip/deflate/br/明文依次试解）→
  JSON 截断/前缀垃圾抢救 → 实在不行从文本捞模型 id。删掉「多半是你代理/杀毒」的误导提示
  （诊断包显示用户根本没设代理）。mockllm 加 4 种不规范中转站桩（长度对不上/JSON 截断/
  前缀垃圾/压缩解不开），e2e 53→60 全绿。
- 插曲：Z 窗口只推了认领就被额度掐断，AA 窗口重做一次落盘并出包，本窗口补文档收尾——
  全程靠 NOW/BACKLOG 接力，无丢失。

### v1.11.3：D1 行内表情 + D2 开帖新开最小化窗口 + D3 开服监听（2026-08-01，W+X 窗口）

- **D1 行内表情不再丢**（X 窗口 `2cf049b`）：用户「发表情包只显示一段字符串」。现状核查发现
  「纯表情消息翻译成 [贴纸 xx]/:emoji: 占位」v1.8.0 就有，真正的缺口是**行内表情**（文字中间
  夹的 <img>）被 innerText 整个丢掉，「看 :kekw: 这个」变「看  这个」——正是用户看到的
  「一段字符串」。修法：content.js 新增 `textWithEmoji`（克隆正文节点把表情 img 换成代码文字
  再取文本，parseLi 改走它）+ `emojiCode` 剥掉 Discord 同名表情的 `~N` 后缀（:cat_cry~1: →
  :cat_cry:，mediaOf 的 emoji 分支同步）。content_test.mjs 补 3 条断言；浏览器侧 107 条全绿。
  `media` 字段下游（server.py/ui.html/tests）零使用，纯表情消息 media 变 [] 无影响面。
- **D2 自动开帖改新开最小化窗口**（W 窗口 `7f186ee`）：用户实测「一个浏览器多个窗口时没在看的
  那个窗口不加载，消息收不到」，要求开帖别在当前浏览器多开标签页。background.js `openPostTab`
  改 `chrome.windows.create({state:"minimized",focused:false})`，失败退回 tabs.create；
  闲置自关时窗口里只有它一个标签页，窗口跟着关。tabs_harness 补 windows 桩 + 7 条断言（runTabs 42）。
- **D3 开服监听**（W 窗口 `ca25b59`）：用户「旅途关服了，开放过一会没人说又关了，错过了」。
  服务端 watch 表 + watch_loop 巡检 + watch_probe 判定（HTTP 状态 + expect 必有字 + absent
  禁有字），**首次只落状态不吭声、翻脸才提醒**，走通知管线 notify_watch（复用 queue_local 和
  全部 hooks = 弹窗/声音/转发全吃）。/api/watch CRUD + /check；诊断包 [5.9] 段；ui.html 新侧栏页
  「⌖ 开服监听」；e2e_watch 26 条。
- 出包（X 窗口）：三处钉齐 v1.11.3；README 新节 + 自查行 + 启动行 + release 名；release/README、
  START_HERE、RUN.md/AGENTS 计数（run 46→49、tabs 35→42）同步；服务端 13 件 704 条全绿；
  双 zip 进 release/（旧 1.11.2 已删）+ outputs/；冒烟：启动行三处 v1.11.3 ✓ /version 5 处 ✓
  /api/presets ✓ 双 zip 文件清点 27+10 与旧版一致 ✓ 扩展包 manifest/textWithEmoji ✓。

### v1.11.2：C6 后台标签页防冻结/防回收（2026-08-01，V 窗口）

- 起因：用户「窗口必须放前台 discord 才能继续更新消息，规则多的话要多开窗口挂前台，有没有适配优化」。
- 正解：后台标签页**本来就能**收（MutationObserver 不受可见性影响、chrome.alarms 心跳兜底），
  真正会断的是 Chrome「内存节省程序」的**冻结**（JS 全停，消息全漏）和**回收**（页面卸载）。
- 落盘（extension/background.js）：`isDiscordTab` + `protectTab`（`chrome.tabs.update(id,
  {autoDiscardable:false})`，官方唯一防回收 API）+ `protectAll`（30s 巡检：全部频道页补设 +
  discarded/frozen 的立刻后台 reload 救回，不抢焦点）；三入口（onCreated / onUpdated(loading) /
  alarm+startup+installed）；救回数记 `st.rescued`，badge 悬停提示「救回过 N 个被 Chrome 回收的标签页」。
- 文档：README 新一节「能不能挂在后台不管它」（能；两个敌人；手动兜底 chrome://settings/performance
  加 discord.com；一个标签页=一个频道，盯几个开几个后台标签页）+ 故障对照一行。
- 测试：tabs_harness 桩补 `tabs.update/reload/onCreated/onUpdated` + **8 条 C6 断言**（runTabs 35/35）；
  run 46/46、runFresh 16/16；服务端 12 件 678 条全绿（改 server.py 的版本号也全跑了一遍）。
- 出包：三处钉齐 v1.11.2；双 zip（27+10 文件）进 release/（旧 1.11.1 已删）+ outputs/；
  全新解压冒烟：启动行三处 v1.11.2 ✓、/version 页 5 处 ✓、/api/presets ✓。
- 端口提醒：server.py 的端口走 `--port` 参数，**不是 PORT 环境变量**（V 窗口冒烟时踩过，
  PORT=8892 无效实际起了 8777）。

### v1.11.1 补钉：版本号对齐（2026-08-01，U 窗口）

- 起因：用户「拓展和程序版本必须对上，不然不好看」。v1.11.1 出包时按老规矩 2「扩展没动就别碰」
  只 bump 了程序，导致程序 1.11.1 / 扩展 1.11.0。
- 落盘：manifest + EXT_MIN 同步 1.11.1；README/release-README/START_HERE 的「不用重装扩展」全部
  改成「这轮扩展也要重装一次」；双 zip 重打（程序 27 文件 / 扩展 10 文件）进 release/ + outputs/。
- **规矩改写（AGENTS.md 硬规矩 2）：bump 程序 VERSION 时 manifest+EXT_MIN 必须同步 bump，
  扩展 zip 每次跟着出——三处永远钉齐，不存在「只动程序」的版本。** 这是用户拍板的，以后别再用
  「白让他重装一遍比不修还糟」当理由只 bump 程序。
- 验证：ext/diag/tabs/e2e 四套 206 条绿（其余 8 套零版本引用）；全新解压冒烟启动行 + /version
  自证页三处全 v1.11.1。

- **v1.11.1（R 窗口）出包** —— VERSION 1.11.1（只 bump 程序）；README 改版节 + release/README +
  START_HERE 同步；AGENTS.md 打包清单修正（dcwatch.spec 要留）；全新解压冒烟过
- **A7 + C5（Q 窗口，P 认领后断）** —— server 新增 `covers()`（什么时候+听哪里+听谁，
  关键词档不挡收信），`match()`=covers()+听内容；`_handle_event` 落库前过闸：**零规则=零收信**，
  自检消息（channel_id="0"）豁免、抓历史 store_only 不过闸、C2 自动开帖排队在闸前；
  拦下数 `_gate_n` 进诊断[0]/[3]段；删 composer 点空白聚焦监听；e2e +3 条 A7 断言，
  multi/diag/wiz/tabs 补兜底规则；README 三处（第一条规则 ID 拿法 / [3]段 / 故障表「收信闸」行）
- **e2e_chk.py 专项债（O 窗口）** —— mockllm 加 aicheck 分支 + `/__chk` 脚本队列；
  `tests/e2e_chk.py` 56 条一次全绿（PLAN_B1 §3.3 的 13 条必钉全覆盖，含 fail open 三连、
  附件转人工零调用、每日上限）;全套回归复跑 **675 条全绿**；RUN.md 补覆盖清单+三个坑
- **v1.11.0（本轮 N 窗口）C2 收尾** ——
  · 扩展侧早前已落地（L `c13a0d8`：tabs 权限、background tabOrders 开/关/回报、storage opened、
    popup 自检行、content 药丸「· 自动」）；ui.html 折叠区 `842d069`；e2e_tabs 53 `66a78a9`
  · 本窗口补：`tests/content_test.mjs` runTabs + `tabs_harness.js` **27 条** chrome.tabs 桩全绿
  · VERSION/EXT_MIN/manifest **1.11.0**；README 新节 + 改版说明 + 故障表；START_HERE/release 同步
  · 全套回归全绿；程序+扩展两个 zip
- **v1.10.0 及以前**（详见 git 历史 / 下面存档）：A1–A6、B1–B6、C1、C3、C4、D 全落地

## 未完成（按优先级排序）
- [ ] **等用户装 v1.11.3 真机验收 + 要新诊断包**（程序扩展都要换，扩展有功能改动）
- [ ] exe 打包只能在用户本机做；扩展未在真 Discord load unpacked 端到端验过（靠假 DOM 回归）

## 注意事项 / 踩过的坑
- **e2e_chk 的模型名用 "mock-chk" 不用 "mock-1"**：`App.no_tools` 是进程内内存，跟 e2e_wb
  共用模型名会互相污染（PLAN_B1 点名过的坑）；复核上下文是 `ts<=当前` 取 n+1 条，
  当前这条也在结果里靠正文不等滤掉 —— 造测试数据别让上文跟触发消息一字不差
- **C2 扩展字段名 ≠ 服务端字段名**：扩展用 `aoOn` / `rep.opened|closed|failed` / `chrome.tabs.*`，
  服务端用 `tabs_report` / `threads_open` / `idle_close`。用服务端名 grep 扩展会**错判没做**（M 窗口踩过）
- **runTabs 的 harness 必须是 async IIFE**，且宜拆成 `tabs_harness.js` 再 Runtime.evaluate 注入；
  塞进 content_test.mjs 模板字符串里容易把 `async` 弄丢 → Uncaught。
  **harness 里的 chrome 桩和 tabOrders/protectAll 是 background.js 的手工镜像**——改 background.js
  用了新 chrome API 必须在桩里补（tests/RUN.md 也写着）；C6 这次补了 update/reload/onCreated/onUpdated
- 交付包 **27 文件**（含 presets/）；扩展包 **10 文件**；每次出程序包都跟着出扩展 zip（硬规矩 2，三处钉齐）
- 出包前三处版本号必须一致（VERSION / EXT_MIN / manifest）。半吊着出包用户必装错
- 其余坑见 v1.10.0 段（import_rules 不加、export 打码、WB_PACK_LIMIT、跑回归先 kill 8777、
  .bat GBK+CRLF、setsid 起服务、推送用一次性 URL PAT）


<details>
<summary>v1.10.0 及更早的技术细节（点开看）</summary>

见 git 历史中本文件 2026-08-01 K 窗口版本，以及 PLAN_*.md / BACKLOG 各条 commit。
要点：A1/C1 工作台落库；A2 回复消息作者；A3/A5 通知 tag+sinks SSE；A4 转发出口 UX；
A6 拉模型三根因；B1 AI 复核；B2/B3/B4/B5/C3 提示词/采样/后处理/预设；B6 只读工具；
C4 输入框；C2 服务端 f5cd2c5；D 命名约定。

</details>
