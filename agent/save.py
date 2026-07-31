#!/usr/bin/env python3
"""存盘：把 /tmp 上的工作副本提交推送，并把交接文档同步回工作区快照。

为什么存在：用户的账号是「10x10」——每个窗口 10 条消息，用完就得换窗口，
换窗口＝换会话＝聊天记录一句都不过来。所以**每做完一步就得存盘**，
不能攒到会话结束（会话是被额度掐断的，不是自己结束的）。

为什么是 .py 不是 .sh：工作区做快照（promote）时 **.sh 和 .bat 会被过滤掉**，
下个窗口就没这个文件了。.py 会留下。

两份拷贝，内容一样，跑哪份都行：
    工作区私有目录 `.bcode/agent-workspace/save.py`（顺手，但换 project / 换账号会没）
    仓库里 `agent/save.py`（跟着 git 走，工作区整个丢了也能 clone 回来；不进交付 zip）

用法（在工作区根目录跑）：
    python3 .bcode/agent-workspace/save.py "提交说明"
    python3 /tmp/dcw/agent/save.py "提交说明" --repo /tmp/dcw
    python3 .bcode/agent-workspace/save.py --check      # 只看状态，不提交

做四件事：
    1. 在工作副本里 git add -A && commit（没改动就跳过）
    2. 用工作区私有目录里的 PAT 推到 GitHub（token 永远不落地到 .git/config）
    3. 把 *.md 同步回工作区的 ./claude/ —— 那份是死快照，但它会被 promote，
       所以哪怕 GitHub 也拿不到了，下个窗口至少还能读到交接文档
    4. 打印一行「远端到哪了」，方便下个窗口核对
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REMOTE = "github.com/tqzbceb/claude.git"
PAT_REL = ".bcode/agent-workspace/secrets/github_pat_tqzbceb.txt"


def find_ws() -> Path:
    """找工作区根目录：认的是「有没有那份 PAT」，不是自己在哪一层。

    这个文件有两份拷贝（工作区私有目录一份、仓库 agent/ 一份），层数不一样，
    所以不能按 __file__ 往上数几级 —— 那样换个地方跑就找错人。
    """
    here = Path(__file__).resolve()
    for c in [Path.cwd(), *Path.cwd().parents, *here.parents]:
        if (c / PAT_REL).exists():
            return c
    return Path.cwd()


WS = find_ws()
PAT_FILE = WS / PAT_REL
SNAPSHOT = WS / "claude"                            # 会被 promote 的那份死快照


def mask(s: str) -> str:
    """任何可能带 token 的输出都过这里。绝不能把 PAT 打进聊天记录。"""
    return re.sub(r"github_pat_[A-Za-z0-9_]+", "***TOKEN***", s)


def run(args, cwd, check=True):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    out = mask((p.stdout + p.stderr).strip())
    if check and p.returncode:
        print(out)
        sys.exit(f"× 失败：{mask(' '.join(args))}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("message", nargs="?", help="commit message")
    ap.add_argument("--repo", default="/tmp/dcw", help="工作副本（git clone 出来的那份）")
    ap.add_argument("--check", action="store_true", help="只看状态")
    a = ap.parse_args()

    repo = Path(a.repo)
    if not (repo / ".git").is_dir():
        sys.exit(f"× {repo} 不是 git 仓库。先 clone：\n"
                 f"  git clone https://{REMOTE} {repo}")

    dirty = run(["git", "status", "--porcelain"], repo)
    print("本地改动：\n" + (dirty or "（没有）"))
    if a.check:
        print("\n远端：" + run(["git", "ls-remote", f"https://{REMOTE}", "HEAD"], repo))
        return

    if not a.message:
        sys.exit("× 要一句提交说明：python3 .bcode/agent-workspace/save.py \"做了什么\"")

    if dirty:
        run(["git", "add", "-A"], repo)
        run(["git", "-c", "user.name=dcwatch-agent",
             "-c", "user.email=agent@dcwatch.local",
             "commit", "-m", a.message], repo)
        print("✓ 已提交")
    else:
        print("· 没有新改动，跳过 commit")

    if not PAT_FILE.exists():
        sys.exit(f"× 没有 PAT（{PAT_FILE}）。跟用户要一个新的 GitHub PAT。")
    pat = PAT_FILE.read_text().strip()
    # 一次性 URL 推：token 只活在这一条命令的参数里，不写进 .git/config
    print(run(["git", "push", f"https://x-access-token:{pat}@{REMOTE}", "HEAD:main"], repo))
    leak = run(["grep", "-rl", "github_pat_", str(repo / ".git")], repo, check=False)
    print("✓ 已推送" + ("　⚠ .git 里有 token 残留：" + leak if leak else "（.git 里无 token 残留）"))

    # 交接文档同步回工作区快照：GitHub 是主存，这份是「连 GitHub 都拿不到」时的兜底
    if SNAPSHOT.is_dir():
        n = 0
        for md in list(repo.glob("*.md")) + list((repo / "tests").glob("*.md")):
            dst = SNAPSHOT / md.relative_to(repo)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or dst.read_bytes() != md.read_bytes():
                shutil.copy2(md, dst)
                n += 1
        print(f"✓ 同步 {n} 份 .md 回工作区快照 ./claude/")

    print("远端现在：" + run(["git", "ls-remote", f"https://{REMOTE}", "HEAD"], repo))


if __name__ == "__main__":
    main()
