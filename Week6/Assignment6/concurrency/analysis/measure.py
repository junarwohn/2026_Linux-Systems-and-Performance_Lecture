#!/usr/bin/env python3
"""Reproduce Week 6 measurements without editing any provided test/tool."""
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
OUT = ROOT / 'evidence'
OUT.mkdir(exist_ok=True)

def run(name, command, prediction, env=None, timeout=180):
    path = OUT / (name + '.txt')
    with path.open('w') as log:
        log.write(f'prediction: {prediction}\ncommand: {command!r}\nenv: {env or {}}\nkernel: {os.uname()}\n')
        log.flush()
        start = time.monotonic()
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   env=os.environ | (env or {}))
        log.write(f'pid: {process.pid}\n'); log.flush()
        try:
            rc = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(); rc = 124
        log.write(f'elapsed_s: {time.monotonic()-start:.6f}\nreturncode: {rc}\n')
    print(name, rc, flush=True)
    return rc

def variants():
    variant_dir = OUT / 'variants'
    variant_dir.mkdir(exist_ok=True)
    bus = (ROOT/'src/bus.c').read_text()
    old_bus = (OUT/'baseline/bus.c').read_text()
    old_publish = old_bus[old_bus.index('void bus_publish'):old_bus.index('/* ==== TODO(L3-2) end')]
    w2 = bus[:bus.index('void bus_publish')] + old_publish + bus[bus.index('/* ==== TODO(L3-2) end'):]
    w2 = w2.replace('pthread_mutex_init(&bus_lock, NULL);', '''pthread_mutexattr_t attr;
    pthread_mutexattr_init(&attr);
    pthread_mutexattr_settype(&attr, PTHREAD_MUTEX_RECURSIVE);
    pthread_mutex_init(&bus_lock, &attr);
    pthread_mutexattr_destroy(&attr);''')
    catalog = (ROOT/'src/catalog.c').read_text()
    pos = catalog.index('int catalog_set_perm')
    w3 = catalog[:pos] + catalog[pos:].replace('pthread_rwlock_rdlock(&apps_lock);\n    pthread_rwlock_wrlock(&perms_lock);', 'pthread_rwlock_wrlock(&perms_lock);\n    pthread_rwlock_rdlock(&apps_lock);')
    config = (ROOT/'src/config.c').read_text()
    w4a = config.replace('pthread_mutex_unlock(&cfg_lock);\n    /* Do not hold a lock needed by registered readers during a grace period. */\n    synchronize_rcu();\n    config_free(old);', 'synchronize_rcu();\n    config_free(old);\n    pthread_mutex_unlock(&cfg_lock);')
    w4b = config.replace('    synchronize_rcu();\n', '')
    for tag, text, filename, case in [('w2',w2,'bus.c',2), ('w3',w3,'catalog.c',3), ('w4a',w4a,'config.c',4), ('w4b',w4b,'config.c',4)]:
        source = variant_dir / (tag + '.c'); source.write_text(text)
        sources = ['src/devsvc.c','src/rcu.c'] + [str(source) if f==filename else 'src/'+f for f in ['sink.c','bus.c','catalog.c','config.c']]
        base = ['cc','-g','-std=gnu11','-pthread','-Isrc']
        run(tag+'-build',base+['-O2','-rdynamic','-o',str(variant_dir/tag)]+sources, 'Build the deliberate incorrect fix separately')
        run(tag+'-build-tsan',base+['-O1','-fsanitize=thread','-o',str(variant_dir/(tag+'_tsan'))]+sources,'Build the diagnostic variant')
        predicted = {'w2':'No deadlock, but persistent callbacks can be skipped; low parallelism', 'w3':'Read/write edges can deadlock', 'w4a':'Grace period waits for a worker blocked on cfg_lock', 'w4b':'Use-after-free or inconsistent frames; TSan can detect races'}[tag]
        env={'DEVSVC':str(variant_dir/tag),'DEVSVC_TSAN':str(variant_dir/(tag+'_tsan')),'TAG':tag}
        run(tag+'-check',['bash','tools/l3_check.sh',str(case)],predicted,env,timeout=350)

if __name__ == '__main__':
    run('environment',['bash','-c','uname -a; getconf GNU_LIBC_VERSION; getconf PAGESIZE; gcc --version; nproc; taskset -pc $$; command -v bpftrace perf strace; ulimit -s'], 'Record actual environment, including unavailable tools')
    original = ['src/devsvc.c','src/rcu.c'] + [str(OUT/'baseline'/f) for f in ('sink.c','bus.c','catalog.c','config.c')]
    for suffix, flags in [('', ['-O2','-rdynamic']), ('_tsan', ['-O1','-fsanitize=thread'])]:
        run('baseline-build'+suffix, ['cc','-g','-std=gnu11','-pthread','-Isrc',*flags,'-o',str(OUT/'baseline'/('devsvc'+suffix)),*original], 'Build preserved original source independently')
    for case in range(1,5):
        run(f'l3-{case}-baseline-native',['env','LD_PRELOAD=./bin/lockcheck.so',str(OUT/'baseline/devsvc'),'-c',str(case),'-d','1'],'Original sources deadlock with circular acquisition or recursive mutex acquisition')
        run(f'l3-{case}-baseline-tsan',[str(OUT/'baseline/devsvc_tsan'),'-c',str(case),'-d','1'],'TSan does not report the case-2 self-acquire; the process still hangs')
    run('l1-check',['bash','tools/l1_check.sh'],'All normal and TSan close/timeout/stress checks pass')
    run('bugif-build',['make','bugif'],'Build if-instead-of-while counterexample')
    for repeat in range(1,4):
        for cap in (1,4,64):
            run(f'l1-q{cap}-r{repeat}',['./bin/bqtest','-p','2','-c','2','-n','100000','-q',str(cap)],'Larger capacity reduces producer/consumer sleeps and generally increases throughput')
        for tag,extra in [('signal',[]),('broadcast',['-b'])]:
            run(f'l1-{tag}-r{repeat}',['./bin/bqtest','-p','4','-c','4','-n','50000','-q','4']+extra,'Broadcast wakes extra threads; predicates must be rechecked')
        run(f'l1-work-r{repeat}',['./bin/bqtest','-p','2','-c','2','-n','50000','-q','4','-w','20'],'Completion can invert while dequeue order remains correct')
    run('l1-bugif',['./bin/bqtest_bugif','-p','2','-c','2','-n','20000','-q','4','-T','10'],'A woken waiter can lose the predicate before reacquiring the mutex')
    for repeat in range(1,4):
        for tag,mode,options,burn in [('lock','lock',[],False),('snapshot','snapshot',[],False),('handoff','handoff',[],False),('lock-D5','lock',['-D','5'],False),('lock-cpu','lock',['-D','0','-c','1'],False),('lock-burn','lock',['-D','0','-c','1'],True),('snapshot-burn','snapshot',['-D','0','-c','1'],True)]:
            burner = subprocess.Popen(['bash','tools/burn.sh','1','12'],stdout=subprocess.DEVNULL) if burn else None
            try:
                run(f'l2-{tag}-r{repeat}',['./bin/albumsvc','-m',mode,'-d','10',*options,'-o',str(OUT/f'l2-{tag}-r{repeat}.csv')], 'Snapshot shortens lock ownership; handoff pays bounded application work; CPU contention can preempt a lock holder')
            finally:
                if burner: burner.wait()
    for case in range(1,5):
        run(f'l3-{case}-after',['bash','tools/l3_run.sh',str(case),'-d','3'],'No lockcheck warnings; TSan may flag read-read order in case 3')
        run(f'l3check-{case}',['bash','tools/l3_check.sh',str(case)],'All five criteria pass under two-CPU affinity')
    variants()
    run('l3-writer',['bash','tools/l3_run.sh','3','-d','3'],'Queued writers can make read/read inverse orders unsafe',{'DEVSVC_RWLOCK':'writer','TAG':'3-writer'})
    for name in ('bpftrace','perf'):
        (OUT/f'{name}-availability.txt').write_text(f'{name}: '+ (shutil.which(name) or 'UNAVAILABLE: executable not installed in this execution environment')+'\n')
    for log in (ROOT/'logs').glob('*'):
        if log.is_file(): shutil.copy2(log, OUT/log.name)
