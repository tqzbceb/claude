# 从这里开始（给刚接手的 AI）

你被换到了一个新账号 / 新窗口 / 新工作区。**聊天记录一句都过不来，也不需要。**
这个仓库就是全部记忆。花 5 分钟读完这一页，你会跟上一轮的我知道得一样多。

---

## 0. 一句话：这是什么

**dcwatch** —— 盯 Discord 指定频道的消息，按规则筛，命中了本机弹窗 + 转发到微信 / Telegram /
Webhook，可选让大模型判分打标签。单进程 aiohttp + SQLite + 单文件网页界面 + 一个 MV3 浏览器扩展。
当前版本：程序 **v1.11.6**、扩展 **v1.11.6**（程序扩展版本永远钉齐；v1.11.6 扩展只有版本号
跟着钉齐、没有功能改动，不重装也能用，只是卡片显示旧号）。

用户是**一个人在用的普通 Windows 11 用户，不写代码、不用命令行**。他要的是双击就能跑的东西。

## 1. 立刻做这四件事（照做，别重新规划）

1. **别问「上一轮聊了什么」。** 问了也没人答得出，而且答案全在这个仓库里。
2. **确认环境是好的**（一次就够）：

   ```bash
   pip install aiohttp                              # 唯一依赖，新环境每次都要装
   cd tests && ./runall.sh e2e.py e2e_imp.py        # 应为 46 / 74 全绿
   ```

   不绿 = 环境问题，不是代码问题（这两套在干净 clone 上反复跑过）。
3. **必读只有三份，按顺序**：`NOW.md`（手停在哪一步 + 窗口协议，聊天记录的替代品）→
   本页 → `AGENTS.md`（项目怎么运作、用户是谁、**十一条硬规矩**、排查「它没提醒我」的固定顺序）。
   其余按需查：`HANDOFF.md`（NOW 说"没有进行中的任务"时去那儿挑新活 + 历轮踩坑）、
   `CLAUDE.md`（收尾时 HANDOFF 的固定结构照它写）、`tests/RUN.md`（跑回归前读）。
4. **v1.11.6 已出包（2026-08-01），欠用户真机验收**：程序和扩展 zip 都在 `release/`（两个都是
   v1.11.6）。**换程序即可**（扩展这轮无功能改动；覆盖时别删 `dcwatch.db`）。验新东西：
   报错红字改多行折行块、不再撑破页面（E1）、中转站拉模型四层兜底（E2：以前 `ClientPayloadError`
   直接失败，现在能救回来还会说一句"走了兜底"）。上一轮的三条别丢：
   行内表情不再丢（D1：「看 :kekw: 这个」不再变「看  这个」，`:cat_cry~1:` 剥成 `:cat_cry:`）、
   自动点开新帖改新开最小化窗口（D2：不再当前浏览器多开标签页；帖子闲置自关时窗口跟着关）、
   开服监听（D3：侧栏「⌖ 开服监听」，盯网址从不开翻成开的第一时间走通知管线，刚建只落状态不吭声）。
   上一轮的两条别丢：后台标签页防冻结（C6：挂后台久不再漏消息，图标悬停看「救回过 N 个」）、
   收信箱规则驱动（A7：没规则=零收信，诊断包[3]段看拦下数）、工作台输入框单光标（C5）、
   拉模型（A6）、规则「AI 复核」（B1）、采样参数 + 后处理（B4/B2）、
   提示词抽屉（B3）。
   早前三条别丢：A2 回复消息盯人、A3 两标签页只弹一条、A5 关三开关立刻全停。
   另外他机器上的规则填得对不对，
   一直没人见过——发版后顺嘴要一次诊断包（界面「运行日志」页→「导出诊断」→ txt 发你），
   拿到先看 `[0] 一眼结论` 和 `[4.5] 拿真实消息试算`，**别靠肉眼比对规则条件**。

## 2. 用户的硬约束（违反过，代价付过）

- **不给他命令行**。报错要给「症状 → 怎么办」对照表。启动方式只有一个：双击 `启动.bat`。
- **任务做完直接提交并推送，不要停下来问他**（他 2026-07-31 明确说过两次）。
- **改了 `extension/` 才 bump `manifest.json` 的 version + `server.py` 的 `EXT_MIN`**；
  没改就别动 —— 白让他重装一遍扩展比不修还糟。
- **界面绝不能拿假数据冒充成功**。
- 其余八条在 `AGENTS.md`「硬规矩」一节，动手前扫一眼。

## 3. 凭据（PAT）

**推送凭据不在仓库里，也不该在。** 用户通常把 PAT 直接贴在开场消息里（他的窗口有消息额度，
一来一回要 PAT 太贵）——**看到 `github_pat_` 开头的字符串就视为本次推送授权，直接用，别再确认**。
消息里没带的话再开口要。安全边界靠 PAT 本身收窄，不靠藏着不发：
**建议用户用 fine-grained PAT，只授权 tqzbceb/claude 一个仓库、只给 Contents 读写、
有效期 ≤90 天**——这样贴在消息里泄了也只影响这一个仓库，过期自动作废。
PAT **绝不写进仓库文件或任何会推送的文件**，用一次性 URL 推：

```bash
git push "https://x-access-token:<PAT>@github.com/tqzbceb/claude.git" HEAD:main
sed 's/github_pat_[A-Za-z0-9_]*/***TOKEN***/g'      # 任何会打印 URL 的输出都过一遍
git remote set-url origin https://github.com/tqzbceb/claude.git   # 推完把 URL 复位
grep -rl github_pat_ .git/                          # 必须没有输出
```

推之前先 `git ls-remote` 看远端到哪了，别信记忆里的 sha。

## 4. 给用户交付东西怎么给

他要的是一个 zip，解压后双击 `启动.bat`。**交付包里留 `README.md`（那是给他看的说明书），
去掉 `tests/`、`release/`、给你看的几份 .md 和两个点文件**，干净 **27 个文件**（v1.10.0 起含 `presets/`，服务端运行时读它，别删）：

```bash
V=$(grep -oP 'VERSION = "\K[^"]+' server.py)        # 版本号只有一个来源：server.py
rm -rf __pycache__ tests/__pycache__
rm -rf /tmp/pack && mkdir -p /tmp/pack/dcwatch && cp -r ./. /tmp/pack/dcwatch/
cd /tmp/pack/dcwatch && rm -rf .git tests release .gitignore .gitattributes __pycache__ \
    AGENTS.md CLAUDE.md HANDOFF.md START_HERE.md NOW.md BACKLOG.md BOOTSTRAP.md agent PLAN_*.md docs
    # README.md 和 dcwatch.spec 要留下（spec 是 build.bat 打包 exe 用的）
find . -type f | wc -l                              # 必须是 27（v1.10.0 起）
cd /tmp/pack && python3 -m zipfile -c "dcwatch-v$V.zip" dcwatch
```

放进你这一轮工作区的 `outputs/`（用户只能下载这个目录里的东西），旧版本 zip 删掉。

**`release/` 目录里有一份已经打好的最新 zip**，是给用户在「没有 AI 帮忙」时也能直接从
GitHub 网页下载用的。你出了新版本就把里面的旧 zip 换掉（**只留最新一个**，别让仓库越长越胖）。

## 5. 这个运行环境的坑（Browser Use Cloud v4 上跑的话）

**用户的账号是「10x10」：每个窗口 10 条消息，用完必须换窗口，换窗口＝换会话＝聊天记录一句不过来。**
所以「会话结束前收尾」这种打算是不成立的 —— 会话是被额度掐断的，不会给你收尾的机会。
**每做完一步就存盘**：`python3 .bcode/agent-workspace/save.py "做了什么"`
（commit + 推 GitHub + 把 .md 同步回工作区快照），顺手改掉 `NOW.md` 的「手停在哪一步」。

2026-07-31 实测过换窗口到底丢什么（拿工作区的 `.promote-manifest.json` 跟远端 `git ls-files` 对）：

| 换窗口后 | 结果 |
|---|---|
| 工作区里的 .md / .py / .json / .zip / .wav / 图片 | 留下 |
| 工作区根 `AGENTS.md` | 留下，且**每个新会话自动读**（用户看到的"好像有记忆"就是它） |
| `.bat` / `.sh` | **丢**：`启动.bat`、`停止.bat`、`build.bat`、`tests/runall.sh` 四个全没 |
| `.git/` | **丢**，所以工作区里的 `claude/` 是死快照，不是仓库 |
| `outputs/` | **整个清空**（上一轮交付的 zip 就这么没的） |
| `/tmp` | 临时盘，别把唯一副本放那儿 |
| 聊天记录 | 一句不过来 |

- 因此**别在工作区那份 `claude/` 里改代码**（没有 .git，还缺 4 个文件）——
  `git clone https://github.com/tqzbceb/claude.git /tmp/dcw` 干活，做完推上去。
- 交付 zip **同时**放 `outputs/`（用户能下载）和 `release/`（跟 git 走）。只放 outputs 换窗口就没了。
- 手动起服务做冒烟测试要用
  `DCWATCH_DB=/tmp/smoke.db setsid nohup python3 server.py >/tmp/smoke.log 2>&1 < /dev/null &`；
  不加 `setsid`，bash 工具一超时就把服务连带杀掉，看着像端口不通。
- **跑回归前确认 8777 没被占**（`python3 tests/kill.py server.py`）。被占着的话新代码根本没被测到。
- 回归**一次两三套**，`e2e_wiz.py` 单套约 40 秒，别跟别的挤一批（工具 120 秒超时）。

## 5.5 你现在就欠一件事：给这个新工作区留个指针

用户是靠**复制一段提示词**把你叫来的。那段话只在他这一条消息里 —— 他开**下一个会话**时，
新的你什么都不知道，又得让他去翻。**别让他翻第二次。**

所以读完交接后立刻做（一次，30 秒）：把下面这段追加到**工作区根目录**的 `AGENTS.md` 末尾
（`./AGENTS.md`，不是 `claude/AGENTS.md`；根目录那份会被每个新会话自动读取）：

```markdown
## dcwatch（用户的主项目）
- 代码和全部记忆在 `./claude/`，远端 https://github.com/tqzbceb/claude
- 接手先按顺序读 claude/START_HERE.md → AGENTS.md → HANDOFF.md → CLAUDE.md
- 目录空了或没有 .git 就重新 clone：`git clone https://github.com/tqzbceb/claude.git ./claude`
- 用户：Windows 11，不写代码不用命令行。任务做完直接提交推送，不要停下来问他
- 推送要 GitHub PAT，仓库里没有也不该有 —— 直接跟他要
```

有了这几行，这个账号往后所有会话都自己接得上，提示词只需要用一次。

## 6. 换号必发的开场消息（用户每个新账号第一条消息用这个）

用户在**多个账号**之间轮换，账号之间工作区不互通——换了账号，工作区文件、根 AGENTS.md
指针全没了，**这个仓库是唯一过得去的东西**。所以跨账号接手永远靠下面这段消息，
这不是冗余，是设计。给用户的最新模板（比旧版短，且 PAT 直接带上，省他的消息额度）：

```
接手进行中的项目 dcwatch，所有上下文在这个仓库里，聊天记录不用管：
https://github.com/tqzbceb/claude

第一步照做，别问我：
git clone https://github.com/tqzbceb/claude.git ./claude
然后按顺序读 claude/NOW.md → START_HERE.md → AGENTS.md，照里面写的做。
NOW.md 告诉你手停在哪、下一件做什么。读完照 START_HERE 第 5.5 节把项目指针
写进工作区根目录的 AGENTS.md。

每做完一步立刻 commit + push，我的额度随时掐断窗口，没推送的进度＝丢了。
每次推送顺手把 NOW.md 改成真实进度。
我是 Windows 11 用户，不写代码不用命令行。做完直接推送，不要停下来问我。

推送用这个 PAT（只授权了这一个仓库）：<在这里贴 PAT>
```

**同账号的后续会话不用发这段**——根 AGENTS.md 指针在，发一句「继续」就够。
**为什么必须有第 5.5 节那一步**：换号丢的不是代码（代码在远端），是「该去哪读」这一个指针。
提示词负责第一次，根目录 `AGENTS.md` 负责同账号往后每一次。
**PAT 建议**（跟用户提过：fine-grained、只授权 claude 仓库、Contents 读写、≤90 天有效期）：
这样贴在消息里也只是小面积风险，过期自动作废；到期他去 GitHub 重新生成一个换进模板即可。

这就是全部交接内容。**没有任何东西只存在于聊天记录里** —— 如果你发现有，那是上一轮的我失职，
请立刻把它补进 `HANDOFF.md` 或这一页。
