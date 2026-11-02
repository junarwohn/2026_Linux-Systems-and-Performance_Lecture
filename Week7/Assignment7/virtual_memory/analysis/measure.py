#!/usr/bin/env python3
"""Collect reproducible native Week 7 evidence, preserving raw data."""
import contextlib
import csv
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
from level1.vasdump import snapshot, report, totals
OUT = ROOT/'evidence'
OUT.mkdir(exist_ok=True)


def command(name, argv, env=None, timeout=45):
    start = time.monotonic()
    with (OUT/(name+'.stdout')).open('w') as output, (OUT/(name+'.stderr')).open('w') as error:
        p = subprocess.Popen(argv, stdout=output, stderr=error, env=os.environ | (env or {}))
        try:
            rc = p.wait(timeout)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait(); rc = 124
    meta = dict(command=argv, environment=env or {}, pid=p.pid, returncode=rc,
                elapsed_s=time.monotonic()-start, kernel=os.uname().release)
    (OUT/(name+'.command.json')).write_text(json.dumps(meta, indent=2))
    return rc


def launch(name, argv, traced=True, env=None, prediction=''):
    trace = OUT/(name+'.trace')
    settings = (dict(LD_PRELOAD='./libmtrace_lite.so', MTRACE_OUT=str(trace)) if traced else {}) | (env or {})
    output = (OUT/(name+'.stdout')).open('w')
    error = (OUT/(name+'.stderr')).open('w')
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=output, stderr=error,
                         env=os.environ | settings, text=True)
    p.run_name = name; p.trace_path = trace; p.log_path = OUT/(name+'.stdout')
    p.started = time.monotonic(); p.handles = [output,error]; p.traced = traced
    (OUT/(name+'.command.json')).write_text(json.dumps(dict(command=argv, environment=settings,
        pid=p.pid, kernel=os.uname().release, prediction=prediction), indent=2))
    return p


def wait_stage(p, stage=None):
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        text = p.log_path.read_text()
        matches = re.findall(r'READY (?:phase|checkpoint)=(\S+)', text)
        if matches and (stage is None or matches[-1] == stage):
            return matches[-1]
        if p.poll() is not None:
            raise RuntimeError(f'{p.run_name}: exited before stage {stage}: {text}')
        time.sleep(.005)
    raise TimeoutError(f'{p.run_name}: stage {stage}')


def trace_snapshot(p):
    previous = p.trace_path.read_text() if p.trace_path.exists() else ''
    count = previous.count('end snapshot=')
    os.kill(p.pid, signal.SIGUSR1)
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        text = p.trace_path.read_text()
        if text.count('end snapshot=') > count:
            block = re.findall(r'snapshot=\d+ pid=.*?end snapshot=\d+\n', text, re.S)[-1]
            values = {k: int(v) for k,v in re.findall(r'(\w+)=(\d+)',block)}
            for key in ('malloc','calloc','realloc','free'):
                values[key] = int(re.search(r'^calls .*?\b'+key+r'=(\d+)',block,re.M).group(1))
            return values
        time.sleep(.005)
    raise TimeoutError('snapshot notification timed out')


def dump(p, label):
    prefix = OUT/(p.run_name+'-'+label)
    raw = Path(f'/proc/{p.pid}/smaps').read_text()
    prefix.with_suffix('.smaps').write_text(raw)
    prefix.with_suffix('.maps').write_text(Path(f'/proc/{p.pid}/maps').read_text())
    vmas = snapshot(p.pid)
    with prefix.with_suffix('.vasdump').open('w') as output, contextlib.redirect_stdout(output):
        print(f'pid={p.pid} stage={label}')
        report(vmas, True)
    return totals(vmas, 'category')


def finish(p, terminate=False):
    if terminate and p.poll() is None:
        p.terminate()
    try:
        rc = p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait(); rc = 124
    for handle in p.handles: handle.close()
    meta_path = OUT/(p.run_name+'.command.json')
    meta = json.loads(meta_path.read_text())
    meta.update(returncode=rc,elapsed_s=time.monotonic()-p.started)
    meta_path.write_text(json.dumps(meta, indent=2))
    if not terminate and rc:
        raise RuntimeError(f'{p.run_name} exit {rc}')


def paused(name, argv, stages, traced=True):
    p = launch(name,argv,traced, prediction='Touched small blocks grow heap; large block adds an anonymous VMA; live requests shrink before heap capacity does')
    records=[]
    try:
        for stage in stages:
            wait_stage(p,stage)
            tr = trace_snapshot(p) if traced else {}
            cat = dump(p,stage)
            records.append(dict(stage=stage,trace=tr,categories=cat))
            p.stdin.write('\n'); p.stdin.flush()
        finish(p)
    finally:
        if p.poll() is None: finish(p, True)
    (OUT/(name+'.measurements.json')).write_text(json.dumps(records, indent=2))
    return records


def cases(name, argv, env=None):
    p = launch(name,argv,True,env, 'case1: growing live bytes; case2: freed bytes with retained RSS; case3: virtual thread stacks dwarf resident pages')
    wait_stage(p)
    ready = time.monotonic(); next_sample = ready; index=0; captured=set()
    with (OUT/(name+'.csv')).open('w',newline='') as output:
        writer=csv.writer(output)
        writer.writerow(['run_id','sample','elapsed_s','phase','VmSize_kB','VmRSS_kB','snapshot','live_bytes','live_blocks'])
        while p.poll() is None:
            phase = re.findall(r'READY phase=(\S+)',p.log_path.read_text())[-1]
            try:
                status=Path(f'/proc/{p.pid}/status').read_text()
                size=int(re.search(r'^VmSize:\s+(\d+)',status,re.M).group(1))
                rss=int(re.search(r'^VmRSS:\s+(\d+)',status,re.M).group(1))
                tr=trace_snapshot(p)
                writer.writerow([name,index,f'{time.monotonic()-ready:.6f}',phase,size,rss,tr['snapshot'],tr['live_bytes'],tr['live_blocks']]);output.flush()
                if phase not in captured:
                    dump(p,phase); captured.add(phase)
            except (OSError,AttributeError,ProcessLookupError):
                # /proc memory fields may disappear just before wait reports exit.
                # Accept this only after observing a real process exit.
                try:
                    p.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    raise RuntimeError('memory fields disappeared while target stayed alive')
                break
            index+=1;next_sample+=1
            time.sleep(max(0,next_sample-time.monotonic()))
    finish(p)
    print(name,index,'samples',flush=True)


def main():
    command('environment',['bash','-c','uname -a; getconf GNU_LIBC_VERSION; getconf PAGESIZE; gcc --version; ulimit -s; command -v strace perf bpftrace'])
    command('build',['make','-j4'])
    for repeat in range(1,4):
        command(f'idle-sleep-r{repeat}',['./vasdump','-v','--delay','0.1','--','sleep','1000'])
        command(f'idle-own-r{repeat}',['./vasdump','-v','--delay','0.1','--','./idle_student'])
        paused(f'threads-r{repeat}',['./observation_workload','threads'],['four_threads'],False)
        for depth in (8,64):
            paused(f'stack{depth}-r{repeat}',['./observation_workload','stack',str(depth)],['deepest'],False)
        paused(f'elf-r{repeat}',['stdbuf','-o0','./elf_workload'],['layout'],False)
        paused(f'connection-r{repeat}',['./checkpoint_workload'],['baseline','A','B','C','D'])
        data=paused(f'known-r{repeat}',['./known_count','--checkpoints'],['before_pairs','after_pairs'])
        assert data[1]['trace']['malloc']-data[0]['trace']['malloc']==400000
        assert data[1]['trace']['free']-data[0]['trace']['free']>=400000
        own=paused(f'own-thread-r{repeat}',['./thread_student'],['before_pairs','after_pairs'])
        assert own[1]['trace']['malloc']-own[0]['trace']['malloc']==400000
        command(f'edges-r{repeat}',['./edge_cases'],dict(LD_PRELOAD='./libmtrace_lite.so',MTRACE_OUT=str(OUT/f'edges-r{repeat}.trace')))
        command(f'ls-r{repeat}',['ls','-l','/usr'],dict(LD_PRELOAD='./libmtrace_lite.so',MTRACE_OUT=str(OUT/f'ls-r{repeat}.trace')))
        command(f'python-r{repeat}',['python3','-c','print(sum(range(10**6)))'],dict(LD_PRELOAD='./libmtrace_lite.so',MTRACE_OUT=str(OUT/f'python-r{repeat}.trace')))
    data=paused('accounting',['./accounting_test'],['baseline','malloc17','realloc113','failed_realloc','zero_block','zero_realloc','released'])
    base=data[0]['trace']; expected=[(0,0),(1,17),(1,113),(1,113),(2,113),(1,0),(0,0)]
    for row,(blocks,nbytes) in zip(data,expected):
        assert row['trace']['live_blocks']-base['live_blocks']==blocks, row
        assert row['trace']['live_bytes']-base['live_bytes']==nbytes, row
    command('growth',['./vasdump','-v','--delay','0.2','--diff','5','--','./observation_workload','growth','12'])
    for arg in ('-hW','-lW','-SW'):
        command('readelf'+arg,['readelf',arg,'./elf_workload'])
    command('nm',['nm','-n','./elf_workload'])
    for repeat in range(1,4):
        cases(f'case1-r{repeat}',['./case1'])
        cases(f'case1-remedy-r{repeat}',['./case1_remedy'])
        cases(f'case2-r{repeat}',['./case2'])
        cases(f'case2-remedy-r{repeat}',['./case2'], {'GLIBC_TUNABLES':'glibc.malloc.mmap_threshold=1024:glibc.malloc.mmap_max=65536'})
        cases(f'case3-r{repeat}',['./case3'])
        cases(f'case3-remedy-r{repeat}',['bash','-c','ulimit -s 1024; exec ./case3'])
    # Tracing is a separate fresh run, without reported performance timings.
    trace_ok = True
    for case in (1,2,3):
        rc = command(f'case{case}-strace',['strace','-f','-e','trace=brk,mmap,munmap,mremap,madvise','-o',str(OUT/f'case{case}.strace'),f'./case{case}','--interval-ms','50','--ticks','12'])
        trace_ok = trace_ok and rc == 0
    print('MEASUREMENTS FINISHED; strace=' + ('PASS' if trace_ok else 'UNAVAILABLE/FAILED; inspect stderr and rerun with ptrace access'),flush=True)

if __name__=='__main__':main()
