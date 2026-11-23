# Lab 10 — 메모리 문제 진단

Linux 7.3.4-arch1-2, x86-64, 4 KiB 페이지에서 제공 프로그램을 빌드했다. 384 MiB로 제한한 위임 cgroup에서 부하만 별도 그룹에 놓고, 관측기는 `observer`에 유지했다. 각 단계의 `memory.current/peak/stat/events/pressure`, `maps`, `smaps_rollup`과 단조 시각을 보관했다. B의 커널 메시지는 읽을 수 있는 journal에서 경로와 PID로 대조했다. [실행 기록](evidence/console.txt)

```bash
make
systemd-run --user --wait --pipe -p WorkingDirectory="$PWD" \
  -p MemoryMax=384M -p MemorySwapMax=0 -p RuntimeMaxSec=65 \
  -p Delegate=memory -p DelegateSubgroup=observer python3 run_delegated.py
```

## A — 만료된 캐시 객체의 수명

| 단계 | buggy current MiB | buggy PSS KiB | fixed current MiB | fixed PSS KiB |
|---|---:|---:|---:|---:|
| baseline | 0.25 | 131 | 0.25 | 135 |
| 4 batches | 32.75 | 32,935 | 0.27 | 267 |
| 8 batches | 64.75 | 65,735 | 0.54 | 267 |
| 12 batches | 97.00 | 98,535 | 0.57 | 267 |

PSS 증가가 그룹 사용량 증가를 거의 설명했다. `maps`에서 익명 heap을 확인한 뒤 할당 위치를 추적했다. 이 환경에서는 heaptrack 대신 Week 10 과제의 `alloctrace.so`를 사용했다. 종료 trace에서 `lab10_a+0x14b4`의 6,144개 객체, 총 100,663,296 B가 남았다. `addr2line -f -e lab10_a 0x14b4`가 `cache_store_add`를 가리켰다. 만료 경로가 포인터를 비우기 전에 `free`를 호출한 수정판에서는 같은 위치의 종료 live 객체가 사라졌다. 고정판의 일시 peak는 8.34 MiB로, 한 batch를 처리하는 동안 필요한 메모리가 포함됐다. SIGUSR1 덤프는 다음 allocator 호출에서 출력되므로 stage 시각과 조금 어긋날 수 있다. [buggy raw](evidence/A-buggy.json), [fixed raw](evidence/A-fixed.json), [buggy alloc](evidence/A-buggy-traced.txt), [fixed alloc](evidence/A-fixed-traced.txt)

## B — 재생의 순간 피크

96 MiB 제한에서는 idle current 48.5 MiB 이후 추가 80 MiB를 쓰는 중 종료 코드 -9를 받았다. 그룹 peak는 96.00 MiB, 이벤트는 `max +38 / oom +1 / oom_kill +1`이었다. 해당 커널 보고서에는 `CONSTRAINT_MEMCG`와 `lab10_b`가 기록됐다. 160 MiB 제한에서 같은 부하를 두 번 재생했더니 current가 48.5 → 128.75 → 48.75 → 128.75 → 48.75 MiB로 변했고 OOM kill 증분은 0이었다. 해제 뒤 기준선으로 돌아오는 두 반복은 A와 다른 일시 피크를 보여 준다. 160 MiB cap은 관측한 128.75 MiB를 담으면서 384 MiB 부모 예산 안에 있다. [96 MiB](evidence/B-96MiB.json), [160 MiB](evidence/B-160MiB.json), [kernel OOM](evidence/B-96MiB-kernel-oom.log)

## C — 같은 부모에서 서비스 두 개의 경쟁

부모 budget을 192 MiB로 고정했다. 동일 시계에서 단계 첫 표본과 마지막 표본 사이의 PSI와 기존 서비스 처리 시간 중앙값을 구했다.

| 단계 | 기존/신규 마지막 current MiB | 부모 some/full % | 기존 순회 work_ms 중앙값 | 신규 high 증분 |
|---|---:|---:|---:|---:|
| 기존만 | 96/0 | 0/0 | 0.699 | 0 |
| 두 서비스 | 59/132 | 7.977/7.977 | 38.044 | 0 |
| 신규 high=48 MiB | 96/47 | 3.784/3.784 | 0.408 | 22,858 |

자식들의 `memory.max=max`라도 부모의 한도에서 합산된 파일 캐시가 회수된다. 양쪽 매핑은 프로그램이 생성해 unlink한 sparse 파일이며, 순회 시간은 영상 처리나 실제 파일 디스크 대역폭을 뜻하지 않는다. 새 서비스에 high를 적용한 뒤 새 서비스가 스로틀·회수를 부담했고 기존 서비스의 resident pages와 순회 시간이 회복됐다. OOM kill은 세 단계 모두 0이었다. high는 목표 경계이고, hard cap처럼 매 표본을 48 MiB 이내로 고정하지 않는다. [그룹·매핑 원문](evidence/C.json), [기존 시간](evidence/C-existing.txt), [신규 시간](evidence/C-new.txt)

Codex를 실험 재현과 분석에 사용했다. 처음에는 dmesg만 확인했으나 권한 오류가 발생해 커널 journal을 조회했고, cgroup 통계와 실제 커널 victim PID를 대조했다.
