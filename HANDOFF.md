# HANDOFF

## 最后更新时间
2026-07-31 11:45（北京时间）

## 当前状态
dcwatch **v1.7.3**（扩展仍为 v1.7.2，本版没改扩展）。回归 273 项全绿
（e2e 46 + ai 27 + multi 26 + wiz 81 + v17 46 + diag 47）。
用户已经跑上 v1.7.2 且扩展装好了（心跳正常、能解析页面），但**规则填错**导致从未提醒过：
他建了两条规则盯同一个频道 ID —— 能听消息的那条是停用的，开着的那条只听「新帖」。
v1.7.3 就是为了让这类错误自己浮出来。

## 已完成
- 单进程 aiohttp 服务端（server.py）：Discord 监听 / 规则引擎 / OpenAI 兼容模型调用 / SQLite 存储
- 单文件 GUI（ui.html，Claude 风格）：收信箱、监听规则、模型接入、通知与转发、批量提取、运行日志、设置
- MV3 浏览器扩展（extension/）：读 discord.com DOM 上报消息，页面内状态药丸 + popup 自检 + 抓历史
- 三条接入方式：浏览器旁听（默认，不需要 token）/ Bot Token / User Token
- 引导式建规则（/api/rules/wizard）与一句话生成规则（/api/rules/compose）
- 通用出口 hooks：Windows toast、提示音、Discord Webhook、Telegram、Server酱、企业微信、自定义 webhook
- 新帖检测（论坛开帖）、抓历史、批量提取 + CSV 导出
- 自证渠道：/version 独立页面、诊断包 /diagnose.txt（[0] 一眼结论段）、启动时打印版本与目录
- Windows 双击即用：启动.bat / 停止.bat / build.bat + freeport.py 自动清理占用端口
- **v1.7.3（本轮）**：诊断包 [4] 段印出规则的 kinds（「什么时候触发」）；新增 **[4.5] 拿真实消息试算**
  （用库里的真消息，库空则用扩展 recent 里的正文，对每条规则跑 match 并把卡点翻成人话）；
  findings 新增「开着的规则只听新帖」并按 bad→warn 排序；新增 NICE_WHY 把 match 的卡点代码译成人话

## 未完成（按优先级排序）
- [ ] **v1.7.3 未推 GitHub**（远端最后是 `0680cf3` / v1.7.2）。用户是从 GitHub 拉文件的，
      不推等于没交付。要推：让他明说「推」并现给一次性 PAT，推完提醒 revoke。
      推送步骤见 AGENTS.md「推送流程」，注意 `.bat` 会被工作区同步删掉，要先从 zip 里 extract 回来
- [ ] 等用户按三步改完规则（打开「频道监听-所有消息」/ 处理掉「帖子监听-所有消息」/ 自己发一条测试），
      确认「收信箱出现 + 弹窗响」这条链路真的通了。不通就再要一份 v1.7.3 的诊断，直接看 [4.5] 段
- [ ] 规则没有导入/导出。用户填错了只能一格格改，给不了他一份「拿去直接用」的规则。
      要做就加 /api/rules/import + 界面导出按钮
- [ ] exe 打包只能在用户本机做（PyInstaller 无法在 Linux 交叉编译），dist\dcwatch.exe 从未在真实 Windows 上验证过
- [ ] 扩展始终没能在真实浏览器里 load unpacked 跑过端到端（云环境上传桥不可用），靠假 Discord DOM 的回归兜底

## 注意事项 / 踩过的坑
- **「有人发消息」和「有人开新帖」是规则里两个独立勾选（kinds）**，只勾后者时频道里聊翻天也不会响。
  这是本轮用户踩的坑，也是最难自查的一格 —— 任何新增的规则字段都要问一句「诊断包里印出来了吗」
- 排查「填好了为什么不响」不能只看规则条件本身，要拿真消息跑 match()。[4.5] 段就是干这个的
- messages 表**没有 mentions_me 列**，所以 [4.5] 里勾了「只在@我」的规则一定算不中，已单独注一句
- 本 workspace 会被整体同步覆盖，`.bat` 和 `.gitattributes` 会消失。改包前先
  `find outputs/dcwatch -type f | wc -l`（应为 26 + CLAUDE/HANDOFF = 28），少了就从上一版 zip 里 extract
- Windows 的 .bat 必须 GBK + CRLF；echo 文本里的 `>` 要转义成 `^>`
- 改完扩展用户必须两步：chrome://extensions 点刷新箭头 + Discord 页面 F5。
  **本版没改扩展，所以 EXT_MIN 保持 1.7.2、manifest 一字不动** —— 白让用户重装一遍扩展比不修还糟
- 交付 zip 一律带版本号，且旧版 zip 删掉，避免他拿错
- 回归一套一套跑，跑前 `rm -f /tmp/p.db*` 并重启 server，别同时跑两套
- 所有出网调用必须走 App.rq()（trust_env + net.proxy），否则国内直连 api.openai.com 必超时
- 界面绝不能拿假数据冒充成功；S.live 一旦为真永不回退
- 微信只走 Server酱 / 企业微信官方通道；火狐已彻底放弃，只支持 Chrome / Edge
