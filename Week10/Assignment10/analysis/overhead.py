"""Compare the collector's slow schedule on a quiet local system."""
import csv
import json
from math import ceil
import os
from pathlib import Path
import re
import subprocess as sp
import time

ROOT = Path(__file__).resolve().parents[1]


def run(interval):
    out = ROOT/"evidence"/f"overhead-slow-{interval}"
    out.mkdir()
    with (out/"probe.txt").open("w") as stream:
        probe = sp.Popen(["./memhog","probe","--interval","100"],cwd=ROOT,stdout=stream)
        try:
            begin = time.monotonic()
            proc = sp.Popen(["./memdiag",out/"monitor","--fast","1","--slow",str(interval),
                             "--seconds","12"],cwd=ROOT)
            _, status, usage = os.wait4(proc.pid,0)
            proc.returncode = os.waitstatus_to_exitcode(status)
            if proc.returncode:
                raise RuntimeError("memdiag failed")
            finish = time.monotonic()
        finally:
            probe.terminate()
            probe.wait(timeout=4)
    with (out/"monitor"/"samples.csv").open() as f:
        starts = {float(row["start_s"]): float(row["end_s"]) for row in csv.DictReader(f)}
    probes = sorted(float(us) for t, us in re.findall(r"^t=([\d.]+) probe_us=([\d.]+)",
                   (out/"probe.txt").read_text(), re.M) if begin <= float(t) <= finish)
    return {"slow_interval_s": interval,"cpu_s":usage.ru_utime+usage.ru_stime,
            "peak_rss_KiB":usage.ru_maxrss, "wall_s":finish-begin, "probe_n":len(probes),
            "probe_p99_us":probes[ceil(.99*len(probes))-1],
            "cycle_mean_ms":1000*sum(end-start for start,end in starts.items())/len(starts),
            "cycles":len(starts)}


if __name__ == "__main__":
    rows = [run(i) for i in (1,10)]
    (ROOT/"evidence"/"collector-overhead.json").write_text(json.dumps(rows,indent=2))
