"""Run the bounded lab workloads in a transient user service with Delegate=memory."""

import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time

from collect import snapshot, counters
from report import calculate

ROOT = Path(__file__).resolve().parents[1]
MIB = 1 << 20


def run(name, parent, directory, data):
    group = parent / name
    group.mkdir()
    processes = []
    files = []
    duration = 4
    record = {"scope": "delegated cgroup", "seconds": duration, "scenario": name}
    try:
        high = 64 if name in ("high", "memory", "mixed", "file-refault") else 160
        maximum = 96 if name == "max" else 256
        swap = 128 if name == "swap-in" else 0
        for key, value in {"memory.high": high * MIB, "memory.max": maximum * MIB,
                           "memory.swap.max": swap * MIB}.items():
            (group / key).write_text(str(value))

        def enter():
            (group / "cgroup.procs").write_text(str(os.getpid()))
            os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})

        def start(command, worker=True):
            index = len(processes)
            err = (directory / f"{name}-{index}.err").open("w")
            files.append(err)
            p = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=err, text=True, preexec_fn=enter if worker else None)
            processes.append(p)
            return p

        with data.open("rb") as f:
            os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        start_state = snapshot(group)
        samples = [start_state]
        if name in ("max", "swap-in"):
            cmds = [["anon_work", "128", str(duration), "hold" if name == "max" else "touch"]]
        else:
            cmds = [["file_reader", str(data), str(duration)]]
        for _ in range({"cpu": 2, "mixed": 1}.get(name, 0)):
            cmds.append(["cpu_busy", str(duration)])
        record["commands"] = cmds
        for command in cmds:
            proc = start([ROOT / "bin" / command[0], *command[1:]])
            if not select.select([proc.stdout], [], [], 15)[0]:
                raise TimeoutError(command)
            ready = proc.stdout.readline().strip()
            if ready != "READY" and name != "max":
                raise RuntimeError(f"{command}: {ready!r}")
        if name == "swap-in":
            try:
                (group / "memory.reclaim").write_text("80M swappiness=200")
            except OSError as exc:
                record["reclaim_status"] = str(exc)
            record["swap_before_go_bytes"] = int((group / "memory.swap.current").read_text())
            # Compare after preparation so swap-in stays separate from the explicit swap-out.
            start_state = snapshot(group)
            samples.append(start_state)
        if name != "max":
            if name != "swap-in":
                start_state = snapshot(group)
                samples.append(start_state)
            watcher = start([ROOT / "bin/psi_watch", "trigger", group / "memory.pressure",
                             str(duration)], worker=False)
            for proc in processes[:-1]:
                proc.stdin.write("GO\n")
                proc.stdin.flush()
        deadline = time.monotonic() + duration + 12
        while any(proc.poll() is None for proc in processes):
            if time.monotonic() > deadline:
                raise TimeoutError(name)
            samples.append(snapshot(group))
            time.sleep(.4)
        end = snapshot(group)
        samples.append(end)
        for index, proc in enumerate(processes):
            (directory / f"{name}-{index}.txt").write_text(proc.stdout.read())
        record["returncodes"] = [proc.returncode for proc in processes]
        if name == "max":
            assert record["returncodes"] == [-9]
        else:
            assert all(code == 0 for code in record["returncodes"][:-1])
        record["calculation"] = calculate(start_state, end)
        record["pswp"] = {
            key: counters(end["files"]["/proc/vmstat"]["raw"])[key]
            - counters(start_state["files"]["/proc/vmstat"]["raw"])[key]
            for key in ("pswpin", "pswpout")
        }
        (directory / f"{name}.json").write_text(json.dumps({"run": record, "raw": samples}, indent=2))
        print(name, record, flush=True)
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        for file in files:
            file.close()
        for attempt in range(20):
            try:
                group.rmdir()
                break
            except OSError:
                if attempt == 19:
                    raise
                time.sleep(.1)


def main():
    relative = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    if not relative.endswith("/observer") or not Path(relative).parent.name.startswith("run-"):
        raise SystemExit("Start in a fresh systemd user service with DelegateSubgroup=observer.")
    parent = Path("/sys/fs/cgroup", relative.lstrip("/")).parent
    if (parent / "memory.max").read_text().strip() != str(384 * MIB):
        raise SystemExit("Expected private parent MemoryMax=384M.")
    (parent / "cgroup.subtree_control").write_text("+memory")
    directory = ROOT / "evidence"
    directory.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(dir="/var/tmp") as data:
        for _ in range(128):
            data.write(os.urandom(MIB))
        data.flush()
        os.fsync(data.fileno())
        for name in ("normal", "cpu", "memory", "mixed", "file-refault", "high", "max", "swap-in"):
            run(name, parent, directory, Path(data.name))


if __name__ == "__main__":
    main()
