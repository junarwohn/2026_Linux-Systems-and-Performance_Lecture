"""Collect bounded cgroup comparisons with the supplied sampler and student tools."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess as sp
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
MIB = 1 << 20


def run(parent, data, case, iteration, threshold, extended=False):
    name = f"{case}-{iteration}" if threshold == 50000 else f"{case}-trigger-{threshold}" + ("-extended" if extended else "")
    out = ROOT / "evidence" / name
    out.mkdir()
    group = parent / name
    group.mkdir()
    for key, value in {"memory.max": 256 * MIB,
                       "memory.high": (56 if case == "cache" else 224) * MIB,
                       "memory.swap.max": (128 if case == "swap" else 0) * MIB}.items():
        (group / key).write_text(str(value))
    processes, streams, commands = [], [], {}
    start = time.monotonic()

    def place():
        (group / "cgroup.procs").write_text(str(os.getpid()))
        os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})

    def launch(label, args, worker=False):
        commands[label] = list(map(str, args))
        sink = (out / (label + ".txt")).open("w")
        streams.append(sink)
        proc = sp.Popen(args, cwd=ROOT, stdout=sink, stderr=sp.STDOUT,
                        preexec_fn=place if worker else None)
        processes.append(proc)
        return proc

    def snapshot():
        return {key: (group / key).read_text() for key in ("memory.stat", "memory.events",
                "memory.pressure", "cpu.pressure", "io.pressure", "memory.current",
                "memory.swap.current", "memory.high", "memory.max", "memory.swap.max")}
    record = {"start_s": start, "scope": "delegated memory cgroup", "before": snapshot(),
              "commands": commands, "samples": []}
    try:
        probe = launch("probe", ["./memhog", "probe", "--interval", "50"])
        psi = launch("psi", ["./psimon", "--interval", "300"])
        sampler = launch("sampler", ["python3", "student_support/sample.py", out / "system.csv", "--seconds", "12" if extended else "6"])
        trigger = launch("trigger", ["./psimon", "--path", group / "memory.pressure",
                                     "--trigger", "some", str(threshold), "2000000", "--interval", "500"])
        time.sleep(.25)
        begin = time.monotonic()
        record["workload_start_s"] = begin
        if case in ("healthy", "cpu", "cache"):
            with data.open("rb") as f:
                os.posix_fadvise(f.fileno(),0,0,os.POSIX_FADV_DONTNEED)
            if case == "cpu":
                launch("busy", ["timeout", "5", "bash", "-c", "while :; do :; done"], True)
            worker = launch("work", ["./memhog", "file", data, "--loops", "24" if extended else "4", "--rate", "256" if extended else "128"], True)
        elif case == "swap":
            worker = launch("work", ["./memhog", "anon", "120MiB", "--hold", "2",
                                     "--retouch", "random", "--seed", "42"], True)
        else:
            worker = launch("work", ["./memhog", "burst", "48MiB", "--burst", "32MiB", "--period", ".25", "--cycles", "12"], True)
        prepared = False
        while worker.poll() is None:
            if time.monotonic() - begin > (16 if extended else 10):
                raise TimeoutError(case)
            if case == "swap" and not prepared and (out / "work.txt").read_text().find("phase=fill bytes=") >= 0:
                prepared = True
                try:
                    (group / "memory.reclaim").write_text("72M swappiness=200")
                except OSError as exc:
                    record["reclaim_status"] = str(exc)
                record["prepared_swap_bytes"] = int((group / "memory.swap.current").read_text())
            record["samples"].append({"t":time.monotonic(), **snapshot()})
            time.sleep(.25)
        record["workload_end_s"] = time.monotonic()
        record["after"] = snapshot()
        for p in (probe, psi, trigger):
            p.send_signal(signal.SIGTERM)
        if worker.returncode != 0 or sampler.wait(timeout=8):
            raise RuntimeError(name)
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
        for p in processes:
            try:
                p.wait(timeout=2)
            except sp.TimeoutExpired:
                p.kill()
                p.wait()
        for stream in streams:
            stream.close()
        record["returncodes"] = [p.returncode for p in processes]
        record["end_s"] = time.monotonic()
        (out / "manifest.json").write_text(json.dumps(record,indent=2))
        group.rmdir()
    print(name, record.get("prepared_swap_bytes", ""), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trigger-followup", action="store_true")
    parser.add_argument("--trigger-extended", action="store_true")
    args = parser.parse_args()
    cg = Path("/proc/self/cgroup").read_text().strip().split("::",1)[1]
    if not cg.endswith("/observer") or not Path(cg).parent.name.startswith("run-"):
        raise SystemExit("Use a transient systemd unit with DelegateSubgroup=observer")
    parent = Path("/sys/fs/cgroup",cg.lstrip("/")).parent
    if (parent/"memory.max").read_text().strip() != str(384*MIB):
        raise SystemExit("Expected parent MemoryMax=384M")
    (parent/"cgroup.subtree_control").write_text("+memory")
    with tempfile.NamedTemporaryFile(dir="/var/tmp") as f:
        for _ in range(96):
            f.write(os.urandom(MIB))
        f.flush()
        os.fsync(f.fileno())
        if args.trigger_extended:
            run(parent, Path(f.name), "cache", 1, 1000, extended=True)
        elif args.trigger_followup:
            for case in ("cache", "swap"):
                run(parent, Path(f.name), case, 1, 1000)
        else:
            for case in ("healthy","cpu","cache","swap","burst"):
                for i in range(1, 2 if case == "burst" else 4):
                    run(parent, Path(f.name), case, i, 50000)
