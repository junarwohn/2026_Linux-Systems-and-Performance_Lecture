"""Aggregate scoped pressure counters separately from global vmstat."""
import csv
import json
from math import ceil
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def counters(raw):
    return {line.split()[0]: int(line.split()[1]) for line in raw.splitlines()}


def psi(raw):
    return {line.split()[0]: int(line.split("total=")[1]) for line in raw.splitlines()}


def delta(left, right, file, key, parse=counters):
    a, b = parse(left[file]), parse(right[file])
    return b[key]-a[key] if key in a and key in b and b[key] >= a[key] else None


def quantiles(values):
    values = sorted(values)
    return [values[ceil(len(values)*p)-1] if values else None for p in (.5,.99,1)]


def metric(output, expression):
    m = re.search(expression, output)
    return float(m[1]) if m else None


def run(path):
    data = json.loads((path/"manifest.json").read_text())
    before, after = data["before"], data["after"]
    interval = data["workload_end_s"]-data["start_s"]
    work = (path/"work.txt").read_text()
    bounds = data["workload_start_s"],data["workload_end_s"]
    probes = [float(us) for t,us in re.findall(r"^t=([\d.]+) probe_us=([\d.]+)",
              (path/"probe.txt").read_text(),re.M) if bounds[0] <= float(t) <= bounds[1]]
    trigger = (path/"trigger.txt").read_text()
    result = {"run":path.name,"group_elapsed_s":interval,
              "workload_elapsed_s":bounds[1]-bounds[0],
              "work_MiB_s":metric(work,r"phase=file_end .*?throughput_MiB_s=([\d.]+)"),
              "fill_s":metric(work,r"phase=fill .*?elapsed_s=([\d.]+)"),
              "retouch_s":metric(work,r"phase=retouch .*?elapsed_s=([\d.]+)"),
              "probe_n":len(probes),
              "trigger_status":"registered" if "registered metric=" in trigger else "fallback",
              "trigger_notifications":trigger.count("event metric=")}
    result.update(zip(("probe_p50_us","probe_p99_us","probe_max_us"),quantiles(probes)))
    for file, key, label in (("memory.pressure", "some", "memory_some"), ("memory.pressure", "full", "memory_full"),
                            ("cpu.pressure","some","cpu_some")):
        count = delta(before,after,file,key,psi)
        result[label+"_delta_us"] = count
        result[label+"_pct"] = count/(interval*10000) if count is not None else None
    for key in ("workingset_refault_file","pgscan_direct","pgscan_kswapd","pgscan_proactive","pswpin","pswpout"):
        count = delta(before,after,"memory.stat",key)
        result[key+"_pages_s"] = count/interval if count is not None else None
    for key in ("high","max","oom","oom_kill"):
        result[key+"_events"] = delta(before,after,"memory.events",key)
    return result


def main():
    rows = [run(m.parent) for m in sorted((ROOT/"evidence").glob("*/manifest.json"))]
    with (ROOT/"evidence"/"summary.csv").open("w") as f:
        writer = csv.DictWriter(f,rows[0])
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
