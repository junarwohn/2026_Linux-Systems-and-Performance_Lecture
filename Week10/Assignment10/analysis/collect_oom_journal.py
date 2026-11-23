"""Export course-run OOMs from this boot's readable kernel journal."""
import json
from pathlib import Path
import re
import subprocess as sp

ROOT = Path(__file__).resolve().parents[1]
journal = sp.check_output(["journalctl","-k","-b","--no-pager","-o","short-monotonic"], text=True)
rows = []
for raw in journal.splitlines():
    m = re.match(r"^\[\s*(\d+\.\d+)\] \S+ kernel: (.*)",raw)
    if m:
        rows.append((float(m[1]), m[2]))

for case in ("oom-B", "oom-B-outside", "oom-C", "M2-original"):
    out = ROOT / "evidence" / case
    manifest = json.loads((out/"manifest.json").read_text())
    group = manifest["group"].removeprefix("/sys/fs/cgroup")
    selected = [(t, line) for t, line in rows if manifest["launch_s"] <= t <= manifest["end_s"] + .35]
    entry = next(i for i, (_, line) in enumerate(selected) if f"oom_memcg={group}," in line)
    start = next(i for i in range(entry, -1, -1) if "invoked oom-killer:" in selected[i][1])
    stop = entry+1
    while stop < len(selected) and any(token in selected[stop][1] for token in
                ("Memory cgroup out of memory: Killed process", "are going to be killed due to memory.oom.group")):
        stop += 1
    lines = [f"[{t:.6f}] {line}" for t,line in selected[start:stop]
             if not line.startswith("Hardware name:")]
    (out/"kernel-oom.log").write_text("\n".join(lines)+"\n")
    cmd = [ROOT/"oomparse", out/"kernel-oom.log", "--page-size", str(manifest["page_B"]), "--total-pages",
           str(manifest["limits"]["max_mib"] * (1<<20)//manifest["page_B"]), "--run", case]
    proc = sp.check_output(cmd,text=True,cwd=ROOT)
    (out/"oomparse.jsonl").write_text(proc)
    record, = map(json.loads, proc.splitlines())
    assert record["scope"]["oom_memcg"] == group and record["tasks"] and record["request"]
    if case == "oom-C":
        assert len(record["additional_group_victims"]) == 2
