# Assignment 9 — 메모리 압력과 회수

`memhog`, `psimon`, `zoneinfo`를 구현했다. Linux 7.3.4-arch1-2, x86-64, 18 CPU, 4 KiB 페이지에서 빌드하고 실행했다. 호스트는 btrfs와 4 GiB zram을 사용했다. 전역 swap/sysctl 권한과 별도 WSL 디스크 swap이 필요한 채점 실험은 미측정이다. 아래 결과는 384 MiB의 위임된 임시 부모 안에서 수행한 로컬 비교다. [환경](evidence/preflight.txt), [빌드·실행](README.md)

## Level 1 — 계측과 회수

`memhog anon`은 런타임 페이지 크기만큼 건너뛰며 실제로 쓰고, 초기 touch에만 속도 제한을 적용한다. `file`은 1 MiB buffered read와 rewind를 사용한다. `probe`는 별도 프로세스에서 mmap과 페이지 쓰기까지만 잰다. 각 raw sample을 기록하고 nearest-rank로 p50/p99/max를 계산한다. [구현](src/memhog.c), [반복 실행](analysis/local_runs.py)

| Zone | Free | min | low | high |
|---|---:|---:|---:|---:|
| DMA | 3,328 | 16 | 20 | 24 |
| DMA32 | 373,609 | 1,562 | 1,952 | 2,342 |
| Normal | 669,098 | 15,316 | 19,145 | 22,974 |
| Movable / Device | 0 | 32 | 32 | 32 |

단위는 페이지다. `min_free_kbytes=67584`, `watermark_scale_factor=10`, `watermark_boost_factor=15000`, `swappiness=60`이었다. 이 스냅샷에서 세 populated zone의 watermark 간격은 각 zone 내부에서 같았다. boost, 할당 order, fallback과 reserve도 가용성에 영향을 준다. 전역 MemFree는 4,184,140 KiB, MemAvailable은 10,240,912 KiB였다. Linux가 재사용 가능한 캐시를 보유하므로 후자가 headroom을 판단할 때 유용하다.

낮은 watermark 부근의 zone 압력은 kswapd를 깨울 수 있고, 회수가 따라가면 상단 watermark 방향으로 회복한다. pressure가 큰 할당 경로에는 direct reclaim이 들어가므로 호출 지연에 회수 작업이 포함된다. 검증하려면 eligible zone의 free/threshold와 같은 시각의 `pgscan_kswapd`, `pgscan_direct`, `allocstall_*`를 확인한다. 샘플 사이의 정확한 최초 crossing은 알 수 없다.

로컬 healthy 3회는 그룹 kswapd/direct scan이 모두 0이었다. 파일 캐시가 `memory.high`를 넘는 조건에서 그룹 direct scan 중앙값은 25,777 pages/s, kswapd는 0 pages/s였다. 48 MiB 유지 + 32 MiB burst 12회의 작은 pilot도 reclaim 없이 완료됐다. 전역 Level 1 A/B/C 3회, 멀티 zone watermark onset 및 kswapd CPU 증거는 미수집이다. [전체 실행별 CSV](evidence/summary.csv)

## Level 2 — CPU, 파일 refault, swap

healthy/cpu/cache는 실제로 쓴 96 MiB 파일을 네 번 읽었고 128 MiB/s로 rate cap을 고정했다. CPU 조건은 동일 CPU의 busy loop, cache 조건은 56 MiB `memory.high`를 추가했다. swap 조건은 120 MiB의 모든 바이트를 xorshift64*로 채우고, seed 42로 순서를 섞어 각 페이지를 한 번 읽었다. 2초 hold 중 `memory.reclaim`으로 swap out을 준비했다.

| 조건, 3회 중앙값 | 처리량 MiB/s 또는 단계별 초 | 그룹 CPU some % | 메모리 some/full % | file refault pages/s | swap in/out pages/s | Probe p50/p99 µs |
|---|---|---:|---:|---:|---:|---:|
| Healthy | 127.978 MiB/s | 1.130 | 0/0 | 0 | 0/0 | 50.186/80.649 |
| CPU | 127.984 MiB/s | 8.461 | 0/0 | 0 | 0/0 | 51.559/88.624 |
| File pressure | 127.982 MiB/s | 1.450 | 0.279/0.279 | 22,539 | 0/0 | 53.138/105.871 |
| Zram retouch | fill 0.063 s, retouch 0.061 s | 0.031 | 0.197/0.197 | 0 | 7,630/7,633 | 52.817/79.070 |

File pressure p99 범위는 75.294–107.619 µs, swap p99 범위는 78.285–95.730 µs이다. 샘플 수, 최대값, 정확한 기간과 실행별 범위는 CSV에 있다. Probe는 그룹 밖에서 잰 별도 할당 지연이다. Cache 1은 9,116 µs / 약 3.269 s × 100 / 1,000,000 ≈ 0.279%였다. 모니터 구간은 부하 시작 직전 준비를 포함하고, probe는 부하 timestamp에 맞춰 잘랐다. 파일 처리량이 같아도 고정 rate cap 아래에서 기다린 자원은 서로 달랐다.

`psimon`의 잘못된 1,000 µs **window**는 인자 검증에서 거부됐다. 50,000 µs **stall** / 2 s trigger 등록에는 성공했으나 짧은 case에서 알림은 0이었다. 1,000 µs / 2 s 설정으로 cache를 24회 읽고 속도 상한을 256 MiB/s로 높인 후, 등록 뒤 약 8.03초에 `POLLPRI` 한 번을 받았다. 짧은 zram follow-up에서는 같은 낮은 threshold로 알림이 없었다. polling은 일정 간격에 누적값을 읽고, trigger는 열린 fd로 임계값 통과를 통보한다. [잘못된 인자](evidence/invalid-trigger.txt), [확장 trigger](evidence/cache-trigger-1000-extended/trigger.txt), [swap follow-up](evidence/swap-trigger-1000/trigger.txt)

파일 refault는 다시 읽힌 파일 페이지를 드러낸다. 익명 swap-in은 `pswpin`을 확인해야 하고 `pswpout`만 증가하면 아직 읽기 지연을 입증할 수 없다. 압축 메모리 swap은 디스크 왕복을 피하는 대신 RAM과 CPU를 사용한다. 디스크 swap 실험 및 zram 간 정량 비교는 미측정이다.

## Level 3 — 두 설정의 비교 설계

| 설정 | 구현한 부하 | 예측 및 비용 | 검증 지표 |
|---|---|---|---|
| watermark_scale_factor | 고정 익명 working set과 일정 간격의 mmap/touch/release burst | 간격을 늘리면 kswapd에 선행 여유가 생겨 direct stall이 줄 수 있고 유지되는 free reserve가 커진다. | 영역별 free/low/high, kswapd/direct scan, allocstall, probe p99 |
| swappiness | 압축하기 어려운 seeded anonymous hold와 ext4 buffered reader | swappiness가 높으면 파일 페이지 보존 가능성이 커지고 anon을 다시 읽을 때 swap 비용이 늘 수 있다. | refault_file/anon, swap in/out, reader MiB/s, retouch 초, PSI 및 probe |

`analysis/tune_vm.py`는 수치를 고정하고 설정별 원래값/변경값을 세 번씩 교대한다. 적용 전후 readback, workload/zone snapshot과 종료 시 원래값 복구를 기록하도록 작성했다. 여기서는 작은 burst pilot과 도구를 검증했다. 관리자 권한을 가진 지정 VM이 필요한 12회 전역 비교, 재설계 후 비교 및 제품 튜닝 효과는 미검증이다. [비교 코드](analysis/tune_vm.py), [pilot 원문](evidence/burst-1/manifest.json)

Codex를 구현과 분석에 사용했다. trigger를 등록한 사실과 실제 알림을 따로 기록했고, 작은 부하의 0건을 기능 실패로 해석하는 대신 지속 시간을 늘려 실제 이벤트를 검증했다.
