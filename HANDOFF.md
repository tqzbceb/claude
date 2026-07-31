# HANDOFF

## 最后更新时间
2026-07-31 10:59（北京时间）

## 当前状态
dcwatch v1.7.2 已完成并通过全部回归测试（服务端 255 项 + 扩展解析 29 项）。程序在用户机器上已经能跑起来，
但用户端还停留在 v1.6.0，且一条监听规则都没建，所以实际还没收到过任何提醒。

## 已完成
- 单进程 aiohttp 服务端（server.py）：Discord 监听 / 规则引擎 / OpenAI 兼容模型调用 / SQLite 存储
- 单文件 GUI（ui.html，Claude 风格）：收信箱、监听规则、模型接入、通知与转发、批量提取、运行日志、设置
- MV3 浏览器扩展（extension/）：读 discord.com DOM 上报消息，页面内状态药丸 + popup 自检 + 抓历史
- 三条接入方式：浏览器旁听（默认，不需要 token）/ Bot Token / User Token
- 引导式建规则（/api/rules/wizard）与一句话生成规则（/api/rules/compose）
- 通用出口 hooks：Windows toast、提示音、Discord Webhook、Telegram、Server酱、企业微信、自定义 webhook
- 新帖检测（论坛开帖）、抓历史、批量提取 + CSV 导出
- 自证渠道：/version 独立页面、诊断包 /diagnose.txt（含 [0] 一眼结论段）、启动时打印版本与目录
- Windows 双击即用：启动.bat / 停止.bat / build.bat（PyInstaller 打 exe）+ freeport.py 自动清理占用端口
- v1.7.2 修复：一批冒出 8 条以上不再无脑当「渲染回填」整批丢弃（改为按 snowflake 时间戳判断），
  并新增 stale_ctx 检测（扩展重载但页面没按 F5 的状态现在能被一眼认出）

## 未完成（按优先级排序）
- [ ] 等用户把 v1.7.2 装上并按 F5 后，确认「自己在目标频道发一条消息 → 界面收信箱立刻出现」这条链路真的通了；
      如果仍然收不到，让他导出 v1.7.2 的诊断包（第 [0] 段就是结论），按结论定位
- [ ] 用户手上一条规则都没有。引导他用「监听规则 → ◆ 帮我建一条」生成第一条规则，
      场景是 AI API key 分享社群：有人发 key / 发邀请码 / 放名额 / 在资源区开新帖时提醒，纯闲聊不提醒
- [ ] 规则目前只能在界面里一条条建，没有导入/导出。若用户需要把现成规则直接给他用，
      需要新增 /api/rules/import 与导出按钮
- [ ] exe 打包只能在用户本机做（PyInstaller 无法在 Linux 交叉编译），尚未在真实 Windows 上验证过 dist\dcwatch.exe
- [ ] 扩展始终没能在真实浏览器里 load unpacked 跑过端到端（云环境上传桥不可用），
      目前全靠假 Discord DOM 的回归测试兜底

## 注意事项 / 踩过的坑
- Windows 的 .bat 必须存成 GBK + CRLF。存成 UTF-8 时中文控制台按 GBK 解析，乱码里会蹦出 `&`，
  cmd 当成命令分隔符，用户会看到「'aiohttp' 不是内部或外部命令」这种莫名报错。
  echo 文本里的 `>` 也要转义成 `^>`。仓库里有 .gitattributes 写死 `*.bat -text` 防止换行被转换
- 改完扩展代码，用户必须做两步：chrome://extensions 点扩展卡片的刷新箭头 + Discord 页面按 F5。
  只覆盖文件不刷新等于完全没变化。所以每次改扩展都要 bump manifest 版本号，让他能自查
- 「装了还是显示旧版本」有三个来源：从 GitHub 下 ZIP 拿到的是最后一次 push 的版本；界面里的
  「下载浏览器扩展」打包的是用户自己磁盘上那份 extension 文件夹；Chrome 的 load unpacked 记的是
  文件夹路径，解压到新位置后点刷新读的还是老路径
- exe（frozen）模式下数据目录是 %LOCALAPPDATA%\dcwatch，全机共享。换文件夹跑会带着上一份配置，
  这不是密钥泄露，界面「设置」第一张卡片和 /version 页都会把真实数据目录摊开给用户看
- 所有出网调用必须走 App.rq()（带 trust_env + net.proxy 配置），否则国内直连 api.openai.com 必超时
- 界面绝不能拿假数据冒充成功。S.live 一旦为真永不回退，任何请求失败都要 toast 出真实状态码
- 回归测试一套一套跑，跑之前清库并重启服务端（去重守卫和 findings 都对库和进程内的桥敏感）。
  别同时跑两套，它们会互相清库
- 微信推送不做 itchat / wechaty 这类逆向方案（封号风险），只走 Server酱 / 企业微信官方通道
- 火狐已彻底放弃：标准版禁止长期安装未签名扩展，且不支持 MV3 background.service_worker。只支持 Chrome / Edge
