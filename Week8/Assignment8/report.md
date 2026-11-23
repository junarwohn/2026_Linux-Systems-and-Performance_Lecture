# Assignment 8 — 페이지 fault와 커널 allocator

Arch Linux 7.3.4-arch1-2, x86-64, 4 KiB base page와 btrfs에서 측정했다. 시스템 THP는 always였고 익명 대상 mapping은 각각 MADV_NOHUGEPAGE를 검사했다. BPF 오브젝트는 clang 23.1.5로 컴파일됐다. libbpf 1.8.0에서 로드가 EPERM, tracefs kmem과 slabinfo 읽기가 EACCES로 실패했다. [환경 및 BPF 상태](evidence/bpf-preflight.txt), [allocator 권한](evidence/allocator-permissions.txt)

## Level 1 — faultwatch

`faultwatch`는 stat의 마지막 닫는 괄호 뒤에서 fault 수를 읽고, status와 smaps를 읽는다. pagemap은 범위 내 실제 base page별로 present/swapped/neither를 세고 exclusive를 별도 표시한다. 이 호스트에서 PFN은 unavailable이었다. 명령 모드의 짧은 프로세스는 sampling delay 전에 끝나도 wait4 결과를 출력했다. [짧은 child](evidence/wait4-short.txt), [interval](evidence/command-interval.txt)

페이지별 observer checkpoint 차이의 3회 중앙값이다. 페이지 수는 접근한 논리 base pages다. 익명 예상값은 처음 읽거나 쓸 때 PTE가 하나씩 채워진다고 예측했다. 파일 cold의 정확한 minor는 사전에 결정할 수 없어 캐시 정책별 가설을 기록했다.

| Phase | Pages | 예상 minor | 실측 minor/major | ΔVmRSS KiB | ΔVmPTE KiB |
|---|---:|---|---:|---:|---:|
| Demand first write | 16,384 | 16,384 | 16,384/0 | 65,536 | 128 |
| Demand every-byte overwrite | 16,384 | 0 | 0/0 | 0 | 0 |
| Anonymous first read | 16,384 | 16,384 | 16,384/0 | 0 | 128 |
| Write after read | 16,384 | 16,384 | 16,384/0 | 65,536 | 0 |
| Child COW write | 4,096 | 4,096 | 4,096/0 | 0 | 0 |
| Default cold 요청 | 16,384 | readahead에 따라 감소 | 402/1 | 65,536 | 128 |
| Default warm remap | 16,384 | fault-around로 감소 | 403/0 | 65,536 | 128 |
| RANDOM cold 요청 | 16,384 | cold일 때 대부분 major | 0/16,384 | 65,536 | 128 |
| RANDOM warm remap | 16,384 | fault-around로 감소 | 1,024/0 | 65,536 | 128 |
| Dense 1 MiB | 256 | 256 | 256/0 | 1,024 | 4 |
| Sparse 512 MiB, 2 MiB stride | 256 | 256 | 256/0 | 1,024 | 1,024 |

- **가상 공간과 거주량.** 읽기 전용 first-touch에서 pagemap에는 16,384 present가 생겼고 ΔRSS는 0이었다. 공용 zero page가 PTE의 present를 충족할 수 있다는 해석과 일치한다. 이후 쓰기에서 65,536 KiB RSS와 16,384 exclusive가 나타났다. PFN을 읽을 수 없어 동일 zero frame인지는 직접 대조하지 못했다. 이 결과는 Week 7의 VMA와 거주량이 독립적으로 바뀐다는 관찰을 뒷받침한다. [read/write run 1](evidence/readwrite-1.json)
- **COW.** fork 직후 두 프로세스의 대상 exclusive는 0이었다. child가 16 MiB를 쓰고 나서 양쪽 exclusive는 각각 4,096이었고 child PSS는 32,842 → 41,036 KiB였다. 대상의 각 RSS는 그대로였지만 공유분의 비례 몫이 독점 몫으로 바뀌었다. exclusive bit는 시각과 mapping scope에 제한되며 frame 번호나 전체 프로세스 공유 관계를 보장하지 않는다. [COW run 1](evidence/cow-1.json)
- **파일.** cold 요청에는 실제 64 MiB를 쓴 disk-backed 파일의 per-file DONTNEED를 사용했다. dedicated VM의 global drop_caches 조건은 미실행이다. RANDOM의 16,384 major와 NORMAL의 1 major는 같은 크기에서도 readahead 차이가 크다는 실측이다. warm remap은 cache를 재사용하되 새 PTE를 채우므로 minor가 남았다. [default run 1](evidence/file-default-1.json), [RANDOM run 1](evidence/file-random-1.json)
- **페이지 테이블.** 4 KiB 테이블에 8 B PTE가 512개 들어가고 하나의 PTE 페이지는 2 MiB를 다룬다. Dense는 실제 주소에서 하나의 2 MiB 구간 안에 있었다. Sparse는 256개 2 MiB 구간에 하나씩 써서 PTE 페이지 256개, 1,024 KiB가 필요했다. 한 반복에는 상위 테이블 경계로 추가 4 KiB가 관찰됐다. [dense](evidence/dense-1.json), [sparse](evidence/sparse-2.json)

## Level 2 — kmemwatch

`buddyinfo`, `zoneinfo`, `pagetypeinfo`, `slabinfo`, `meminfo`를 읽는다. `buddy`의 order n 블록은 2ⁿ × 4,096 B의 정렬된 연속 물리 페이지를 뜻한다. order 3은 32,768 B다. 올바른 buddy끼리 free 상태이며 같은 zone/order 내에서 allocator의 조건을 만족하면 위 order로 병합될 수 있다. [baseline](evidence/allocator-baseline.jsonl), [diff](evidence/allocator-diff.jsonl)

| 같은 프로세스 연결 실험 1 | minor 합 | VmRSS KiB | 시스템 buddy free KiB | order ≥9 free KiB |
|---|---:|---:|---:|---:|
| mmap 직후 | 67 | 1,888 | 3,649,568 | 3,536,896 |
| first touch | 32,835 | 132,960 | 3,547,996 | 3,475,456 |
| 교번 페이지 해제 | 32,835 | 67,424 | 3,554,404 | 3,475,456 |
| munmap | 32,835 | 1,888 | 3,628,504 | 3,479,552 |

이 환경의 order 9는 2 MiB다. 첫 쓰기에서 RSS는 131,072 KiB 증가했으나 전역 buddy는 101,572 KiB 감소했다. 다른 프로세스, per-CPU page list, PTE/allocator overhead와 비원자적 읽기가 개입한다. 교번 가상 주소 해제는 실제 물리 이웃 관계를 입증하지 못한다. 실제 3회 모두 RSS는 65,536 KiB 감소했지만 전역 free는 두 번 증가하고 한 번 감소했다. [run 1](evidence/partial-1.json), [run 2](evidence/partial-2.json), [run 3](evidence/partial-3.json)

baseline의 Normal zone은 free 584,423 pages로 min/low/high 15,316/19,145/22,974보다 높았다. eligible zone free가 low 부근으로 떨어지면 kswapd를 통해 회수를 시도할 수 있다. Slab 401,004 KiB = SReclaimable 202,640 + SUnreclaim 198,364 KiB였고, PageTables 76,516 KiB와 KernelStack 28,356 KiB도 프로세스의 사용자 RSS와 다른 커널 메모리다. 100개 sleep 프로세스가 살아 있을 때 PageTables는 1회차 76,540 → 81,648 KiB였다. 10,000개 파일 작성·stat 때 Slab은 403,528 → 415,004 KiB였고, 삭제 후 405,128 KiB였다. 개별 객체가 사라져도 slab이 부분 사용이면 커널 페이지가 당장 반환되지는 않는다. [프로세스 실험](evidence/process-1.json), [메타데이터 실험](evidence/metadata-1.json)

`task_struct`, `dentry`, `anon_vma`, `kmalloc-64`, `kmalloc-1k`는 이 계정에서 sysfs geometry와 slab count 모두 unavailable이었다. 공통 parser는 alias를 canonical cache로 해석하고, 추정 cache footprint와 object payload를 구분한다. 전자는 `total_slabs × pages_per_slab × page_size`이며, 그 합계는 fallback과 allocator geometry 때문에 system Slab과 다를 수 있다. [geometry 출력](evidence/allocator-baseline.jsonl)

## Level 3 — myslabtop

네 raw tracepoint의 cumulative counters를 atomic increment하고 사용자 공간에서 interval delta를 계산한다. cache view는 cache pointer의 이름을 CO-RE로 읽고, kmalloc view는 requested bytes를 키로 집계한다. 성공 alloc과 non-sentinel free만 표시 rate에 포함하고 raw totals, map 삽입 오류, 이름 읽기 실패와 잘림을 별도로 남긴다. map scan 구간에는 작은 시간 skew가 가능하다. 동시 map을 읽은 뒤 초기화하는 방식은 쓰지 않았다. [BPF 코드](level3/slab_events.bpf.c), [loader](level3/myslabtop)

큰 cache와 event rate는 서로 다른 측정이다. high churn에서도 재사용이 빠르면 footprint가 일정할 수 있다. named cache allocation 후 kfree처럼 다른 hook으로 해제하는 경로가 있고 attachment 전 객체도 있으므로 rate 차를 live object나 leak으로 바꿀 수 없다. requested/allocated 차는 이 이벤트의 allocation rounding이다. 전체 slab fragmentation, page allocator, vmalloc을 이 값으로 설명할 수 없다. 짧은 interval은 순간 burst와 overhead 양쪽에 더 민감하므로 동일 workload·기간에서 비교해야 한다.

이 환경에서 네 프로그램 attach, per-cache/size 시계열, slabtop 대조, 4-hook reference totals 및 0.1/2초 비교는 미검증이다. paced workload와 두 view 실행기를 작성했다. [권한 실패 원문](evidence/bpf-preflight.txt), [workload](workloads/phase_activity.py)

Codex를 도구 구현과 관측 분석에 사용했다. 첫 read의 present 증가를 RSS 증가로 간주하는 해석을 pagemap과 smaps를 비교해 수정했다.
