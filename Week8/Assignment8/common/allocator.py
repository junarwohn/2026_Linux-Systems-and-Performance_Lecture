import os
from pathlib import Path
import re
import sys

PAGE = os.sysconf("SC_PAGE_SIZE")
MEM = ("Slab", "SReclaimable", "SUnreclaim", "PageTables", "KernelStack")


def watermarks():
    zones = {}
    for line in Path("/proc/zoneinfo").read_text().splitlines():
        match = re.match(r"Node (\d+), zone\s+(\S+)", line)
        if match:
            key = (int(match[1]), match[2])
            zones[key] = {}
        fields = line.split()
        if fields[:2] == ["pages", "free"]:
            zones[key]["free_pages"] = int(fields[2])
        elif fields and fields[0] in ("min", "low", "high"):
            zones[key][fields[0] + "_pages"] = int(fields[1])
    return zones


def buddy():
    zones = {}
    for line in Path("/proc/buddyinfo").read_text().splitlines():
        _, node, _, zone, *orders = line.split()
        zones[int(node.rstrip(",")), zone] = [int(x) for x in orders]
    return zones


def migrate():
    rows = {}
    for line in Path("/proc/pagetypeinfo").read_text().splitlines():
        m = re.match(r"Node\s+(\d+), zone\s+(\S+), type\s+(\S+)\s+(.*)", line)
        if m:
            rows[int(m[1]), m[2], m[3]] = [int(x) if x.isdigit() else x for x in m[4].split()]
    return rows


def row(active, total, size, per_slab, pages, active_slabs, slabs):
    return dict(active_objects=active, total_objects=total, object_B=size, objects_per_slab=per_slab,
                pages_per_slab=pages, active_slabs=active_slabs, total_slabs=slabs,
                cache_KiB=slabs * pages * PAGE // 1024 if slabs is not None and pages is not None else None,
                utilization_pct=100 * active / total if total and active is not None else None)


def slabs():
    try:
        result = {}
        for line in Path("/proc/slabinfo").read_text().splitlines()[2:]:
            left, _, right = line.partition(": slabdata")
            name, *fields = left.split()[:6]
            result[name] = row(*map(int, fields + right.split()[:2]))
        return result, {}
    except OSError as exc:
        print(f"/proc/slabinfo: {exc}; sysfs fallback, unreadable fields are null", file=sys.stderr)
    result, aliases = {}, {}
    for entry in sorted(Path("/sys/kernel/slab").glob("*")):
        if entry.is_symlink():
            aliases[entry.name] = entry.resolve().name
            continue

        def number(name):
            try:
                return int((entry / name).read_text().split()[0])
            except (OSError, ValueError, IndexError):
                return None

        order = number("order")
        result[entry.name] = row(None, number("total_objects"), number("object_size"),
                                 number("objs_per_slab"), 1 << order if order is not None else None,
                                 None, number("slabs"))
    return result, aliases


def meminfo():
    data = {line.split(":")[0]: int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()}
    return {key + "_KiB": data.get(key) for key in MEM}
