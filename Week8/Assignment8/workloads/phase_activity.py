"""Pace file metadata or task activity between full myslabtop intervals."""
import argparse
import json
from pathlib import Path
import signal
import subprocess as sp
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def run(mode, output, interval):
    output.mkdir()
    monitors = []
    files = []
    with (output/"phases.jsonl").open("w") as markers:
        def mark(phase, **fields):
            print(json.dumps({"phase":phase,"t_monotonic_s":time.monotonic(),**fields}),
                  file=markers,flush=True)

        def pause(seconds):
            end = time.monotonic()+seconds
            while time.monotonic() < end:
                if any(p.poll() is not None for p in monitors):
                    raise RuntimeError("myslabtop exited; see stdout/stderr for each view")
                time.sleep(min(.1,max(0,end-time.monotonic())))

        try:
            for view in ("cache","kmalloc"):
                stdout, stderr = ((output/f"{view}.{suffix}").open("w") for suffix in ("jsonl","err"))
                files.extend((stdout,stderr))
                args = [str(ROOT/"myslabtop"),"--view",view,"--interval",str(interval)]
                monitors.append(sp.Popen(args,cwd=ROOT,stdout=stdout,stderr=stderr))
                mark("collector", command=args)
            mark("pre")
            pause(7)
            mark("active")
            if mode == "idle":
                pause(10)
            elif mode == "files":
                with tempfile.TemporaryDirectory(dir="/var/tmp") as directory:
                    paths = [Path(directory,str(i)) for i in range(10000)]
                    for phase, action in (("create",Path.touch),("stat",Path.stat),("delete",Path.unlink)):
                        mark(phase, operations=len(paths), batch=1000)
                        for i in range(0,len(paths),1000):
                            begin = time.monotonic()
                            for path in paths[i:i+1000]:
                                action(path)
                            pause(max(0, .34-(time.monotonic()-begin)))
            else:
                for batch in range(5):
                    children = []
                    try:
                        mark("start",batch=batch,count=20)
                        for _ in range(20):
                            children.append(sp.Popen(["sleep","30"]))
                        pause(1.8)
                    finally:
                        mark("reap",batch=batch,count=len(children))
                        for child in children:
                            child.terminate()
                        for child in children:
                            child.wait()
                    pause(.3)
            mark("post")
            pause(7)
        finally:
            for child in monitors:
                if child.poll() is None:
                    child.send_signal(signal.SIGINT)
            for child in monitors:
                try:
                    child.wait(timeout=4)
                except sp.TimeoutExpired:
                    child.kill()
                    child.wait()
            for f in files:
                f.close()
            mark("end",returncodes=[p.returncode for p in monitors])


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("mode",choices=("idle","files","processes"))
    p.add_argument("output",type=Path)
    p.add_argument("--interval",type=float,choices=(.1,2),default=2)
    a = p.parse_args()
    run(a.mode,a.output,a.interval)
