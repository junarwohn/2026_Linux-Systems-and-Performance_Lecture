"""Demonstrate the ranking and inheritance changes for local scoped workloads."""
import os
from pathlib import Path
import subprocess as sp

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)


def score(pid):
    proc = Path("/proc", str(pid))
    return [(proc/file).read_text().strip() for file in ("oom_score", "oom_score_adj")]


def change(pid, adj):
    try:
        Path("/proc", str(pid), "oom_score_adj").write_text(str(adj))
        return "ok"
    except OSError as exc:
        return str(exc)


workers = []
try:
    for size in (4, 12, 24):
        p = sp.Popen(["./memhog", "anon", f"{size}MiB", "--hold", "8"],
                     stdout=sp.PIPE, text=True)
        workers.append(p)
        while line := p.stdout.readline():
            if "phase=fill bytes=" in line:
                break
        print("before", size, p.pid, score(p.pid))
    p = workers[0]
    floor = int(score(p.pid)[1])
    for adj in (500, floor, 0, -1, -1000):
        print("adj-request", p.pid, adj, change(p.pid, adj), "readback", score(p.pid))
finally:
    for p in workers:
        p.terminate()
    for p in workers:
        print("exit", p.pid, p.wait())
own = os.getpid()
print("parent", own, score(own))
print("raise-parent", change(own, 500), score(own), flush=True)
pid = os.fork()
if not pid:
    me = os.getpid()
    print("after-fork", me, score(me), flush=True)
    os.execv("/bin/cat", ["cat", "/proc/self/oom_score_adj"])
print("child waited", os.waitpid(pid, 0))
