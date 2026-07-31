# 从这里开始（给刚接手的 AI）

你被换到了一个新账号 / 新窗口 / 新工作区。**聊天记录一句都过不来，也不需要。**
这个仓库就是全部记忆。花 5 分钟读完这一页，你会跟上一轮的我知道得一样多。

---

## 0. 一句话：这是什么

**dcwatch** —— 盯 Discord 指定频道的消息，按规则筛，命中了本机弹窗 + 转发到微信 / Telegram /
Webhook，可选让大模型判分打标签。单进程 aiohttp + SQLite + 单文件网页界面 + 一个 MV3 浏览器扩展。
当前版本 **v1.9.4**（程序和扩展这一版对齐，都是 1.9.4；A2 改过扩展所以用户必须重装扩展）。

用户是**一个人在用的普通 Windows 11 用户，不写代码、不用命令行**。他要的是双击就能跑的东西。

## 1. 立刻做这四件事（照做，别重新规划）

1. **别问「上一轮聊了什么」。** 问了也没人答得出，而且答案全在这个仓库里。
2. **确认环境是好的**（一次就够）：

   ```bash
   pip install aiohttp                              # 唯一依赖，新环境每次都要装
   cd tests && ./runall.sh e2e.py e2e_imp.py        # 应为 46 / 74 全绿
   ```

   不绿 = 环境问题，不是代码问题（这两套在干净 clone 上反复跑过）。
3. **先读 `NOW.md`** —— 上一个窗口手停在哪一步（粒度到"步"，是聊天记录的替代品）。
   它说"没有进行中的任务"才去 `HANDOFF.md` 挑新活。然后**读这三份，按顺序**：`AGENTS.md`（项目怎么运作、用户是谁、**十一条硬规矩**、排查
   「它没提醒我」的固定顺序）→ `HANDOFF.md`（上一轮做完了什么、下一步做什么、踩过的坑）→
   `CLAUDE.md`（用户定的工作协议：会话开始/结束各做什么）。`tests/RUN.md` 是回归的说明书。
4. **v1.9.4 已出包（2026-07-31），欠用户真机验收**：程序 zip + 扩展 zip 都在 `release/`。
   等用户装上后验三条（A2 回复消息的盯人命中、A3 两个标签页只弹一条、
   A5 关三个开关立刻全停），装扩展必须三步：覆盖文件 → ⟳ → Discord 页 F5。
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

## 3. 凭据（每次换号都要重新要）

**推送凭据不在仓库里，也不该在。** 老工作区的私有目录里那份 PAT 随账号一起失联了。
所以：**直接跟用户要一个新的 GitHub PAT**（他给过三次，知道怎么给），然后一次性 URL 推：

```bash
git push "https://x-access-token:<PAT>@github.com/tqzbceb/claude.git" HEAD:main
sed 's/github_pat_[A-Za-z0-9_]*/***TOKEN***/g'      # 任何会打印 URL 的输出都过一遍
git remote set-url origin https://github.com/tqzbceb/claude.git   # 推完把 URL 复位
grep -rl github_pat_ .git/                          # 必须没有输出
```

推之前先 `git ls-remote` 看远端到哪了，别信记忆里的 sha。

## 4. 给用户交付东西怎么给

他要的是一个 zip，解压后双击 `启动.bat`。**交付包里留 `README.md`（那是给他看的说明书），
去掉 `tests/`、`release/`、给你看的三份 .md 和两个点文件**，干净 **26 个文件**：

```bash
V=$(grep -oP 'VERSION = "\K[^"]+' server.py)        # 版本号只有一个来源：server.py
rm -rf __pycache__ tests/__pycache__
rm -rf /tmp/pack && mkdir -p /tmp/pack/dcwatch && cp -r ./. /tmp/pack/dcwatch/
cd /tmp/pack/dcwatch && rm -rf .git tests release .gitignore .gitattributes __pycache__ \
    AGENTS.md CLAUDE.md HANDOFF.md START_HERE.md NOW.md agent   # README.md 要留下
find . -type f | wc -l                              # 必须是 26
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

## 6. 如果哪天又要换号：把下面这段原样发给新窗口

```
接手一个进行中的项目。所有上下文都在这个 GitHub 仓库里，聊天记录不用管：
https://github.com/tqzbceb/claude

第一步照做，别问我：
git clone https://github.com/tqzbceb/claude.git ./claude
然后按顺序读 claude/NOW.md → START_HERE.md → AGENTS.md → HANDOFF.md → CLAUDE.md，照里面写的做。
NOW.md 会告诉你手停在哪一步、下一件做什么。读完照 START_HERE 第 5.5 节，
把项目指针写进工作区根目录的 AGENTS.md，这样我下次开新会话不用再发这段话。

最重要的一条：**每做完一步就立刻 commit + push 一次**，不要攒到最后。
我的账号额度随时会掐断窗口，没推送的进度＝丢了。推送多花点额度没关系，我号多。
每次推送顺手把 claude/NOW.md 的「手停在哪一步」改成真实进度再推。

我是 Windows 11 用户，不写代码不用命令行。做完直接提交推送，不要停下来问我。
要推送凭据时跟我要 GitHub PAT（我手上有一个能用的，你提我就贴）。
```

**为什么必须有第 5.5 节那一步**：换号丢的不是代码（代码在远端），是「该去哪读」这一个指针。
提示词负责第一次，根目录 `AGENTS.md` 负责往后每一次。少了后者，用户每开一个会话都得当一次搬运工。

这就是全部交接内容。**没有任何东西只存在于聊天记录里** —— 如果你发现有，那是上一轮的我失职，
请立刻把它补进 `HANDOFF.md` 或这一页。
