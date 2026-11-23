#!/usr/bin/env python3
"""Collect consecutive workload checkpoints, three fresh runs per case."""
import json
import os
from pathlib import Path
import re
import subprocess as sp
import tempfile

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)


def sample(cmd):
    output = sp.check_output(cmd, text=True, stderr=sp.DEVNULL, timeout=10)
    return [json.loads(line) for line in output.splitlines()]


def memory_case(mode, options, file):
    cmd = ["./memory_work", mode]
    if mode == "file":
        fd = os.open(file, os.O_RDONLY)
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)
        cmd.extend([file, options])
    result = []
    with sp.Popen(cmd, stdin=sp.PIPE, stdout=sp.PIPE, text=True) as p:
        try:
            for line in p.stdout:
                if not line.startswith("stage="):
                    result.append({"workload": line.strip()})
                    continue
                fields = dict(re.findall(r"(\w+)=(\S+)", line))
                row = {"checkpoint": fields, "observer": {}}
                for role in ("pid", "child"):
                    pid = fields[role]
                    if int(pid):
                        observer = ["./faultwatch", pid]
                        if int(fields["bytes"]) and fields["stage"] != "unmapped":
                            observer.extend(["--range", fields["range"], "--pfn"])
                        row["observer"][role] = sample(observer)[0]
                if mode == "partial":
                    row["allocator"] = sample(["./kmemwatch"])
                result.append(row)
                p.stdin.write("\n")
                p.stdin.flush()
        finally:
            if p.stdout and p.returncode is None:
                p.stdin.close()
            if p.wait(timeout=8):
                raise RuntimeError(cmd)
    return result


def activity(mode, directory):
    rows = {"before": sample(["./kmemwatch", "--top", "10"])}
    if mode == "process":
        children = []
        try:
            for _ in range(100):
                children.append(sp.Popen(["sleep", "25"]))
            rows["count"] = len(children)
            rows["during"] = sample(["./kmemwatch", "--top", "10", "--slab", "task_struct"])
        finally:
            for p in children:
                p.terminate()
            for p in children:
                p.wait()
    else:
        with tempfile.TemporaryDirectory(dir=directory) as tmp:
            folder = Path(tmp)
            for i in range(10000):
                (folder / str(i)).touch()
            for i in range(10000):
                (folder / str(i)).stat()
            rows["count"] = 10000
            rows["during"] = sample(["./kmemwatch", "--top", "10", "--slab", "dentry"])
    rows["after"] = sample(["./kmemwatch", "--top", "10"])
    return rows


with tempfile.TemporaryDirectory(dir="/var/tmp") as tmp:
    file = Path(tmp) / "written.bin"
    with file.open("wb") as f:
        for _ in range(64):
            f.write(os.urandom(1 << 20))
        f.flush()
        os.fsync(f.fileno())
    for mode, option in (("demand", ""), ("readwrite", ""), ("cow", ""), ("dense", ""),
                         ("sparse", ""), ("partial", ""), ("file", "default"), ("file", "random"),
                         ("process", ""), ("metadata", "")):
        name = "-".join(x for x in (mode, option) if x)
        for i in range(1, 4):
            result = activity(mode, tmp) if mode in ("process", "metadata") else memory_case(mode, option, file)
            Path("evidence", f"{name}-{i}.json").write_text(json.dumps(result, indent=2))
