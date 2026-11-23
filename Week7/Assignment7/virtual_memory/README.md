# Assignment 7 — Process Memory

vasdump, malloc 추적기, 필수 case1~3 진단의 Level 1~3 구현·실험·검증을 완료했다.
아래 결과는 `evidence/`에 보관된 실행 로그를 기준으로 한다. 선택 사례 case4·5는
빌드·실행 가능한 제공 코드이며 추가 진단은 진행하지 않았다.

## 구현 내용

| 단계 | 파일 | 구현 및 핵심 동작 |
|---|---|---|
| Level 1 | `level1/vasdump.py` | smaps 직접 파싱, exe/heap/stack/library/file/anonymous/special 분류, 권한·분류별 합계, 상세 출력, 주소 범위별 diff, command 모드 자식 종료·회수 |
| Level 2 | `level2/mtrace_student.c` | malloc/calloc/realloc/free 호출 수, pointer ownership, 요청 크기 histogram, live/peak bytes, metadata 부족 표시, snapshot 출력 |
| Level 2 지원 | `level2/support.c`, `mtrace_student.h` | 제공 forwarding·bootstrap·TLS guard·mutex·SIGUSR1 notification 코드를 사용 |
| 추가 workload/test | `workloads/idle_student.c`, `thread_student.c`, `tests/accounting.c`, `test_vasdump.py` | 최소 sleep, 4-thread 할당, allocator accounting, parser·diff·프로세스 수명 검증 |
| Level 3 | `analysis/measure.py`, `workloads/case1_remedy.c` | 새 프로세스 3회 반복 측정, phase별 근거 수집, case1의 별도 source 개선 비교 |

실제 제공 사례 소스는 `level3/cases/`에 있다. Makefile과 `./case1`~`./case5` launcher를
이 경로에 맞췄다. baseline 사례 소스는 보존했고 case1 개선은 별도 파일로 작성했다.

## 빌드와 기본 검증

64-bit Linux/glibc, C compiler, GNU make, Python 3, binutils, strace가 필요하다.
이 디렉터리에서 실행한다.

```sh
make -j4
make smoke
python3 tests/test_vasdump.py
```

`make smoke`는 allocator API와 제공 workload의 실행을 검사한다. 통계의 정확성은
별도의 체크포인트·known-count·accounting·metadata 부족 실험으로 확인했다.
전체 측정과 표 생성 명령은 다음과 같다. 같은 이름의 결과를 덮어쓰므로 재실행 전
기존 `evidence/`를 보관한다. strace 실행에는 ptrace 권한이 필요하다.

```sh
python3 analysis/measure.py
python3 analysis/report.py
```

## 도구 사용

vasdump의 메모리 단위는 kB=1024 B다. category·permission 합계는 각각 전체 합계와 같다.
`-v`는 VMA별 주소·권한·offset·경로·Size/Rss/Pss를 출력하고, `--diff`는 두 시점의
added/removed/changed 범위와 signed Size/Rss 변화량을 보여 준다.

```sh
./vasdump -v -- sleep 1000
./vasdump -v PID
./vasdump --delay 0.2 --diff 5 -- ./observation_workload growth 12
```

PID 모드는 대상을 유지한다. command 모드는 관측 후 TERM을 보내고, 1초 내 종료하지
않으면 KILL 후 회수한다. 대상 종료·필드 누락 등은 불완전 snapshot 오류로 처리한다.

추적기는 requested-size accounting을 사용한다. 호출 수에는 실패·`free(NULL)`도 포함하며,
histogram은 성공한 non-NULL 요청을 크기별로 센다. live/peak의 단위는 bytes다.
`realloc(p,0)`의 glibc 해제 동작, 실패한 nonzero realloc의 기존 블록 보존,
0바이트 non-NULL 블록의 소유권을 처리했다.

```sh
MTRACE_OUT=trace.log LD_PRELOAD=./libmtrace_lite.so ./edge_cases
MTRACE_OUT=known.trace LD_PRELOAD=./libmtrace_lite.so ./known_count
MTRACE_OUT=connection.trace LD_PRELOAD=./libmtrace_lite.so ./checkpoint_workload
```

체크포인트 프로그램이 출력한 PID를 사용해 다른 터미널에서 관측한다.

```sh
kill -USR1 PID
# Wait for "end snapshot=N" in connection.trace before the next observation.
./vasdump -v PID
```

snapshot과 vasdump가 끝난 뒤 workload 터미널에서 Enter를 눌러 다음 단계로 진행한다.
`LD_PRELOAD`에는 위의 상대 경로를 사용한다. 공백이 들어간 절대 경로는 loader가
여러 항목으로 나눌 수 있다. SIGUSR1 handler는 알림만 보내고 별도 reporter가 통계를 출력한다.
정적 metadata와 reporter allocation은 요청 통계에서 제외되며 vasdump에는 보일 수 있다.

## 검증 및 실험 결과

| 검증 | 보관된 결과 | 근거 |
|---|---|---|
| allocator smoke/edge API | EDGE_OK 및 정상 종료 | [smoke 출력](evidence/smoke.stdout), [추적 통계](evidence/edges-r1.trace) |
| vasdump 추가 테스트 | 6개 PASS | [vasdump-tests.txt](evidence/vasdump-tests.txt) |
| 제공/직접 작성한 4-thread workload | workload malloc delta 각각 400,000회, 3회 반복 검증 | [known-count](evidence/known-r1.measurements.json), [직접 작성한 workload](evidence/own-thread-r1.measurements.json) 및 r2/r3 |
| realloc 실패·zero-size·free accounting | 단계별 live blocks/bytes의 기대값 일치 | [accounting.measurements.json](evidence/accounting.measurements.json) |
| metadata 부족 | 용량 8개인 별도 빌드에서 부족 993건과 `complete=no` 표시 | [exhaustion-test.txt](evidence/exhaustion-test.txt) |
| ls/Python 출력 보존 | 3회 모두 원래 출력과 일치 | [stdout-comparison.txt](evidence/stdout-comparison.txt) |
| 필수 case1~3 및 개선 비교 | 총 18개 실행, 각각 10개 이상 시계열 표본, 정상 종료 | [검증 기록](evidence/validation.txt) |

ELF의 실제 PT_LOAD와 VMA 대응, 두 symbol의 load bias 확인, ASLR, idle/thread/recursion,
5초 growth diff를 관측했다. connection 실험에서는 같은 PID의 baseline → A → B → C → D를
동기화해 library 통계와 smaps를 함께 기록했다.

| 체크포인트 | workload 저장 상태 | baseline 대비 live 변화 (kB) |
|---|---|---:|
| A | touched 1,000 × 1 KiB | +1000.000 |
| B | A + touched 16 MiB | +17384.000 |
| C | 큰 블록 해제 | +1000.000 |
| D | 작은 블록도 모두 해제 | +0.000 |

3회 모두 위 live 변화를 확인했다. D에서 live가 0이 되어도 heap Size/Rss는 유지되어,
요청 바이트와 allocator가 보유하는 메모리의 차이를 관측했다.
근거는 [connection-r1.measurements.json](evidence/connection-r1.measurements.json)과 r2/r3이다.

## 필수 사례 진단

표는 동일한 안정 phase에서 비교한 3회 중앙값이며, 메모리 단위는 kB다.
범위와 원문은 [보고서](report.md)에 있다.

| 사례 | 진단 | 적용한 개선 | 개선 전 → 후 |
|---|---|---|---|
| case1 | 매 tick의 할당을 해제하지 않아 소유권을 잃음 | 별도 `case1_remedy`에서 사용 후 free | live 12288 → 0 |
| case2 | 대부분 free한 뒤에도 마지막 live chunk와 allocator 보유 영역 때문에 RSS 유지 | `glibc.malloc.mmap_threshold=1024`, `glibc.malloc.mmap_max=65536` | Rss 39036 → 6212; live 68 → 68 |
| case3 | 24개 worker의 큰 가상 stack 예약 | 새 프로세스에서 stack limit 1024 kB | VSZ 212980 → 33780; live 6.75 → 6.75 |

```sh
./case1 --help
./case1
./case1_remedy
GLIBC_TUNABLES=glibc.malloc.mmap_threshold=1024:glibc.malloc.mmap_max=65536 ./case2
bash -c 'ulimit -s 1024; exec ./case3'
```

baseline과 개선은 새 프로세스에서 동일 부하로 실행했다. 원래 binary의 tunable/환경 변경과
case1의 별도 source 변경을 구별했다. CSV의 phase는 최근 출력 marker이며 전이 중 표본은
안정 상태 비교에서 제외했다. free 후 Rss 유지나 큰 VSZ만으로 누수를 확정하지 않았다.

## 실행 환경과 해석 범위

실측 환경은 Linux 7.2.11-arch1-1, x86-64, glibc 2.44, GCC 16.2.1,
페이지 4096 B, 기본 stack limit 8192 kB다. strace는 시간 측정과 분리해 수집했다.
이번 환경은 guard에 `MADV_GUARD_INSTALL`을 사용해 별도 `---p` VMA가 보이지 않을 수 있다.
stack의 anonymous VMA가 병합될 수 있으므로 VMA 개수를 스레드 수로 해석하지 않았다.

추적기의 전역 mutex는 일관된 통계를 제공하지만 allocator 호출을 직렬화하는 비용이 있다.
direct mmap·정렬 할당 API·thread stack 등은 네 API의 요청 통계 범위 밖이다.
live allocation은 누수 후보이며 소유권·수명을 함께 확인해야 한다.
`MTRACE_INTERVAL`은 미구현 선택 기능이다. fork/exec tree, 대체 allocator,
SIGUSR1을 이미 사용하는 프로그램은 지원 범위 밖이다.

## 보고서와 제출 자료

| 경로 | 내용 |
|---|---|
| [report.md](report.md) | ELF 표·connection 표·필수 사례 진단·분석 문항·AI 사용 내역 |
| [SUBMISSION.md](SUBMISSION.md) | 출력 명세·bootstrap/동시성 설계·실험별 파일 지도 |
| [evidence/](evidence/) | 명령·환경·PID·종료 상태, 원문 로그, CSV, smaps/maps/vasdump 및 strace |
| [analysis/](analysis/) | 측정·표 생성 드라이버와 case1 개선 patch |
| [제출 묶음](../assignment7_submission.tar.gz) | 소스·문서·실험 근거·체크섬 |
| [압축 해제 검증 기록](../submission-validation.txt) | 별도 디렉터리의 체크섬·빌드·실행 검증 결과 |

Codex를 사용해 구현·측정·분석을 작성했다. 실제 관측으로 수정한 가정과 검증한 코드 이슈는
보고서에 명시했다.
