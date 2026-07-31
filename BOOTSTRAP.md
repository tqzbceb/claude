# BOOTSTRAP —— 新账号 / 全新工作区的自举指南

> 你是一个全新工作区里的 agent，对这个项目一无所知。用户换了账号，聊天记录、
> 工作区文件、AGENTS.md 备忘全部没带过来。**这个仓库就是全部记忆。**
> 按这一页做完，你就等于上一个号的我。

## 第 0 步：拿到推送权限（唯一需要用户配合的事）

推送用 GitHub PAT。用户会在第一条消息里上传一个 `github_pat_tqzbceb.txt`
（或直接粘贴 token，形如 `github_pat_...`）。拿到后立刻存好：

```bash
mkdir -p .bcode/agent-workspace/secrets
# 把 token 写进（上传的话从 uploads/ 拷过去）：
cp uploads/github_pat_tqzbceb.txt .bcode/agent-workspace/secrets/github_pat_tqzbceb.txt
```

推送用法：
```bash
git push "https://x-access-token:$(cat .bcode/agent-workspace/secrets/github_pat_tqzbceb.txt)@github.com/tqzbceb/claude.git" HEAD:main
```
任何会打印 URL 的输出都过一遍 `sed 's/github_pat_[A-Za-z0-9_]*/***TOKEN***/g'`。
如果用户没给 token：读操作照常（clone 是公开的），要推送时再跟他要一次。

## 第 1 步：铺好工作区

```bash
git clone https://github.com/tqzbceb/claude.git ./claude   # 工作区快照（给用户看/找文件用）
git clone https://github.com/tqzbceb/claude.git /tmp/dcw   # 真正干活的仓库
mkdir -p .bcode/agent-workspace
cp /tmp/dcw/agent/save.py .bcode/agent-workspace/save.py   # 存盘脚本
```

## 第 2 步：恢复记忆 —— 把下面整段追加到工作区根目录的 `AGENTS.md` 末尾

（新会话会自动读根 AGENTS.md，这就是跨窗口的长期记忆。原样抄，别改。）

```markdown
## dcwatch（用户的主项目）
- **先读 `claude/NOW.md`** —— 上一个窗口手停在哪一步。它是聊天记录的替代品，读它别问用户
- 代码和全部记忆在 `./claude/`，远端 https://github.com/tqzbceb/claude
- 接手先按顺序读 claude/NOW.md → START_HERE.md → AGENTS.md → HANDOFF.md → CLAUDE.md
- 目录空了或没有 .git 就重新 clone：`git clone https://github.com/tqzbceb/claude.git ./claude`
- 用户：Windows 11，不写代码不用命令行。任务做完直接提交推送，不要停下来问他
- 推送用 GitHub PAT，已存在工作区私有目录（不在仓库里）：
  `.bcode/agent-workspace/secrets/github_pat_tqzbceb.txt`
  用法：`git push "https://x-access-token:$(cat .bcode/agent-workspace/secrets/github_pat_tqzbceb.txt)@github.com/tqzbceb/claude.git" HEAD:main`
  任何会打印 URL 的输出都过一遍 `sed 's/github_pat_[A-Za-z0-9_]*/***TOKEN***/g'`；
  推完 `grep -rl github_pat_ claude/.git/` 必须无输出。文件没了或 401 才跟用户要新的

## 这个账号是「10x10」：每窗口 10 条消息，用完换窗口（换窗口＝换会话）
实测换窗口会丢什么：
- **留下**：工作区里的 .md / .py / .json / .zip / .wav / 图片，以及根 `AGENTS.md`（每个新会话自动读）
- **丢掉**：`.bat` 和 `.sh`、`.git/`（所以 `./claude/` 是死快照不是仓库）、
  **`outputs/` 整个清空**、`/tmp`、聊天记录
所以：
- 干活永远 `git clone https://github.com/tqzbceb/claude.git /tmp/dcw`，别在 `./claude/` 里改代码
- **每做完一步就存盘**，别攒到"会话结束"——会话是被额度掐断的，不会给你收尾的机会：
  `python3 .bcode/agent-workspace/save.py "做了什么"`（commit + 推送 + 把 .md 同步回快照）
- 存盘时顺手把 `claude/NOW.md` 的「手停在哪一步」改掉，这是下个窗口唯一的接力棒
- 交付 zip **同时**放 `outputs/`（用户下载）和 `release/`（跟 git 走）；只放 outputs 换窗口就没了

## 窗口工作协议（用户定的，2026-07-31，严格执行）
额度只够一个窗口做一个小任务。每个窗口固定四步：
1. 用户消息有新需求 → **动任何代码之前**先写进 `BACKLOG.md` 并推送。
   窗口随时可能被额度掐断，没推送的需求＝丢了，用户说「继续」下个窗口就不知道继续什么
2. 挑定这个窗口的**一个**小任务后，先把`NOW.md`「状态」改成「正在做 XX，步骤：…」并推送，再动手
3. 干活中每完成一步就 save 一次（save.py 会顺手推送），`NOW.md`始终反映真实进度
4. 做完把`NOW.md`改成「做完了 XX，下一件是 XX」并推送
5. 回复末尾报：✅ 本窗口完成 XX，已推送；下窗口发「继续」即可
用户每窗口只会发「继续」或新需求，不会再发长提示词。缺 PAT 时读操作照常，推送前再要
```

## 第 3 步：按老规矩接活

按顺序读：`claude/NOW.md`（手停在哪一步）→ `START_HERE.md` → `AGENTS.md`（项目规矩）
→ `HANDOFF.md`（挑活）→ `CLAUDE.md`。读完直接开工，不要再问用户"我该做什么"。

## 项目一句话

dcwatch：Windows 本地 Discord 频道监控工具（server.py + ui.html + 浏览器扩展），
用户不写代码，交付物是 `release/dcwatch-vX.Y.Z.zip`，回归测试在 `tests/`。
当前版本 v1.9.3，496 条回归全绿，没有半截活。

---
*这页由上一个账号的 agent 在 2026-07-31 额度耗尽前写下。新号的你，接好了。*
