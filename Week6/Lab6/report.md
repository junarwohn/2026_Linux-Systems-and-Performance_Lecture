# Lab 6 — 동시성, lock 대기와 condition variable

Linux 7.3.4-arch1-2, 논리 CPU 18개와 GCC 16.2.1에서 제공 소스를 빌드했다. `tools/lab.sh`의 1, 1tsan, 2c, 2d, 3ifwait, 3iolock을 각각 실행했다. 1·2 thread lockbench는 직접 실행하고 futex는 strace로 측정했다. [명령·출력](evidence/console.txt)

| 카운터 모드 | 결과 / 기대값 | ns/increment |
|---|---:|---:|
| plain | 5,444,908 / 10,000,000 | 2.09 |
| atomic | 10,000,000 / 10,000,000 | 26.29 |
| mutex | 10,000,000 / 10,000,000 | 46.85 |
| local | 10,000,000 / 10,000,000 | 0.17 |

plain의 read/modify/write가 겹치면 둘이 같은 값을 읽어 한 증가분을 덮어쓸 수 있다. C의 data race에는 undefined behavior가 있어 lost를 실제 잃은 명령 수로 볼 수 없다. TSan plain은 우연히 결과가 정확했지만 동기화 없는 충돌 접근을 보고했다. atomic에는 같은 실행에서 경고가 없었다. 일반 빌드로만 시간을 비교했다. atomic의 공유 cache line은 코어 간 소유권이 이동하고, padding된 local은 각 thread의 counter를 분리해 false sharing을 줄인다. [plain TSan](evidence/lab1-tsan-plain.txt), [atomic TSan](evidence/lab1-tsan-atomic.txt)

| Lock 모드 | 1 thread ns/op | 2 threads ns/op | 2 threads sleeps |
|---|---:|---:|---:|
| spin | 287 | 261 | 0 |
| MCS | 263 | 405 | 0 |
| mutex | 407 | 663 | 62,920 |
| adaptive | 382 | 370 | 2,183 |

무경쟁 mutex의 빠른 경로는 userspace에서 끝난다. 별도의 strace에는 시작·join도 포함된다. Futex wake는 waiter를 실행 가능 상태로 바꾸며, 대기자는 이후 userspace에서 lock을 얻기 위해 다시 경쟁한다. 19 threads에서 MCS는 5초에 26,020/3,800,000 ops를 끝냈다. FIFO 선두 waiter가 선점되면 뒤의 runnable waiter도 기다린다. [oversubscribed MCS](evidence/lab2-t19-mcs.txt), [mutex syscall](evidence/futex-mutex-2.trace)

두 reader의 rwlock은 105.5 ns/read, RCU는 2.7 ns/read였다. writer p50은 각 3 µs와 104 µs였다. RCU는 새 사본을 게시하고 이미 시작된 reader가 이전 포인터를 놓을 때까지 기다린다. grace period를 생략한 버전은 bad read 73회를 기록했다. [rwlock](evidence/lab2-cfg-rwlock-r2.txt), [RCU](evidence/lab2-cfg-rcu-r2.txt), [nogp](evidence/lab2-cfg-nogp-r2.txt)

- ifwait의 duplicate는 200, empty take는 399였다. 깨움 뒤 mutex를 다시 잡았을 때 다른 consumer가 item을 이미 가져갔을 수 있어 while에서 queue predicate를 재검사해야 한다. [ifwait](evidence/lab3-ifwait.txt)
- I/O를 lock 안에서 한 버전과 푼 뒤 실행한 버전은 둘 다 item 200개를 처리했다. producer lock wait p99는 4,729 → 0 µs, 총 시간은 1,035 → 508 ms였다. 빈 공간을 기다리는 condvar 시간은 별도의 대기다. [iolock](evidence/lab3-iolock.txt), [fixed](evidence/lab3-fixed.txt)
- nounlock GDB에서 producer는 mutex lock, consumer는 cond wait, main은 join 경로에 있었다. owner TID 33329는 남아 있는 세 thread 중에 없었다. 같은 조건의 별도 wchan 관찰은 futex 수면을 보여 줬다. bpftrace/perf가 없어 strace를 저장했다. syscall 원문만으로 pthread caller별 집계를 확인할 수 없다. [GDB](evidence/nounlock-gdb.txt), [fixed futex](evidence/cv-fixed.trace)

Codex로 실행과 분석을 자동화했다. sibling GDB attach가 거부된 뒤 대상의 부모를 GDB로 exec해 owner와 thread backtrace를 읽었다.
