#!/bin/bash
# 跑服务端回归。用法：./runall.sh e2e.py e2e_ai.py
# 一次别喂太多套 —— 调用方的命令超时通常是 120s，两三套一批。
# 程序目录默认取「本脚本所在目录的上一级」（仓库里 tests/ 就在项目根下面）。
# 不是这个布局就显式指定：DC=/path/to/dcwatch ./runall.sh e2e.py
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DC="${DC:-$(dirname "$HERE")}"
[ -f "$DC/server.py" ] || { echo "找不到 $DC/server.py，用 DC=<dcwatch 目录> 指定"; exit 1; }
WORK=/tmp/bcode                       # 假服务和 echo.jsonl 都在这儿，路径写死在测试里
mkdir -p "$WORK"; cp "$HERE"/*.py "$WORK"/ 2>/dev/null
cd "$WORK"
python3 kill.py mockllm >/dev/null 2>&1
python3 kill.py echo.py >/dev/null 2>&1
(setsid nohup python3 mockllm.py >/tmp/mockllm.log 2>&1 &)   # 假 OpenAI :8899
(setsid nohup python3 echo.py    >/tmp/echo.log    2>&1 &)   # 假 webhook :8898
sleep 1
for s in "$@"; do
  python3 kill.py server.py >/dev/null 2>&1
  sleep 1
  rm -f /tmp/p.db*                    # 库必须干净，去重守卫会静默丢重复 msg_id
  (cd "$DC" && setsid env DCWATCH_DB=/tmp/p.db nohup python3 server.py >/tmp/pkg.log 2>&1 &)
  sleep 3
  echo "===== $s"
  python3 -u "$s" 2>&1 | tail -6
done
python3 kill.py server.py >/dev/null 2>&1
