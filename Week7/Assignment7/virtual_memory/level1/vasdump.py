#!/usr/bin/env python3
"""Interface skeleton. Implement /proc parsing and reports without inspection libraries."""
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

FIELDS = ("Size", "Rss", "Pss", "Shared_Clean", "Shared_Dirty",
          "Private_Clean", "Private_Dirty", "Anonymous")
CATEGORIES = ("executable", "heap", "stack", "shared-library",
              "other-file", "anonymous", "special")

def parse_smaps(path):
    header = re.compile(r'^([0-9a-f]+)-([0-9a-f]+)\s+(\S+)\s+([0-9a-f]+)\s+'
                        r'(\S+)\s+(\d+)(?:\s+(.*))?$')
    records = []
    with open(path, encoding='utf-8', errors='replace') as stream:
        for line in stream:
            match = header.match(line.rstrip('\n'))
            if match:
                start, end, perms, offset, dev, inode, name = match.groups()
                records.append(dict(start=int(start, 16), end=int(end, 16),
                                    permissions=perms, offset=int(offset, 16),
                                    dev=dev, inode=int(inode), path=name or '', fields={}))
            elif records and ':' in line:
                key, value = line.split(':', 1)
                if key in FIELDS:
                    records[-1]['fields'][key] = int(value.split()[0])
    if not records:
        raise ValueError('incomplete snapshot: no VMAs')
    for vma in records:
        missing = set(FIELDS) - vma['fields'].keys()
        if missing:
            raise ValueError('incomplete snapshot: missing fields ' + ','.join(sorted(missing)))
    return records

def classify(vma, executable):
    name = vma['path']
    if name == '[heap]':
        return 'heap'
    if name == '[stack]':
        return 'stack'
    if name.startswith('['):
        return 'special'
    if not name:
        return 'anonymous'
    clean = name.removesuffix(' (deleted)')
    if isinstance(executable, dict):
        major, minor = (int(x, 16) for x in vma['dev'].split(':'))
        if vma['inode'] and executable.get('inode'):
            same = (vma['inode'] == executable['inode']
                    and os.makedev(major, minor) == executable['dev'])
        else:
            same = clean == executable['path'].removesuffix(' (deleted)')
    else:
        same = clean == str(executable).removesuffix(' (deleted)')
    if same:
        return 'executable'
    if re.search(r'\.so(?:\.|$)', Path(clean).name):
        return 'shared-library'
    return 'other-file'


def identity(pid):
    # The name in stat may contain spaces or parentheses.
    tail = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    if tail[0] in ('Z', 'X'):
        raise ValueError('target exited')
    return tail[19]  # starttime is field 22; tail starts at field 3.


def snapshot(pid):
    before = identity(pid)
    exe_path = f'/proc/{pid}/exe'
    st = os.stat(exe_path)
    exe = dict(path=os.readlink(exe_path), dev=st.st_dev, inode=st.st_ino)
    vmas = parse_smaps(f'/proc/{pid}/smaps')
    if identity(pid) != before:
        raise ValueError('incomplete snapshot: process identity changed')
    for vma in vmas:
        vma['category'] = classify(vma, exe)
    return vmas


def totals(vmas, key):
    result = {}
    for vma in vmas:
        values = result.setdefault(vma[key], dict.fromkeys(FIELDS, 0))
        for field in FIELDS:
            values[field] += vma['fields'][field]
    return result


def print_table(label, rows):
    print(label, *(field + '_kB' for field in FIELDS))
    for key, values in rows.items():
        print(key, *(values[field] for field in FIELDS))


def report(vmas, verbose):
    if verbose:
        print('range permissions offset device inode category Size_kB Rss_kB Pss_kB pathname')
        for v in vmas:
            print(f"{v['start']:x}-{v['end']:x} {v['permissions']} {v['offset']:08x} "
                  f"{v['dev']} {v['inode']} {v['category']} "
                  f"{v['fields']['Size']} {v['fields']['Rss']} {v['fields']['Pss']} {v['path']}")
    categories = totals(vmas, 'category')
    print_table('category', {c: categories.get(c, dict.fromkeys(FIELDS, 0)) for c in CATEGORIES})
    print_table('permissions', dict(sorted(totals(vmas, 'permissions').items())))
    overall = {f: sum(v['fields'][f] for v in vmas) for f in FIELDS}
    print_table('scope', {'TOTAL': overall})
    print('snapshot complete=yes units=kB note=proc_smaps_is_not_an_atomic_process_freeze')


def diff_report(first, second):
    a = {(v['start'], v['end']): v for v in first}
    b = {(v['start'], v['end']): v for v in second}
    changes = []
    for key in a.keys() | b.keys():
        old, new = a.get(key), b.get(key)
        ds = (new['fields']['Size'] if new else 0) - (old['fields']['Size'] if old else 0)
        dr = (new['fields']['Rss'] if new else 0) - (old['fields']['Rss'] if old else 0)
        if old != new:
            status = 'added' if old is None else 'removed' if new is None else 'changed'
            changes.append((key, ds, dr, status, (new or old)['path']))
    changes.sort(key=lambda row: (-abs(row[2]), -abs(row[1]), row[0][0]))
    print('status range delta_Size_kB delta_Rss_kB pathname')
    for (start, end), ds, dr, status, name in changes:
        print(f'{status} {start:x}-{end:x} {ds:+d} {dr:+d} {name}')
    old, new = totals(first, 'category'), totals(second, 'category')
    print('category delta_Size_kB delta_Rss_kB')
    for cat in CATEGORIES:
        values = {f: new.get(cat, {}).get(f, 0) - old.get(cat, {}).get(f, 0) for f in FIELDS}
        print(f"{cat} {values['Size']:+d} {values['Rss']:+d}")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-v', action='store_true')
    parser.add_argument('--delay', type=float, default=1.0)
    parser.add_argument('--diff', type=float)
    parser.add_argument('target', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    import math
    if (not math.isfinite(args.delay) or args.delay < 0 or
            (args.diff is not None and (not math.isfinite(args.diff) or args.diff <= 0))):
        parser.error('delay must be nonnegative and diff must be positive')
    if not args.target:
        parser.error('provide a PID or -- command [args...]')
    child = None
    try:
        if args.target[0] == '--':
            if len(args.target) == 1:
                parser.error('missing command after --')
            child = subprocess.Popen(args.target[1:])
            pid = child.pid
            time.sleep(args.delay)
        else:
            if len(args.target) != 1 or not args.target[0].isdigit() or int(args.target[0]) < 1:
                parser.error('expected a positive PID or -- command')
            pid = int(args.target[0])
        print(f'pid={pid}')
        target_identity = identity(pid)
        first = snapshot(pid)
        report(first, args.v)
        if args.diff is not None:
            time.sleep(args.diff)
            if identity(pid) != target_identity:
                raise ValueError('target identity changed between snapshots')
            second = snapshot(pid)
            print('second snapshot:')
            report(second, args.v)
            diff_report(first, second)
        return 0
    except (OSError, ValueError) as error:
        print(f'vasdump: incomplete snapshot: {error}', file=sys.stderr)
        return 1
    finally:
        if child is not None:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    child.kill()
            child.wait()

if __name__ == '__main__':
    sys.exit(main())
