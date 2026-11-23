"""Verify registry membership and excluded roles without sending signals."""
import os
from pathlib import Path
import re
import subprocess as sp
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
rel = Path("/proc/self/cgroup").read_text().strip().split("::",1)[1]
if not rel.endswith("/observer") or not Path(rel).parent.name.startswith("run-"):
    raise SystemExit("Use a transient user service with DelegateSubgroup=observer")
parent = Path("/sys/fs/cgroup",rel.lstrip("/")).parent
if (parent/"memory.max").read_text().strip() != str(384<<20):
    raise SystemExit("Expected parent MemoryMax=384M")
(parent/"cgroup.subtree_control").write_text("+memory")
target = parent/"lab10-dry-run"
target.mkdir()
(target/"memory.max").write_text(str(64<<20))
(target/"memory.swap.max").write_text("0")

def enter():
    (target/"cgroup.procs").write_text(str(os.getpid()))

def row(proc,role):
    status=Path("/proc",str(proc.pid),"stat").read_text()
    start=status[status.rindex(")")+1:].split()[19]
    return f"{proc.pid} {start} {role} {target}\n"

processes = []
try:
    for size in ("3MiB","8MiB"):
        processes.append(sp.Popen(["./memhog","anon",size,"--adj","900","--hold","6"],
                                  cwd=ROOT,stdout=sp.DEVNULL,preexec_fn=enter))
    processes.append(sp.Popen(["./memhog","anon","16MiB","--adj","900","--hold","6"],
                              cwd=ROOT,stdout=sp.DEVNULL))
    time.sleep(.25)
    inside, excluded, outside = processes
    print(f"candidate={inside.pid} observe={excluded.pid} outside={outside.pid}", flush=True)
    with tempfile.TemporaryDirectory(prefix="lab10-dry-run-") as tmp:
        registry=Path(tmp,"intended.tsv")
        registry.touch(mode=0o600)
        registry.write_text(row(inside,"candidate")+row(excluded,"observe")+row(outside,"candidate"))
        result=sp.check_output(["build/earlyoom/earlyoom","--lab","--dryrun","--registry",registry,
                                "--mem-percent","99","--swap-percent","100","--seconds",".5",
                                "--levels","900,700,200,0"],cwd=ROOT,text=True)
        print(result,end="")
        assert re.findall(r"event=select .*?pid=(\d+)",result) == [str(inside.pid)]
        assert "event=signal " not in result and all(p.poll() is None for p in processes)
finally:
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()
        proc.wait()
    target.rmdir()
