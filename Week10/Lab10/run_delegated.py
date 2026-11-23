"""Reproduce Lab 10 inside a transient systemd user unit."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess as sp
import time

ROOT = Path(__file__).resolve().parent
MIB = 1 << 20
FIELDS = ("memory.current", "memory.peak", "memory.events", "memory.stat",
          "memory.pressure", "memory.high", "memory.max", "memory.swap.max")
OUT = ROOT / "evidence"


def collect(groups, processes):
    result = {"t": time.monotonic(), "groups": {}, "processes": {}}
    for group in groups:
        result["groups"][group.name] = {key: (group / key).read_text() for key in FIELDS}
    for process in processes:
        proc = Path("/proc", str(process.pid))
        try:
            result["processes"][process.pid] = {
                key: (proc / key).read_text() for key in ("smaps_rollup", "maps")}
        except OSError as exc:
            result["processes"][process.pid] = {"status": str(exc)}
    for file in ("/proc/pressure/memory", "/proc/meminfo"):
        result[file] = Path(file).read_text()
    return result


def create(parent, name, maximum="max"):
    group = parent / name
    group.mkdir()
    (group / "memory.max").write_text(str(maximum))
    (group / "memory.swap.max").write_text("0")
    return group


def checkpoint_run(parent, name, argv, limit, tracer=None):
    group = create(parent, name, limit)
    rows = [collect([group], [])]
    with (OUT / f"{name}.txt").open("w") as out:
        cmd = list(argv) + [str(group)]
        environment = os.environ.copy()
        if tracer:
            environment["LD_PRELOAD"] = str(tracer)
        proc = sp.Popen(cmd, cwd=ROOT, text=True, stdout=sp.PIPE, stderr=sp.STDOUT,
                        stdin=sp.PIPE, env=environment)
        try:
            for line in proc.stdout:
                out.write(line)
                match = re.match(r"STAGE (\d+) (\S+)", line)
                if match:
                    row = collect([group], [proc])
                    row["stage"] = match[2]
                    rows.append(row)
                if line.startswith("WAITING:"):
                    if tracer:
                        proc.send_signal(signal.SIGUSR1)
                    proc.stdin.write("\n")
                    proc.stdin.flush()
            rc = proc.wait(timeout=5)
            rows.append({"returncode": rc, **collect([group], [])})
            expected = -9 if name == "B-96MiB" else 0
            if rc != expected:
                raise RuntimeError(f"{name}: status {rc}, expected {expected}")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            (OUT / f"{name}.json").write_text(json.dumps(rows, indent=2))
            group.rmdir()


def services(parent):
    group = create(parent, "C", 192 * MIB)
    (group / "cgroup.subtree_control").write_text("+memory")
    existing, new = create(group, "existing"), create(group, "new")
    paths = [group, existing, new]
    rows = []
    workers = []
    try:
        def start(role, target):
            stream = (OUT / f"C-{role}.txt").open("w")
            try:
                worker = sp.Popen([ROOT / "lab10_c", role, target], stdout=stream, stderr=sp.STDOUT)
                workers.append(worker)
            finally:
                stream.close()
        def observe(phase, seconds):
            for _ in range(seconds):
                time.sleep(1)
                rows.append({"phase": phase, **collect(paths, workers)})
        start("existing", existing)
        observe("existing-only", 5)
        start("new", new)
        observe("both", 8)
        (new / "memory.high").write_text(str(48 * MIB))
        observe("new-high-48MiB", 10)
    finally:
        for worker in workers:
            worker.terminate()
        for worker in workers:
            worker.wait(timeout=5)
        rows.append({"returncodes": [p.returncode for p in workers], **collect(paths, [])})
        (OUT / "C.json").write_text(json.dumps(rows, indent=2))
        for target in (existing, new, group):
            target.rmdir()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracer", type=Path)
    parser.add_argument("--only-a", action="store_true")
    args = parser.parse_args()
    cg = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    if not cg.endswith("/observer") or not Path(cg).parent.name.startswith("run-"):
        parser.error("Use a transient systemd user unit with DelegateSubgroup=observer.")
    parent = Path("/sys/fs/cgroup", cg.lstrip("/")).parent
    if (parent / "memory.max").read_text().strip() != str(384 * MIB):
        parser.error("Expected private parent MemoryMax=384M.")
    (parent / "cgroup.subtree_control").write_text("+memory")
    OUT.mkdir(exist_ok=True)
    for mode in ("buggy", "fixed"):
        name = "A-" + mode + ("-traced" if args.tracer else "")
        checkpoint_run(parent, name, [ROOT / "lab10_a", mode], 256 * MIB, args.tracer)
    if not args.only_a:
        for size in (96, 160):
            checkpoint_run(parent, f"B-{size}MiB", [ROOT / "lab10_b"], size * MIB)
        services(parent)
        with (OUT / "dmesg-status.txt").open("w") as out:
            sp.run(["dmesg", "--level=err,warn"], stdout=out, stderr=out)


if __name__ == "__main__":
    main()
