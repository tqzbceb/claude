# HANDOFF

## 最后更新时间
2026-07-31 12:05（北京时间）

## 当前状态
dcwatch **v1.7.4**（扩展也是 v1.7.4，本版**改了扩展，用户必须重装/刷新**）。
回归全绿：服务端 273 项（e2e 46 + ai 27 + multi 26 + wiz 81 + v17 46 + diag 47）
+ 浏览器里的 content.js 回归 34 项 + 新旧判定 16 项。
GitHub 远端已跟上（v1.7.3 = `c1d08be`，v1.7.4 见本次提交）。
用户上一轮的问题（规则填错，能听消息的那条停用了、开着的那条只听新帖）已由 v1.7.3 的
诊断包 [4.5] 段覆盖，**但还没等到他的回执**，不知道他改没改对。

## 已完成
- 单进程 aiohttp 服务端（server.py）：Discord 监听 / 规则引擎 / OpenAI 兼容模型调用 / SQLite 存储
- 单文件 GUI（ui.html，Claude 风格）：收信箱、监听规则、模型接入、通知与转发、批量提取、运行日志、设置
- MV3 浏览器扩展（extension/）：读 discord.com DOM 上报消息，页面内状态药丸 + popup 自检 + 抓历史
- 三条接入方式：浏览器旁听（默认，不需要 token）/ Bot Token / User Token
- 引导式建规则（/api/rules/wizard）与一句话生成规则（/api/rules/compose）
- 通用出口 hooks：Windows toast、提示音、Discord Webhook、Telegram、Server酱、企业微信、自定义 webhook
- 新帖检测（论坛开帖）、抓历史、批量提取 + CSV 导出
- 自证渠道：/version 独立页面、诊断包 /diagnose.txt（[0] 一眼结论段、[4.5] 拿真消息试算）、启动时打印版本与目录
- Windows 双击即用：启动.bat / 停止.bat / build.bat + freeport.py 自动清理占用端口
- v1.7.3：诊断包 [4] 段印规则的 kinds；新增 [4.5] 拿真实消息逐条试算并把卡点译成人话；
  findings 新增「开着的规则只听新帖」并按 bad→warn→ok 排序
- **v1.7.4（本轮）后台标签页的两个迟到问题**：
  · content.js 合批时**第一条立刻发**，不再干等 600ms 定时器
  · background.js 新增 `pullTabs()`：chrome.alarms 每 30 秒主动向标签页要一次心跳
    （`dcwatch-pull` → content.js 的 `pingBody()`），只在「标签页超过 30 秒没报」时才拉。
    **不筛 url，因此不需要任何新权限** —— 加 `tabs` 会弹「读取浏览记录」，
    加 discord.com 的 host_permissions 会让已装好的扩展被 Chrome 停用等重新授权
  · 之前那一版只加了 content.js 的接收端、没有发送端，是半截代码（本轮补完）

## 未完成（按优先级排序）
- [ ] 等用户装上 v1.7.4 并按三步改完规则（开「频道监听-所有消息」/ 处理掉只听新帖那条 /
      自己在频道里发一条），确认「收信箱出现 + 弹窗响」真的通了。不通就要一份新诊断，直接看 [0] 和 [4.5]
- [ ] 规则没有导入/导出。用户填错了只能一格格改，给不了他一份「拿去直接用」的规则。
      要做就加 /api/rules/import + 界面导出按钮
- [ ] exe 打包只能在用户本机做（PyInstaller 无法在 Linux 交叉编译），dist\dcwatch.exe 从未在真实 Windows 上验证过
- [ ] 扩展始终没能在真实浏览器里 load unpacked 跑过端到端（云环境上传桥不可用），靠假 Discord DOM 的回归兜底
- [ ] `pullTabs()` 会向所有标签页广播 `dcwatch-pull`（无权限方案的代价）。标签页极多时是几十次
      必然失败的 sendMessage，可接受但不优雅；若将来已经要加权限了，可顺手改回按 url 筛

## 注意事项 / 踩过的坑
- **改了扩展就必须 bump manifest version 和 EXT_MIN**，并在回复里告诉用户该看到几点几。
  反过来，扩展没改就别动这两个 —— 白让他重装一遍比不修还糟。
  本轮踩到的反例：上一版改了 content.js 却没 bump，于是同样标着 1.7.2 的扩展有两种内容
- **测试里不要写死版本号**。e2e_diag 把桥的扩展版本写死成 1.7.2，EXT_MIN 一提到 1.7.4
  就假红三条。现在改成从 `/api/state.env.ext_min` 取
- 加了 chrome.runtime 的新消息类型，要同时在 content_test.mjs 的 chrome 桩里补 `onMessage`，
  否则 content.js 的 try/catch 会把它静默吞掉，测试看着全绿其实那段代码根本没跑
- **「有人发消息」和「有人开新帖」是规则里两个独立勾选（kinds）**，只勾后者时频道里聊翻天也不会响
- 排查「填好了为什么不响」不能只看规则条件本身，要拿真消息跑 match()。[4.5] 段就是干这个的
- messages 表**没有 mentions_me 列**，所以 [4.5] 里勾了「只在@我」的规则一定算不中，已单独注一句
- 本 workspace 会被整体同步覆盖，`.bat` 和 `.gitattributes` 会消失。改包前先
  `find outputs/dcwatch -type f | wc -l`（应为 26 + CLAUDE/HANDOFF = 28），少了就从上一版 zip 里 extract
- Windows 的 .bat 必须 GBK + CRLF；echo 文本里的 `>` 要转义成 `^>`
- 交付 zip 一律带版本号，且旧版 zip 删掉，避免他拿错
- 回归一套一套跑，跑前 `rm -f /tmp/p.db*` 并重启 server，别同时跑两套
- 所有出网调用必须走 App.rq()（trust_env + net.proxy），否则国内直连 api.openai.com 必超时
- 界面绝不能拿假数据冒充成功；S.live 一旦为真永不回退
- 微信只走 Server酱 / 企业微信官方通道；火狐已彻底放弃，只支持 Chrome / Edge
- **推送凭据已长期保存**在 workspace 的 `.bcode/agent-workspace/secrets/github_pat.txt`（chmod 600，
  不在仓库里、不在 outputs 里）。用户 2026-07-31 明确要求「每次任务做完都推」，所以不必每轮再问
