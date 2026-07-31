"""找出占着端口的那个进程，并把它停掉。给 停止.bat / 启动.bat 用。

为什么要有这个文件：用户关窗口的方式五花八门（点 X、强制关机、任务栏右键关闭），
偶尔会留下一个还占着 8777 的僵尸进程。这时候再双击启动，新进程起不来，
界面也打不开 —— 从用户角度看就是「程序坏了」。这里负责把这种情况自动收拾掉。

用法（退出码是给 .bat 判断的）：
  python freeport.py check   [端口]   看看端口上是什么   0=空闲 2=活着的dcwatch 1=被别人占着
  python freeport.py stop    [端口]   停掉它            0=已释放 1=没停掉
  python freeport.py ensure  [端口]   启动前清路        0=可以启动 2=已有活的dcwatch 1=被别人占着别动
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

# 只有这些名字的进程才允许自动杀 —— 别人的程序占了 8777 是他的自由，不能替他做主
OURS = {"python.exe", "pythonw.exe", "dcwatch.exe", "python", "python3", "python3.exe"}
DEFAULT_PORT = 8777


def answers(port, timeout=2.0):
    """端口上是不是一个还活着的 dcwatch？是就返回版本号。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/state" % port, timeout=timeout) as r:
            return ((json.loads(r.read().decode()) or {}).get("env") or {}).get("ver") or "?"
    except Exception:
        return None


def busy(port):
    s = socket.socket()
    s.settimeout(0.6)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def pids_on(port):
    """谁在监听这个端口。Windows 走 netstat，其它系统走 ss（方便在别的机器上自测）。"""
    out = []
    if os.name == "nt":
        try:
            txt = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                                 text=True, timeout=20, errors="ignore").stdout
        except Exception:
            return out
        for ln in txt.splitlines():
            f = ln.split()
            # TCP    127.0.0.1:8777    0.0.0.0:0    LISTENING    12345
            if len(f) >= 5 and f[0].upper() == "TCP" and f[1].endswith(":%d" % port) \
                    and f[3].upper().startswith("LISTEN") and f[4].isdigit() and f[4] != "0":
                out.append(int(f[4]))
    else:
        try:
            txt = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True,
                                 timeout=20, errors="ignore").stdout
        except Exception:
            txt = ""
        for ln in txt.splitlines():
            if re.search(r"[:.]%d\b" % port, ln.split()[3] if len(ln.split()) > 3 else ""):
                out += [int(x) for x in re.findall(r"pid=(\d+)", ln)]
        if not out:                      # 没装 ss 的机器：直接查 /proc
            out += _pids_via_proc(port)
    return sorted(set(out))


def _pids_via_proc(port):
    """/proc 兜底：先在 /proc/net/tcp 找监听这个端口的 socket inode，再看谁开着它。"""
    inodes = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = open(f).read().splitlines()[1:]
        except Exception:
            continue
        for ln in lines:
            c = ln.split()
            if len(c) < 10:
                continue
            try:
                if int(c[1].split(":")[1], 16) == port and c[3].upper() == "0A":   # 0A = LISTEN
                    inodes.add(c[9])
            except (ValueError, IndexError):
                continue
    if not inodes:
        return []
    found = []
    for pid in [d for d in os.listdir("/proc") if d.isdigit()]:
        try:
            for fd in os.listdir("/proc/%s/fd" % pid):
                try:
                    tgt = os.readlink("/proc/%s/fd/%s" % (pid, fd))
                except OSError:
                    continue
                if tgt.startswith("socket:[") and tgt[8:-1] in inodes:
                    found.append(int(pid))
                    break
        except OSError:
            continue
    return found


def name_of(pid):
    try:
        if os.name == "nt":
            txt = subprocess.run(["tasklist", "/fi", "PID eq %d" % pid, "/nh", "/fo", "csv"],
                                 capture_output=True, text=True, timeout=20, errors="ignore").stdout
            m = re.search(r'^"([^"]+)"', txt.strip())
            return m.group(1) if m else "?"
        return open("/proc/%d/comm" % pid).read().strip()
    except Exception:
        return "?"


def kill(pid):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/f", "/pid", str(pid)], capture_output=True, timeout=20)
        else:
            os.kill(pid, 9)
        return True
    except Exception:
        return False


def wait_free(port, secs=6.0):
    end = time.time() + secs
    while time.time() < end:
        if not busy(port):
            return True
        time.sleep(0.3)
    return not busy(port)


def graceful(port):
    """先请它自己退，这样数据库能正常收尾。"""
    try:
        urllib.request.urlopen(urllib.request.Request(
            "http://127.0.0.1:%d/api/quit" % port, data=b""), timeout=6).read()
        return True
    except Exception:
        return False


def do_stop(port, quiet=False):
    def say(s):
        if not quiet:
            print(s)

    if not busy(port):
        say("端口 %d 本来就是空的，没有东西在跑。" % port)
        return 0

    ver = answers(port)
    if ver:
        say("端口 %d 上是 dcwatch v%s，正在请它自己退出…" % (port, ver))
        graceful(port)
        if wait_free(port):
            say("已经停了，端口 %d 空出来了。" % port)
            return 0
        say("它没退干净，改用强制结束。")
    else:
        say("端口 %d 被占着，但它不响应 dcwatch 的接口 —— 多半是上次没退干净的僵尸进程。" % port)

    pids = pids_on(port)
    if not pids:
        say("奇怪：端口被占着，却找不到是哪个进程。请重启电脑，或换个端口："
            "启动.bat 8778")
        return 1

    for pid in pids:
        nm = name_of(pid)
        if nm.lower() not in OURS:
            say("占着端口 %d 的是【%s】(PID %d)，这不是 dcwatch，我不动它。" % (port, nm, pid))
            say("要么把那个程序关掉，要么给 dcwatch 换个端口：双击 启动.bat 时在后面加端口号，"
                "比如在命令行里跑  启动.bat 8778")
            return 1
        say("强制结束 %s (PID %d) …" % (nm, pid))
        kill(pid)

    if wait_free(port):
        say("端口 %d 已经空出来了，现在可以正常启动了。" % port)
        return 0
    say("还是没能释放端口 %d。重启一下电脑最快。" % port)
    return 1


def main():
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "check").lower()
    try:
        port = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].strip() else DEFAULT_PORT
    except ValueError:
        port = DEFAULT_PORT

    if cmd == "check":
        ver = answers(port)
        if ver:
            print("端口 %d：dcwatch v%s 正在跑。" % (port, ver))
            return 2
        if busy(port):
            pids = pids_on(port)
            who = "、".join("%s(PID %d)" % (name_of(p), p) for p in pids) or "查不出是谁"
            print("端口 %d：被占着，%s，但它不是活着的 dcwatch。" % (port, who))
            return 1
        print("端口 %d：空闲。" % port)
        return 0

    if cmd == "stop":
        return do_stop(port)

    if cmd == "ensure":
        # 启动前清路：活的 dcwatch 就别重复启动，僵尸就清掉，别人的程序就别碰
        if answers(port):
            return 2
        if not busy(port):
            return 0
        return 0 if do_stop(port) == 0 else 1

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
