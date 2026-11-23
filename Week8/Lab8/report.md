# Lab 8 — Page fault와 커널 객체

Linux 7.3.4-arch1-2, 4 KiB 페이지에서 `make`로 세 프로그램을 빌드했다. 각 단계는 Enter 대기 상태에서 관찰했다. 파일 조건은 btrfs에 실제로 쓴 16 MiB 파일을 사용했다.

## 텍스트와 COW

`./lab1 text`의 함수는 `r-xp` VMA에 있었고 해당 쓰기를 실행한 자식은 SIGSEGV(11)를 받았다. `./lab1 cow`의 자식은 4,096페이지 첫 쓰기의 minor 4,096/major 0, 다시 쓰기의 minor/major 0을 기록했다. 부모 값 7과 자식 최종값 10이 분리됐다. COW는 private VMA에서 허용된 write다. fork 직후 해당 PTE에 fault가 나면 커널이 새 페이지를 할당하고 복사해 자식 PTE를 업데이트할 수 있다. RX VMA의 write는 VMA 접근 권한 위반이다. maps와 fault 수에서 하드웨어 fault error code를 직접 얻을 수 없다. [텍스트](evidence/text.txt), [COW](evidence/cow.txt)

| 익명 단계 | VmSize KiB | Rss KiB |
|---|---:|---:|
| baseline | 2,944 | 1,888 |
| mmap 직후 | 35,712 | 1,892 |
| 첫 쓰기 후 | 35,712 | 34,728 |
| 다시 쓰기 후 | 35,712 | 34,728 |
| munmap 후 | 2,944 | 1,960 |

첫 쓰기 11.871 ms, minor 8,192였고 다시 쓰기는 0.174 ms, minor 0이었다. 이 차이는 첫 쓰기의 페이지 할당과 PTE 채움으로 설명한다. 4 KiB 수준의 stage 간 차이에는 관측 코드의 접근이 포함될 수 있다. [익명 원문](evidence/anon.txt)

| 첫 파일 접근 조건 | minor | major | 시간 ms |
|---|---:|---:|---:|
| cold 요청, RANDOM | 0 | 4,096 | 136.604 |
| warm, RANDOM | 256 | 0 | 0.393 |
| cold 요청, NORMAL | 211 | 1 | 7.104 |

파일별 DONTNEED는 캐시 퇴거 요청이고, 여기서는 실제 첫 read의 major fault 차이를 확인했다. NORMAL은 readahead와 fault-around로 fault당 여러 페이지를 충족할 수 있었다. warm도 새 mapping의 PTE를 채울 때 fault가 발생한다. 이미 채운 동일 mapping의 reread는 이 실행에서 모두 0/0이었다. [cold random](evidence/cold-random.txt), [warm random](evidence/warm-random.txt), [cold normal](evidence/cold-normal.txt)

`./lab3 task`에서 살아 있는 자식은 0 → 64 → 0이 됐고 부모 maps는 25줄이었다. `./lab3 vma`에서는 25 → 매핑 추가 후 26 → 교번 mprotect 후 2,073 → unmap 후 25였다. 한 프로세스 안에서도 권한이 달라지는 경계에서 VMA를 나눌 수 있다. 커널이 task/VMA 객체를 해제해도 slab에는 빈 객체 공간과 부분 사용 slab이 남을 수 있다. `/proc/slabinfo`는 권한이 없어 ACTIVE/OBJS를 측정하지 못했다. [task](evidence/task.txt), [vma](evidence/vma.txt), [slab 읽기 상태](evidence/vma-ALTERNATING_RW_NONE/slabinfo-status.txt)

Codex를 checkpoint 자동화와 분석에 사용했다. cold라는 이름 그대로 캐시 비거주를 가정하지 않고 실제 major fault와 reread 차이를 검토했다.
