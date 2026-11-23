"""Create aligned before/after figures from the local monotonic checkpoints."""
import csv
import json
from pathlib import Path
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
MIB = 1 << 20
EVIDENCE = ROOT / "evidence"


def load(name):
    path = EVIDENCE/name
    manifest = json.loads((path/"manifest.json").read_text())
    rows = json.loads((path/"checkpoints.json").read_text())
    output = (path/"work-0.txt").read_text()
    return manifest, rows, output


def pss(row):
    for proc in row["processes"].values():
        value = re.search(r"^Pss:\s+(\d+)", proc.get("smaps_rollup", ""), re.M)
        if value:
            return int(value[1])/1024
    return float("nan")


def summary():
    rows = []
    for manifest in sorted(EVIDENCE.glob("*/manifest.json")):
        if not manifest.with_name("checkpoints.json").exists():
            continue
        run = manifest.parent.name
        metadata, samples, output = load(run)
        final = samples[-1]
        values = dict(line.split()[:2] for line in final["group"]["memory.events"].splitlines())
        live = re.search(r"reason=exit .*?live_bytes=(\d+)",output)
        observed_pss = [pss(row) for row in samples]
        observed_pss = [x for x in observed_pss if x == x]
        rows.append({"run":run, "cycles":output.count("event=cycle"), "returncodes":metadata.get("returncodes"),
                     "sampled_s":round(final["t"]-metadata["launch_s"],4),
                     "end_live_B":int(live[1]) if live else None,
                     "peak_Pss_MiB":round(max(observed_pss),3) if observed_pss else None,
                     "peak_group_MiB":round(int(final["group"]["memory.peak"])/MIB,3),
                     "PSI_some_us": re.search(r"^some .*?total=(\d+)",final["group"]["memory.pressure"],re.M)[1],
                     "high":values["high"],"max":values["max"],"oom":values["oom"],
                     "oom_kill":values["oom_kill"],"oom_group_kill":values.get("oom_group_kill")})
    with (EVIDENCE/"summary.csv").open("w") as f:
        writer = csv.DictWriter(f,rows[0])
        writer.writeheader()
        writer.writerows(rows)


def plot(case, runs):
    fig, axes = plt.subplots(2,1,figsize=(8.2,5.5),sharex=True,layout="constrained")
    axes[0].set_ylabel("Cgroup memory.current (MiB)")
    axes[1].set_xlabel("Seconds from workload launch")
    axes[1].set_ylabel({"M1":"Process PSS (MiB)","M2":"Cgroup OOM kills","M3":"Memory PSI some (s accumulated)"}[case])
    for name, color in zip(runs,("#b44c3a","#26768f")):
        manifest, rows, _ = load(name)
        rows = [row for row in rows if row["t"] >= manifest["launch_s"]]
        at = [row["t"]-manifest["launch_s"] for row in rows]
        memory = [int(row["group"]["memory.current"])/MIB for row in rows]
        axes[0].plot(at,memory, label=name,color=color)
        if case == "M1":
            other = [pss(row) for row in rows]
        elif case == "M2":
            other = [int(re.search(r"^oom_kill (\d+)",row["group"]["memory.events"],re.M)[1]) for row in rows]
            limit = manifest["limits"]["max_mib"]
            axes[0].axhline(limit,color=color,linestyle=":",alpha=.6)
            # Peaks are kernel counters and can occur between sampling cycles.
            peak = int(rows[-1]["group"]["memory.peak"])/MIB
            axes[0].scatter([at[-1]],[peak],color=color,marker="D",s=20)
        else:
            other = [int(re.search(r"^some .*?total=(\d+)",row["group"]["memory.pressure"],re.M)[1])/1e6 for row in rows]
            axes[0].axhline(manifest["limits"]["high_mib"],color=color,linestyle=":",alpha=.6)
        axes[1].step(at,other,where="post",label=name,color=color)
    for axis in axes:
        axis.grid(axis="y",alpha=.2)
        axis.set_xlim(left=0)
        axis.legend(loc="best")
    fig.suptitle({"M1":"M1 | Expired allocations before and after free",
                  "M2":"M2 | Same peak workload at 40 MiB and 80 MiB limits",
                  "M3":"M3 | Same work at 38 MiB and 64 MiB memory.high"}[case])
    fig.savefig(EVIDENCE/f"{case}-before-after.png",dpi=180)
    plt.close(fig)


if __name__ == "__main__":
    summary()
    plot("M1",("M1-original-1","M1-fixed-1"))
    plot("M2",("M2-original","M2-fixed"))
    plot("M3",("M3-original","M3-fixed"))
