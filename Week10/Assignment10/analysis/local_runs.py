"""Run isolated local OOM checks and matched M1/M2/M3 diagnostic pairs."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess as sp
import time

ROOT = Path(__file__).resolve().parents[1]
MIB = 1 << 20
FILES = ("memory.current", "memory.peak", "memory.stat", "memory.pressure", "memory.events",
         "memory.events.local", "memory.max", "memory.high", "memory.swap.max", "cgroup.procs")


def group(parent, name, *, max_mib, high_mib=None, group_kill=False):
    path = parent / name
    path.mkdir()
    (path / "memory.max").write_text(str(max_mib * MIB))
    (path / "memory.swap.max").write_text("0")
    if high_mib is not None:
        (path / "memory.high").write_text(str(high_mib * MIB))
    if group_kill:
        (path / "memory.oom.group").write_text("1")
    return path


def experiment(parent, out, name, commands, limits, *, diagnostics=True, trace=False, outside=None):
    out = out / name
    out.mkdir()
    cg = group(parent, f"lab10-{name}", **limits)
    children, streams, samples = [], [], []
    observer = probe = None

    def snap():
        fields = {key:(cg/key).read_text().strip() for key in FILES}
        procs = {}
        for p in (*children, *((outside,) if outside else ())):
            path = Path("/proc",str(p.pid))
            try:
                procs[str(p.pid)] = {key:(path/key).read_text() for key in ("smaps_rollup","oom_score","oom_score_adj","stat")}
            except OSError as exc:
                procs[str(p.pid)] = {"status": str(exc)}
        return {"t":time.monotonic(), "group":fields,"processes":procs}

    def enter():
        (cg / "cgroup.procs").write_text(str(os.getpid()))

    def start(label, argv, worker=False, tracing=False):
        sink = (out/(label+".txt")).open("w")
        streams.append(sink)
        env = os.environ.copy()
        if tracing:
            env["LD_PRELOAD"] = "./alloctrace.so"
        return sp.Popen(argv, cwd=ROOT, stdout=sink, stderr=sp.STDOUT, env=env,
                        preexec_fn=enter if worker else None)

    limits = {**limits}
    record = {"commands":commands,"start_s":time.monotonic(),"limits":limits,"group":str(cg),
              "scope":"fresh systemd user service", "page_B":os.sysconf("SC_PAGE_SIZE")}
    try:
        monitor = ["./memdiag", out/"monitor", "--fast", "1", "--slow", "10"] if diagnostics else ["./memmon", out/"monitor", "--interval", "0.2"]
        observer = start("monitor", [*monitor, "--cgroup", cg])
        probe = start("probe", ["./memhog", "probe", "--interval", "100"])
        samples.append(snap())
        time.sleep(.3)
        record["launch_s"] = time.monotonic()
        for index, command in enumerate(commands):
            children.append(start(f"work-{index}", command, True, trace))
            if name == "oom-C" and index < len(commands)-1:
                time.sleep(.2)
        deadline = time.monotonic() + 315 if name.startswith("long") else time.monotonic() + 60 if name.startswith("M3") else time.monotonic() + 18
        last_dump = time.monotonic()
        while any(p.poll() is None for p in children):
            current = time.monotonic()
            if current > deadline:
                raise TimeoutError(name)
            samples.append(snap())
            if trace and current - last_dump > (30 if name.startswith("long") else 1):
                for child in children:
                    if child.poll() is None:
                        child.send_signal(signal.SIGUSR1)
                last_dump = current
            time.sleep(1 if name.startswith("long") else .18)
        record["returncodes"] = [p.returncode for p in children]
        samples.append(snap())
        expected = [-9] * len(commands) if name in ("oom-B","oom-B-outside","oom-C","M2-original") else [0] * len(commands)
        if record["returncodes"] != expected:
            raise RuntimeError(f"{name}: expected {expected}, observed {record['returncodes']}")
    except BaseException as exc:
        record["error"] = repr(exc)
        raise
    finally:
        for proc in (*children,observer,probe):
            if proc and proc.poll() is None:
                proc.terminate()
        for proc in (*children,observer,probe):
            if not proc:
                continue
            try:
                proc.wait(timeout=3)
            except sp.TimeoutExpired:
                proc.kill()
                proc.wait()
        for f in streams:
            f.close()
        record["end_s"] = time.monotonic()
        record["returncodes"] = [proc.returncode for proc in children]
        record["termination"] = ("error/watchdog" if "error" in record else
                                 "memcg OOM" if -9 in record["returncodes"] else "workload completed")
        (out/"manifest.json").write_text(json.dumps(record,indent=2))
        (out/"checkpoints.json").write_text(json.dumps(samples,indent=2))
        cg.rmdir()
    print(name, record["returncodes"], flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--long", choices=("original","fixed"))
    parser.add_argument("--m3-only", action="store_true")
    parser.add_argument("--outside-case", action="store_true")
    args = parser.parse_args()
    cg = Path("/proc/self/cgroup").read_text().strip().split("::",1)[1]
    if not cg.endswith("/observer") or not Path(cg).parent.name.startswith("run-"):
        parser.error("Use a transient unit with DelegateSubgroup=observer.")
    parent = Path("/sys/fs/cgroup",cg.lstrip("/")).parent
    if (parent/"memory.max").read_text().strip() != str(384*MIB):
        parser.error("Expected parent MemoryMax=384M.")
    (parent/"cgroup.subtree_control").write_text("+memory")
    out = ROOT/"evidence"
    if args.outside_case:
        cmd = ["./memhog","anon","64MiB","--hold","12"]
        with (out/"oom-B-outside-observer.txt").open("w") as f, sp.Popen(cmd,cwd=ROOT,stdout=f,stderr=f) as outside:
            try:
                time.sleep(.25)
                if outside.poll() is not None:
                    raise RuntimeError("outside workload failed before B")
                experiment(parent,out,"oom-B-outside",[["./memhog","anon","64MiB","--rate","64","--hold","2"]],
                           {"max_mib":40},diagnostics=False,outside=outside)
                if outside.poll() is not None:
                    raise RuntimeError("outside workload exited during B")
                print("outside PID still alive after OOM",outside.pid,flush=True)
            finally:
                outside.terminate()
        return
    if args.long:
        binary = "./scenario-fixed" if args.long == "fixed" else "./scenario"
        return experiment(parent,out,"long-M1-"+args.long, [[binary,"M1","--bytes","128KiB","--base","8MiB",
            "--cycles","300","--period","1","--seconds","310","--seed","42"]],
            {"max_mib":128}, trace=True)
    if not args.m3_only:
        experiment(parent,out,"reserve",[["./memhog","reserve","64MiB","--touch",".5","--step","8MiB","--hold","1"]],
                   {"max_mib":96}, diagnostics=False)
        experiment(parent,out,"oom-B",[["./memhog","anon","64MiB","--rate","64","--hold","2"]],
                   {"max_mib":40}, diagnostics=False)
        experiment(parent,out,"oom-C",[["./memhog","anon","24MiB","--rate","48","--hold","8"]]*3,
                   {"max_mib":56,"group_kill":True}, diagnostics=False)
        for i in range(1,4):
            for mode,binary in (("original","./scenario"),("fixed","./scenario-fixed")):
                experiment(parent,out,f"M1-{mode}-{i}",[[binary,"M1","--bytes","4MiB","--base","8MiB","--cycles","10",
                        "--period",".44","--seconds","6","--seed","42"]],{"max_mib":96},trace=True)
        for mode,limit in (("original",40),("fixed",80)):
            experiment(parent,out,"M2-"+mode,[["./scenario","M2","--bytes","48MiB","--base","8MiB",
                       "--cycles","3","--hold",".75","--period",".3","--seconds","6","--seed","42"]],
                       {"max_mib":limit})
    for mode,limit in (("original",38),("fixed",64)):
        experiment(parent,out,"M3-"+mode,[["./scenario","M3","--bytes","32MiB","--base","8MiB",
                   "--cycles","3","--hold",".65","--period",".3","--seconds","15","--seed","42"]],
                   {"max_mib":80,"high_mib":limit})


if __name__ == "__main__":
    main()
