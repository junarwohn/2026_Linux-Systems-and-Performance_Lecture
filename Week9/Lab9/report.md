# Lab 9 — 메모리 압력과 회수

Linux 7.3.4-arch1-2, x86-64, 4 KiB 페이지, btrfs, zram에서 직접 측정했다. 제공 C 프로그램을 `make`로 빌드했다. 사용자 systemd가 위임한 384 MiB 부모를 만들고, 관측기는 `observer`, 부하는 별도 하위 cgroup에 두었다. 부하를 같은 CPU에 고정하고 각 조건에서 4초씩 실행했다. 파일 부하는 실제로 쓴 128 MiB 파일을 사용했다.

```bash
make
bin/psi_watch read /proc/pressure/memory 2
systemd-run --user --wait --pipe -p WorkingDirectory="$PWD" \
  -p MemoryMax=384M -p MemorySwapMax=128M -p RuntimeMaxSec=100 \
  -p Delegate=memory -p DelegateSubgroup=observer python3 tools/delegated.py
```

## PSI와 처리량

`total`은 누적 stall 시간(µs)이다. 두 표본 사이의 차이를 구간의 µs로 나누고 100을 곱했다. `some`은 하나 이상의 실행 가능한 작업이 자원 때문에 기다린 구간을, 메모리 `full`은 해당 범위의 비유휴 작업이 모두 막힌 구간을 센다. 같은 시간을 각각 포함할 수 있다. `avg10`은 별도의 시간 평균이므로 이 표의 구간 비율과 달라진다. [시스템 PSI](evidence/system-psi.txt), [실행 기록](evidence/console.txt)

| 조건 | 파일 처리량 MiB/s | 그룹 CPU some % | 그룹 메모리 some/full % | 해석 |
|---|---:|---:|---:|---|
| normal | 7,727 | 3.67 | 0/0 | 파일 작업이 주로 캐시를 재사용했다. |
| cpu | 3,113 | 99.85 | 0/0 | 한 CPU의 실행 경쟁만 추가해도 처리량이 크게 떨어졌다. |
| memory | 2,747 | 3.95 | 3.44/3.44 | 파일 작업이 제한 그룹에서 회수와 재접근을 반복했다. |
| mixed | 2,031 | 70.57 | 2.23/1.94 | 파일 작업이 stall 중일 때 CPU 부하가 진행한 구간이 있었다. |

파일 처리량은 worker의 `bytes / seconds / 1048576`이다. 원시 PSI와 전역·그룹 counter를 JSON에 따로 저장했다. `memory`, `file-refault`, `high`에서 some 150,000 µs / 2 s trigger 알림을 각각 한 번 관측했다. 알림이 없는 짧은 구간에서도 측정한 stall이 임계값보다 작을 수 있다. [normal](evidence/normal.json), [cpu](evidence/cpu.json), [memory](evidence/memory.json), [mixed](evidence/mixed.json), [trigger](evidence/memory-1.txt)

## 회수, 재접근과 제한

- `file-refault`는 그룹 `workingset_refault_file` +2,828,800, 전역 `pswpin` 0이었다. `swap-in`은 `memory.reclaim` 후 실제 swap charge 83,980,288 B를 확인하고 재접근했다. 그 구간에 `pswpin`이 20,497페이지 늘고 `pswpout` 증분은 0이었다. swap-in 구간의 그룹 memory some/full은 0.110/0.110%였다. `pswpout`만 관측하면 이후 읽기가 있었는지 알 수 없고, I/O PSI가 함께 증가하면 scope 및 실제 읽기 요청을 대조한다. [파일 refault](evidence/file-refault.json), [swap-in](evidence/swap-in.json)
- `high=64` MiB 조건에서 high 이벤트는 21,729회 증가했고 OOM kill은 0이었다. `max=96` MiB에 128 MiB first-touch를 넣은 조건은 `max +37, oom +1, oom_kill +1`과 종료 코드 -9를 관측했다. 실측 kernel journal에서 `CONSTRAINT_MEMCG`와 그룹 경로가 일치했다. high 초과는 회수·스로틀의 신호이고, max 초과도 회수에 성공하면 OOM으로 이어지지 않는다. [high](evidence/high.json), [max](evidence/max.json), [kernel journal](evidence/max-kernel-oom.log)
- 이 호스트에서는 전역 background/global-direct 부하를 실행하지 않았다. 파일 제한 그룹의 `pgscan_direct` 증가와 전역 watermark 실험은 범위가 다르다. Background 회수는 kswapd counter로, 할당 호출 경로의 회수는 direct scan과 allocstall의 변화로 확인해야 한다. 두 카운터가 동시에 증가할 수 있고, kswapd가 충분히 따라가면 memory PSI가 낮게 유지될 수 있다.

Codex를 실행 자동화와 보고서 작성에 사용했다. AI가 제안한 root launcher 대신 메모리 컨트롤러를 위임받은 임시 user service를 사용했고, 실제 그룹 이벤트와 kernel journal을 대조해 OOM 범위를 검증했다.
