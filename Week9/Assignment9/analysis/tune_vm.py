"""Paired reclaim tuning on the dedicated course VM."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess as sp
import time

ROOT = Path(__file__).resolve().parents[1]
MIB = 1 << 20
VARIABLES = {"vm.watermark_scale_factor": (1, 3000), "vm.swappiness": (0, 200)}


def interrupted(signum, _frame):
    raise SystemExit(128 + signum)


def parameter(variable):
    return Path("/proc/sys", *variable.split("."))


def set_value(file, value):
    file.write_text(f"{value}\n")
    effective = int(file.read_text())
    if effective != value:
        raise RuntimeError(f"{file}: read back {effective}, expected {value}")


@contextmanager
def setting(file, value):
    original = int(file.read_text())
    try:
        set_value(file, value)
        yield
    finally:
        set_value(file, original)


def warm(file, duration):
    with file.open("rb") as f:
        os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
    time.sleep(duration)


def run(args, variable, condition, value, index):
    output = args.output / f"{variable}-{condition}-{index}"
    output.mkdir(parents=True)
    processes = []
    logs = []
    commands = {}
    start = time.monotonic()
    result = {}

    def start_process(name, command):
        commands[name] = list(map(str,command))
        f = (output/f"{name}.log").open("w")
        logs.append(f)
        p = sp.Popen(command,cwd=ROOT,stdout=f,stderr=sp.STDOUT)
        processes.append(p)
        return p

    def overhead():
        return {name: parameter(name).read_text().strip()
                for name in ("vm.swappiness","vm.watermark_scale_factor","vm.min_free_kbytes")}

    try:
        result = {"run":output.name,"start_s":start,"settings":overhead(),
                  "swap":Path("/proc/swaps").read_text(), "commands":commands}
        if variable == "vm.swappiness":
            warm(args.file,2)
        start_process("sampler",["python3","student_support/sample.py",output/"system.csv",
                                 "--seconds",str(args.seconds+12)])
        start_process("psi",["./psimon"])
        start_process("probe",["./memhog","probe"])
        (output/"zones-before.txt").write_text(sp.check_output(["./zoneinfo"],cwd=ROOT,text=True))
        time.sleep(1)
        workload_start = time.monotonic()
        if variable == "vm.watermark_scale_factor":
            workers = [start_process("burst",["./memhog","burst",f"{args.anon_mib}MiB",
                "--burst",f"{args.burst_mib}MiB","--cycles",str(args.seconds),"--period","1"])]
        else:
            anon = start_process("anon",["./memhog","anon",f"{args.anon_mib}MiB","--hold",str(args.seconds),
                                        "--retouch","random","--seed",str(args.seed)])
            # Anon owns the warm working set before the buffered reader starts.
            deadline = time.monotonic() + 8
            while "phase=fill bytes=" not in (output/"anon.log").read_text():
                if anon.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("anonymous warmup did not complete")
                time.sleep(.1)
            size = args.file.stat().st_size
            loops = max(1, args.seconds * args.rate * MIB // size)
            file = start_process("file",["./memhog","file",args.file,"--rate",str(args.rate),"--loops",str(loops)])
            workers = [file,anon]
        result["workload_start_s"] = workload_start
        deadline = workload_start + args.seconds + 10
        for p in workers:
            if p.wait(timeout=max(.1,deadline-time.monotonic())):
                raise RuntimeError(f"{p.args}: {p.returncode}")
        result["workload_end_s"] = time.monotonic()
        (output/"zones-after.txt").write_text(sp.check_output(["./zoneinfo"],cwd=ROOT,text=True))
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
        for p in processes:
            try:
                p.wait(timeout=3)
            except sp.TimeoutExpired:
                p.kill()
                p.wait()
        for f in logs:
            f.close()
        result.update(end_s=time.monotonic(), returncodes=[p.returncode for p in processes])
        (output/"manifest.json").write_text(json.dumps(result,indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output",type=Path)
    p.add_argument("--file",type=Path,required=True,help="fully written ext4 input file")
    p.add_argument("--anon-mib",type=int,required=True)
    p.add_argument("--burst-mib",type=int,required=True)
    p.add_argument("--scale",type=int,required=True)
    p.add_argument("--swappiness",type=int,required=True)
    p.add_argument("--seconds",type=int,default=60)
    p.add_argument("--rate",type=int,default=128,help="file read rate in MiB/s")
    p.add_argument("--seed",type=int,default=42)
    a = p.parse_args()
    if os.geteuid() != 0:
        p.error("run as root in the dedicated course VM")
    a.output = a.output.resolve()
    a.file = a.file.resolve()
    st = a.file.stat()
    if not a.file.is_file() or st.st_size < MIB or st.st_blocks * 512 < st.st_size:
        p.error("expected a fully written regular file of at least 1 MiB")
    if sp.check_output(["findmnt","-T",a.file,"-no","FSTYPE"],text=True).strip() != "ext4":
        p.error("the course comparison requires an ext4 file")
    if min(a.anon_mib,a.burst_mib,a.rate) <= 0 or not 5 <= a.seconds <= 300 or a.seed < 0:
        p.error("invalid workload bounds")
    if not any(not line.split()[0].startswith("/dev/zram") for line in Path("/proc/swaps").read_text().splitlines()[1:]):
        p.error("record and enable the dedicated VM's disk swap before the swappiness comparison")
    available = next(int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()
                     if line.startswith("MemAvailable:"))
    if (a.anon_mib + a.burst_mib) * 1024 > available * .9:
        p.error("anonymous payloads exceed 90% of initial MemAvailable")
    a.output.mkdir(parents=True,exist_ok=False)
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)
    final = {}
    try:
        for (variable,(lo,hi)), value in zip(VARIABLES.items(),(a.scale,a.swappiness)):
            file = parameter(variable)
            origin = int(file.read_text())
            final[variable] = {"original":origin}
            if not lo <= value <= hi or value == origin:
                p.error(f"replacement {variable} must differ from {origin} and be in [{lo}, {hi}]")
            for index in range(1,4):
                conditions = [("original",origin),("changed",value)]
                if index % 2 == 0:
                    conditions.reverse()
                for label, effective in conditions:
                    with setting(file,effective):
                        run(a,variable,label,effective,index)
                    time.sleep(3)
    finally:
        for name in final:
            final[name]["readback"] = int(parameter(name).read_text())
        (a.output/"restoration.json").write_text(json.dumps(final,indent=2))


if __name__ == "__main__":
    main()
