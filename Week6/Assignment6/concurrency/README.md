# Assignment 6 — Concurrency

bounded queue, album service의 UI 잠금 경로 단축, 네 교착상태 사례의 수정을 구현했다.
Level 1~3 구현과 제공 기능 검증은 완료했다. **bpftrace·perf를 사용하는 필수 계측은 미완료**다.
아래 결과는 `evidence/`에 보관된 실행 로그를 기준으로 한다.

## 구현 내용

| 단계 | 파일 | 구현 및 핵심 동작 |
|---|---|---|
| Level 1 | `src/bq.c` | mutex 아래 큐 상태 보호, `BQ_WHILE` 재검사, put/get, 한 번 계산한 CLOCK_MONOTONIC deadline, close 후 drain 및 전체 대기자 깨우기 |
| Level 2 | `src/album.c` | album 구조체와 각 title 문자열의 deep copy, 부분 할당 실패 및 snapshot 해제 처리 |
| Level 2 | `src/albumsvc.c` | snapshot 저장은 잠금 밖에서 수행. handoff는 UI만 album을 수정하고 프레임당 최대 64개 update를 적용하며 snapshot을 saver로 전달 |
| Level 3 / case1 | `src/sink.c` | sink 인덱스 순서로 mutex 획득 |
| Level 3 / case2 | `src/bus.c` | 잠금 아래 구독자 배열을 복사한 뒤 잠금 밖에서 callback 실행 |
| Level 3 / case3 | `src/catalog.c` | 경로별 read/write 모드 적용, 쓰는 경로는 apps → perms 순서 유지 |
| Level 3 / case4 | `src/config.c` | reader의 단일 RCU pointer load, 새 config publish 후 잠금 밖에서 grace period 대기 및 old config 해제 |

제공된 인터페이스·테스트·driver·RCU·tools·참조 library는 유지했다.
Level 3 수정은 TODO 영역 안에서 수행했고 필수 `svc_work`를 보존했다.
참조 `devsvc_ref`는 속도 비교에 사용했다. 최종 album service는 직접 구현한 큐를 사용한다.

## 빌드와 실행

이 디렉터리에서 실행한다. C compiler, GNU make, pthread 및 TSan runtime이 필요하다.

```sh
make -j4
bash tools/l1_check.sh
for c in 1 2 3 4; do bash tools/l3_check.sh "$c"; done

./bin/bqtest -p 2 -c 2 -n 100000 -q 4
./bin/albumsvc -m snapshot -d 10
./bin/albumsvc -m handoff -d 10
```

전체 비교 실험 및 보고서 표 재생성 명령은 다음과 같다. 측정 드라이버와 제공 검사 스크립트는
같은 이름의 결과를 덮어쓰므로 재실행 전 기존 `evidence/`와 `logs/`를 보관한다.

```sh
taskset -c 0,1 python3 analysis/measure.py
python3 analysis/report.py
```

시간은 일반 빌드로 측정했다. TSan과 `LD_PRELOAD=./bin/lockcheck.so` 진단은 별도 실행이다.
lockcheck를 TSan 빌드에 함께 로드하지 않는다. `l3_run.sh`는 TSan 자체의 잠금 순서 진단을
켜고, `l3_check.sh`는 이를 끈 뒤 data race와 mode-aware lockcheck 결과를 구분한다.

## 검증 결과

| 검증 | 보관된 결과 | 근거 |
|---|---|---|
| 큐 close·timeout·stress·broadcast | 일반/TSan 빌드 모두 PASS | [l1-check.txt](evidence/l1-check.txt) |
| 교착 수정 case1~4 | 각각 lockcheck·TSan·stress·parallel·speed 전체 PASS | [case1](evidence/l3check-1.txt), [case2](evidence/l3check-2.txt), [case3](evidence/l3check-3.txt), [case4](evidence/l3check-4.txt) |
| `BUG_IF` 큐 | 10초 내 미완료로 실패 관측 | [l1-bugif.txt](evidence/l1-bugif.txt) |
| 잘못된 수정 w2/w3/w4a/w4b | callback 누락·교착·메모리 경쟁 등의 실패 확인 | [w2](evidence/w2-check.txt), [w3](evidence/w3-check.txt), [w4a](evidence/w4a-check.txt), [w4b](evidence/w4b-check.txt) |
| writer-preferring rwlock | case3 HANG 관측 | [진단 로그](evidence/l3-3-writer-lockcheck.txt) |

UI 비교는 기본 부하, 각 10초 실행의 3회 중앙값이다. 시간 단위는 µs다.

| 모드 | miss | frame latency p99 | UI lock/apply p99 | saver lock hold p50 |
|---|---:|---:|---:|---:|
| lock | 25 | 25092 | 23026 | 23159 |
| snapshot | 0 | 2308 | 0 | 13 |
| handoff | 0 | 2615 | 8 | 해당 없음 |

원문은 [lock](evidence/l2-lock-r1.txt), [snapshot](evidence/l2-snapshot-r1.txt),
[handoff](evidence/l2-handoff-r1.txt)과 각 r2/r3 로그다.
snapshot은 긴 저장 작업을 잠금 밖으로 옮겨 lock hold를 줄인다. handoff는 UI가 큐 처리와
복사 비용을 부담한다. UI lock의 0µs 표시는 µs 단위 계측 결과이며 비용이 없다는 뜻은 아니다.
CPU 부하·저장 지연 비교와 상세 해석은 [보고서](report.md)에 있다.

## 실행 환경과 미완료 계측

실측 환경은 Linux 7.2.11-arch1-1, x86-64, glibc 2.44, GCC 16.2.1, 페이지 4096 B다.
논리 CPU 18개인 머신에서 측정 프로세스를 CPU 0·1로 제한했다. 이는 과제의 WSL2 kernel 6.6 /
vCPU 2대와 동일한 환경은 아니다. 다른 작업의 영향을 완전히 배제한 성능 측정도 아니다.

`bpftrace`와 `perf`가 없어 futex의 op/호출자별 집계, lockwait uprobes,
perf sched timehist는 측정하지 못했다. 근거는 [bpftrace 상태](evidence/bpftrace-availability.txt)와
[perf 상태](evidence/perf-availability.txt)에 있다. 해당 값을 0 또는 PASS로 해석하지 않았다.
별도 strace 로그는 수집했으며 보고서에서 보조 근거로 사용했다.

두 도구와 필요한 추적 권한을 갖춘 환경에서 다음 명령으로 남은 계측을 수행할 수 있다.
제공된 `l2_run.sh`는 sudo를 요청한다. 추적 후 결과 해석과 보고서 갱신이 필요하다.

```sh
bash analysis/trace.sh
```

## 보고서와 제출 자료

| 경로 | 내용 |
|---|---|
| [report.md](report.md) | 예측·반복 측정 표·5.3/6.3/7.3 문항 분석·AI 사용 내역 |
| [SUBMISSION.md](SUBMISSION.md) | 환경·출력 단위·재현 절차·제공 코드 경계 |
| [evidence/](evidence/) | 정확한 명령·PID·커널·소요시간을 포함한 원문 로그, UI CSV, 별도 strace |
| [evidence/baseline/](evidence/baseline/) | 수정 전 원본 사례 소스 |
| [evidence/variants/](evidence/variants/) | 의도적으로 잘못 고친 별도 소스와 patch |
| [제출 묶음](../assignment6_submission.tar.gz) | 소스·문서·실험 근거·체크섬 |
| [압축 해제 검증 기록](../submission-validation.txt) | 별도 디렉터리의 체크섬·빌드·실행 검증 결과 |

Codex를 사용해 구현·측정·분석을 작성했다. 반례로 검증한 제안과 관측 한계는 보고서에 명시했다.
