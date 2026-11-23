# Linux Systems and Performance — Week 6~10

| 주차 | Lab 보고서 | Assignment 보고서 및 제출 자료 | 실측 상태 |
|---|---|---|---|
| Week 6 | [동시성 실습](Week6/Lab6/report.md) | [Concurrency](Week6/Assignment6/concurrency/report.md) · [tar.gz](Week6/Assignment6/assignment6_submission.tar.gz) · [futex 후속 측정](Week6/Assignment6/local-evidence/README.md) | Lab 및 제출 기능 검증. bpftrace·perf 계측 미수집 |
| Week 7 | [메모리 매핑 실습](Week7/Lab7/report.md) | [Process Memory](Week7/Assignment7/virtual_memory/report.md) · [tar.gz](Week7/Assignment7/assignment7_submission.tar.gz) | Lab checkpoint 및 필수 과제 실험 |
| Week 8 | [Page Fault 실습](Week8/Lab8/report.md) | [보고서](Week8/Assignment8/report.md) · [구현·실행](Week8/Assignment8/README.md) | 페이지·allocator 실험 3회. root slabinfo·BPF attach 필요 |
| Week 9 | [PSI·회수 실습](Week9/Lab9/report.md) | [보고서](Week9/Assignment9/report.md) · [구현·실행](Week9/Assignment9/README.md) | 반복 cgroup 비교·zram·PSI trigger. 전역 회수·디스크 swap·sysctl 비교 미측정 |
| Week 10 | [메모리 진단 실습](Week10/Lab10/report.md) | [보고서](Week10/Assignment10/report.md) · [구현·실행](Week10/Assignment10/README.md) | memcg OOM·M1~M3 전후 측정. 전역 A/D 및 지정 보드 실험 미측정 |

Week 6·7의 두 submission tar를 풀어 SHA-256 manifest와 기존 디렉터리를 각각 확인했다. Week 6의 257개, Week 7의 624개 파일이 모두 일치했다. Week 9·10은 위임된 transient cgroup에서 제한과 범위를 증명했고, 나머지 실측 조건은 각 보고서에 남겼다.

## Week6 — Concurrency

- bounded queue의 blocking put/get, monotonic timeout, close 후 drain과 전체 대기자 깨우기를 구현했다.
- album service의 deep-copy snapshot과 UI 소유권 기반 handoff를 구현했다.
- sink 잠금 순서, 재진입 callback, rwlock 모드, RCU grace period를 적용해 네 교착상태 사례를 수정했다.
- 일반/ThreadSanitizer 큐 테스트와 네 사례의 lockcheck·TSan·stress·parallel·speed 검증을 모두 통과했다.
- 용량·깨움 방식·UI 모드를 3회씩 측정하고, 잘못된 수정과 writer-preferring rwlock의 반례를 보관했다.

`bpftrace`와 `perf`가 측정 환경에 없어 futex 호출자별 집계, lockwait, perf sched timehist는
미완료다. 사용 가능한 환경에서 실행할 명령은 [trace.sh](Week6/Assignment6/concurrency/analysis/trace.sh)에 있다.

## Week7 — Process Memory

- `/proc/<pid>/smaps`를 직접 파싱하는 vasdump에 VMA 분류·집계·상세 출력·diff·자식 프로세스 정리를 구현했다.
- malloc/calloc/realloc/free 추적기에 요청 횟수, pointer ownership, histogram, live/peak bytes와 SIGUSR1 snapshot을 구현했다.
- ELF·ASLR·스레드·재귀·메모리 증가와 allocation/free 체크포인트를 관측했다.
- 4개 스레드의 malloc 400,000회를 정확히 집계하고, allocator edge case·메타데이터 부족·vasdump 테스트를 검증했다.
- 필수 case1~3의 원인과 개선 전후를 각각 새 프로세스로 3회 측정했다. 선택 사례 case4·5의 추가 진단은 진행하지 않았다.

## 실행 환경과 자료

기존 실측 환경은 Linux 7.2.11-arch1-1, x86-64, glibc 2.44, GCC 16.2.1,
페이지 4096 B다. Week6는 논리 CPU 18개인 머신에서 측정 프로세스를 CPU 0·1로 제한했다.
과제에서 제시한 WSL2 환경과의 차이는 보고서에 기록했다.

각 과제의 `evidence/`에는 원문 로그·CSV·메모리 덤프·시스템콜 trace가 있고,
`SUBMISSION.md`에는 출력 명세와 실험별 파일 지도가 있다.
제출 묶음은 소스·보고서·로그·체크섬을 포함하며, 압축 해제 후 빌드·실행을 확인한 기록은
[Week6 검증 기록](Week6/Assignment6/submission-validation.txt)과
[Week7 검증 기록](Week7/Assignment7/submission-validation.txt)에 있다.

분석 및 코드 작성에 Codex를 사용했다. 검증하거나 수정한 제안은 각 보고서의 AI 사용 내역에 명시했다.
