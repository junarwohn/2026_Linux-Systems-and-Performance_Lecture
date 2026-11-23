"""Timestamped CSV views for memory monitoring and outside-in diagnosis."""
import argparse
import csv
import json
import math
import os
from pathlib import Path
import re
import signal
import time

MEMORY = ("MemTotal", "MemAvailable", "CommitLimit", "Committed_AS", "SwapTotal",
          "SwapFree", "Slab", "SReclaimable", "SUnreclaim", "CmaFree")
COUNTERS = ("pgscan", "pgsteal", "allocstall", "pswpin", "pswpout", "compact", "workingset_refault")
FAST_GROUP = ("memory.current", "memory.max", "memory.high", "memory.swap.current",
              "memory.swap.max", "memory.events", "memory.pressure")
stop = False


def halt(*_):
    global stop
    stop = True


def sample(groups, pids, slow, diagnostic, only_explicit=False):
    rows = []

    def read(path, callback):
        try:
            callback(Path(path).read_text())
        except (OSError, ValueError, IndexError) as exc:
            add(path, "read", "", "", str(exc))

    def add(source, field, unit, value, status="ok"):
        rows.append((str(source), field, unit, value, status))

    def key_values(raw, source, unit, keep=lambda key: True):
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) >= 2 and keep(parts[0].rstrip(":")):
                add(source, parts[0].rstrip(":"), unit, parts[1])

    def pressure(raw, path):
        for line in raw.splitlines():
            kind, *tokens = line.split()
            for pair in tokens:
                field, value = pair.split("=")
                if field in ("avg10", "total"):
                    add(path, f"{kind}.{field}", "us" if field == "total" else "%", value)

    def system_memory(raw):
        values = {line.split(":")[0]: line.split()[1] for line in raw.splitlines()}
        for key in MEMORY:
            add("/proc/meminfo", key, "KiB", values.get(key, ""), "ok" if key in values else "absent")

    if slow or not diagnostic:
        read("/proc/meminfo", system_memory)
    for resource in ("memory", "cpu", "io"):
        path = "/proc/pressure/" + resource
        read(path, lambda raw: pressure(raw, path))
    read("/proc/vmstat", lambda raw: key_values(raw, "/proc/vmstat", "native",
         lambda key: key.startswith(COUNTERS)))
    for group in groups:
        for name in FAST_GROUP + (("memory.stat",) if slow or not diagnostic else ()):
            path = group / name
            if name.endswith("pressure"):
                read(path, lambda raw: pressure(raw, path))
            elif name.endswith(("events", "stat")):
                unit = (lambda key: "events" if key.startswith(("pg", "workingset", "thp", "numa"))
                        else "B") if name.endswith("stat") else lambda key: "events"
                def stats(raw):
                    for line in raw.splitlines():
                        key, value = line.split()[:2]
                        add(path, key, unit(key), value)
                read(path, stats)
            else:
                read(path, lambda raw: add(path, name, "B", raw.strip()))

    targets = set(pids)
    for group in groups:
        path = group / "cgroup.procs"
        read(path, lambda raw: targets.update(map(int, raw.split())))
    if diagnostic and slow and not only_explicit:
        ranked = []
        for proc in Path("/proc").glob("[0-9]*"):
            try:
                status = (proc/"status").read_text()
                rss = re.search(r"^VmRSS:\s+(\d+)",status,re.M)
                if rss:
                    ranked.append((int(rss[1]), int(proc.name)))
            except OSError as exc:
                add(proc / "status", "top10_eligibility", "", "", str(exc))
        targets.update(pid for _, pid in sorted(ranked,reverse=True)[:10])

    for pid in sorted(targets):
        base = Path("/proc", str(pid))
        try:
            stat = (base/"stat").read_text()
            identity = stat[stat.rindex(")")+1:].split()[19]
            status = (base/"status").read_text()
        except (OSError, ValueError, IndexError) as exc:
            add(base, "identity", "", "", f"exit/unavailable: {exc}")
            continue
        current_len = len(rows)
        unit = lambda key: "KiB" if key != "Name" else "text"
        for line in status.splitlines():
            key, _, value = line.partition(":")
            if key in ("Name", "VmRSS", "RssAnon", "RssFile", "RssShmem"):
                add(base, key, unit(key), value.split()[0] if key != "Name" else value.strip())
        add(base, "start_ticks", "ticks", identity)
        for name in ("oom_score", "oom_score_adj", "cgroup"):
            read(base/name, lambda raw: add(base, name, "raw", raw.strip()))
        if diagnostic and slow:
            read(base/"smaps_rollup", lambda raw: key_values(raw, base/"smaps_rollup",
                 "KiB", lambda key: key in ("Pss", "Rss")))
        try:
            after = (base/"stat").read_text()
            if after[after.rindex(")")+1:].split()[19] != identity:
                raise ValueError("PID reused")
        except (OSError, ValueError, IndexError) as exc:
            del rows[current_len:]
            add(base, "identity", "", "", f"exit/unavailable: {exc}")
    if diagnostic and slow:
        read("/proc/buddyinfo", lambda raw: add("/proc/buddyinfo", "orders", "blocks", raw))
        try:
            page = os.sysconf("SC_PAGE_SIZE")
            slab = []
            for line in Path("/proc/slabinfo").read_text().splitlines()[2:]:
                left, _, right = line.partition(": slabdata")
                a, b = left.split(), right.split()
                slab.append((int(a[5]) * int(b[1]) * page, a[0]))
            add("/proc/slabinfo", "top5", "B", json.dumps(sorted(slab,reverse=True)[:5]))
        except (OSError, ValueError, IndexError) as exc:
            add("/proc/slabinfo", "top5", "B", "", str(exc))
    return rows


def main(diagnostic):
    p = argparse.ArgumentParser()
    p.add_argument("output", type=Path)
    p.add_argument("--cgroup", action="append", type=Path, default=[])
    p.add_argument("--pid", action="append",type=int, default=[])
    p.add_argument("--seconds", type=float)
    p.add_argument("--fast" if diagnostic else "--interval", type=float, default=1, dest="fast")
    if diagnostic:
        p.add_argument("--slow", type=float, default=10)
        p.add_argument("--only-explicit", action="store_true", help="omit system-wide PID ranking")
    else:
        p.set_defaults(slow=1, only_explicit=False)
    args = p.parse_args()
    if any(not math.isfinite(x) or x <= 0 for x in (args.fast, args.slow) + ((args.seconds,) if args.seconds is not None else ())):
        p.error("intervals/duration must be positive")
    if any(pid <= 0 for pid in args.pid):
        p.error("PID must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    manifest = vars(args).copy()
    manifest["output"] = str(args.output)
    manifest["cgroup"] = [str(path) for path in args.cgroup]
    manifest.update(start=time.monotonic(), clock="CLOCK_MONOTONIC", diagnostic=diagnostic)
    next_slow = 0
    data = args.output/"samples.csv"
    metadata = args.output/"manifest.json"
    if metadata.exists():
        p.error("output already contains a run manifest")
    try:
        stream = data.open("x")
    except FileExistsError:
        p.error("output already contains samples.csv")
    try:
        with stream as f:
            writer = csv.writer(f)
            writer.writerow(("start_s","end_s","source","field","unit","value","status","rate_per_s"))
            previous = {}
            while not stop:
                start = time.monotonic()
                slow = start >= next_slow
                rows = sample(args.cgroup, args.pid, slow, diagnostic, args.only_explicit)
                if slow:
                    next_slow = start + args.slow
                end = time.monotonic()
                for row in rows:
                    source, field, unit, value, status = row
                    rate = ""
                    if source == "/proc/vmstat" and status == "ok":
                        old = previous.get(field)
                        n = int(value)
                        if old and n >= old[1] and start > old[0]:
                            rate = (n-old[1])/(start-old[0])
                        previous[field] = (start, n)
                    writer.writerow((start,end,*row,rate))
                f.flush()
                if args.seconds is not None and end >= manifest["start"] + args.seconds:
                    break
                deadline = start + args.fast
                while not stop and time.monotonic() < deadline:
                    time.sleep(min(.1,max(0, deadline-time.monotonic())))
    finally:
        manifest.update(end=time.monotonic(), termination="signal" if stop else "duration/error")
        with metadata.open("x") as f:
            json.dump(manifest, f, indent=2)
