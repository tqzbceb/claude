"""按 cmdline 关键词杀进程，跳过自己和自己的父 shell。"""
import os, sys, signal

pat = sys.argv[1]
me, parent = os.getpid(), os.getppid()
for d in os.listdir("/proc"):
    if not d.isdigit() or int(d) in (me, parent):
        continue
    try:
        cmd = open(f"/proc/{d}/cmdline").read().replace("\0", " ")
    except OSError:
        continue
    if pat in cmd and "kill.py" not in cmd:
        try:
            os.kill(int(d), signal.SIGTERM)
            print("killed", d, cmd.strip()[:70])
        except OSError:
            pass
