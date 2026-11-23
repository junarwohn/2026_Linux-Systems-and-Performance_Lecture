# Week 6 — Concurrency

- [실습 실행 안내](Lab6/README.md) · [실습 보고서](Lab6/report.md)
- [과제 실행 안내](Assignment6/concurrency/README.md) · [과제 보고서](Assignment6/concurrency/report.md) · [제출물 구성](Assignment6/concurrency/SUBMISSION.md)
- [제출 tar.gz](Assignment6/assignment6_submission.tar.gz) · [압축 해제 후 검증 기록](Assignment6/submission-validation.txt) · [futex 후속 측정](Assignment6/local-evidence/README.md)

## 과제 결과

- bounded queue의 blocking put/get, monotonic timeout, close 후 drain과 전체 대기자 깨우기를 구현했다.
- album service의 deep-copy snapshot과 UI 소유권 기반 handoff를 구현했다.
- sink 잠금 순서, 재진입 callback, rwlock 모드, RCU grace period를 적용해 네 교착 상태 사례를 수정했다.
- 일반/ThreadSanitizer 큐 테스트와 네 사례의 lockcheck·TSan·stress·parallel·speed 검증을 통과했다.
- 용량·깨움 방식·UI 모드를 3회씩 측정하고, 잘못된 수정과 writer-preferring rwlock의 반례를 보관했다.

기존 과제는 Linux 7.2.11-arch1-1, x86-64, glibc 2.44, GCC 16.2.1, 4 KiB 페이지 환경에서 측정했다. 논리 CPU 18개 중 CPU 0·1로 실험 프로세스를 제한했다. 제출 tar의 파일 257개가 풀린 사본과 일치했으며 SHA-256 manifest를 검증했다.

`bpftrace`와 `perf`가 필요한 futex 호출자별 집계, lockwait, perf sched timehist는 미측정이다. [계측 스크립트](Assignment6/concurrency/analysis/trace.sh)
