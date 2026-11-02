# Assignment 7 제출 안내

## 환경과 재현

Linux 7.2.11-arch1-1, x86-64, glibc 2.44, GCC 16.2.1, 페이지 4096 B,
기본 stack limit 8192 kB. 기본 allocator tunable은 별도로 지정하지 않았다.
case2 개선은 `GLIBC_TUNABLES=glibc.malloc.mmap_threshold=1024:glibc.malloc.mmap_max=65536`,
case3 개선은 새 프로세스의 `ulimit -s 1024`다.

```sh
make -j4
make smoke
python3 tests/test_vasdump.py
./vasdump -v -- sleep 1000
MTRACE_OUT=trace.log LD_PRELOAD=./libmtrace_lite.so ./edge_cases
python3 analysis/measure.py
python3 analysis/report.py
```

비교는 새 프로세스에서 3회 반복했다. `analysis/measure.py`가 checkpoint의 marker를
기다리고 SIGUSR1 snapshot의 end marker와 vasdump를 확인한 뒤 Enter를 보낸다.
case1~3은 기본 ticks/interval/mib/threads를 유지했다. strace는 시간 비교와 분리한
진단으로 `--interval-ms 50 --ticks 12`를 썼다. 샌드박스가 ptrace를 차단하므로
승인된 외부 strace 실행으로 다시 수집했으며 성공/실패 시도의 메타데이터를 구별했다.
재실행 시 같은 evidence 파일이 덮어써지므로 먼저 기존 디렉터리를 보관한다.

컴파일 플래그는 Makefile의 `-O1 -g -std=c11 -Wall -Wextra -Wpedantic -fno-builtin
-fno-omit-frame-pointer`, ELF는 PIE, recursion은 sibling-call 최적화 비활성화다.

## 출력과 구현

vasdump의 VMA 열은 range, permissions, file offset, device, inode, category,
Size/Rss/Pss, pathname이다. category와 permission별 표는 8개 필드를 모두 kB
(1024 B)로 출력한다. missing field/대상 종료는 불완전 snapshot 오류이며 nonzero다.
동시에 변경되는 프로세스를 원자적으로 정지시키는 도구는 아니다.
diff는 address range로 짝지어 added/removed/changed와 signed Size/Rss를 출력하고
absolute Rss, absolute Size 내림차순, start 오름차순으로 정렬한다.
PID 모드는 대상을 유지하고 command 모드만 TERM → 1초 후 KILL → reap한다.

mtrace는 requested-size accounting이다. 정적 pointer table 262144개, tombstone 선형
탐색으로 free/realloc ownership을 관리하며 메타데이터 부족은 `complete=no`로 남긴다.
histogram은 성공한 non-NULL 요청의 크기를 `0`, `1`, `2-3` ...
`9223372036854775808-18446744073709551615`로 센다. realloc(p,0)의 NULL은 glibc의
해제 동작이며 live에서 제거하지만 새 할당 histogram에는 넣지 않는다.
`calls`는 실패·free(NULL)를 포함한 outer API 호출 수다. `null_results`는 NULL 반환 수로,
zero-size realloc처럼 NULL이 정상인 경우도 포함하므로 전부 실패 횟수로 읽지 않는다.
bytes/blocks/peak의 단위는 bytes와 개수다. `snapshot=N`부터 `end snapshot=N`까지
하나의 일관된 보고다. `untracked_releases`는 통계에서 소유하지 않은 포인터 해제 수다.

제공 support.c의 forwarding/bootstrap/TLS guard/mutex/notification/destructor를 유지했다.
전역 mutex는 실제 allocator 호출부터 hook까지 직렬화해 정확한 통계를 얻지만 원래
allocator의 병렬성을 줄인다. reporter 자체의 할당은 제외하며 일반 runtime 할당은 포함한다.
정적 메타데이터의 .bss와 reporter stack은 vasdump에는 보이고 요청 통계에는 없다.
SIGUSR1 handler는 errno 보존과 pipe write만 한다. reporter가 mutex 밖에서 기다리고
mutex 아래에서 보고한다. 알림은 합쳐지거나 full pipe에서 누락될 수 있다.
bootstrap은 1 MiB 정적 arena에서 max_align_t 정렬, overflow/capacity 검사 후 배정한다.
bootstrap free는 no-op, realloc은 별도 bootstrap block으로 min(old,new)를 복사하며
저장 공간은 프로세스 종료까지 유지한다. glibc free로 넘기지 않는다.
MTRACE_INTERVAL은 구현하지 않은 선택 기능이다. fork/cancellation/대체 allocator는 범위 밖이다.

## 제공 파일과 변경

| 경로 | 작성/수정 내용 |
|---|---|
| level1/vasdump.py | 직접 smaps parser, 분류, 집계, diff, 자식 수명 관리 |
| level2/mtrace_student.c | 요청 카운터, ownership, histogram, live/peak, 출력 |
| level2/support.c, mtrace_student.h | 제공 코드 그대로 사용 |
| Makefile, case1~case5 launcher | 실제 level3/cases 경로에 맞춘 빌드와 launcher |
| workloads/idle_student.c, thread_student.c | 직접 작성한 최소 관측/4-thread workload |
| workloads/case1_remedy.c | 제공 case1의 별도 개선 비교; baseline 원본은 보존 |
| tests/accounting.c, test_vasdump.py | 추가 accounting/lifecycle/분류 검증 |
| analysis/measure.py, report.py | 측정 동기화와 보고서 표 생성 |
| analysis/case1-remedy.patch | case1 개선 비교의 정확한 변경 |

PDF는 case 소스 비공개를 전제하지만 배포 README와 실제 저장소에는 소스가 있다.
baseline은 원본을 수정하지 않고 빌드·실행했으며 진단 결론은 실측과 함께 제시했다.
PDF/README의 예시 `cases/` 경로는 이 저장소의 `level3/cases/`와 다르므로 바로잡았다.

## 근거 파일 지도

| 실험 | evidence 파일 prefix |
|---|---|
| idle / thread / recursion | idle-sleep-rN, idle-own-rN, threads-rN, stack8-rN, stack64-rN |
| ELF / ASLR | elf-rN, readelf-hW/-lW/-SW, nm |
| 5초 growth diff | growth, growth-r2, growth-r3 |
| connection / allocator count | connection-rN, known-rN, own-thread-rN |
| ls / Python / edge APIs | ls-rN, python-rN, edges-rN, accounting |
| metadata exhaustion | exhaustion.trace, exhaustion-test.txt |
| 사례 / 개선 | case1/2/3-rN, case1/2/3-remedy-rN |
| 시스템콜 증거 | case1.strace, case2.strace, case3.strace |

N=1,2,3. `.command.json`은 실제 argv/env/PID/상태/시간, `.stdout/.stderr`는 원문,
`.trace`는 allocator snapshot, `.smaps/.maps/.vasdump`는 관측 시점별 덤프다.
CSV는 run_id, sample, READY부터의 elapsed_s, 최근 phase marker, VmSize/VmRSS kB,
snapshot 번호, live_bytes, live_blocks를 기록한다. status와 allocator snapshot 사이에는
수 ms 차이가 있다. 자동 진행 case의 전이 중 sample은 안정 상태 비교에서 제외한다.
일반 runtime과 observer가 있기 때문에 raw VmRSS와 workload 바이트를 동일시하지 않는다.

보고서 [report.md](report.md)에 ELF 표, baseline 대비 checkpoint 변화, 사례별 근거와
대안·개선·확신도, 필수 분석 문항 답변 및 AI 사용 내역이 있다.
