# Lab 7 — 가상 메모리 매핑과 해제

제공 소스를 `make`로 빌드했다. `strace -ff -e trace=brk,mmap,munmap,madvise -o evidence/memory.trace ./lab7_memory`로 실행하고 각 Enter 전에 `maps`, `status`, `smaps_rollup`을 저장했다. Linux 7.3.4-arch1-2, x86-64, glibc, 4 KiB 페이지 환경이다. [프로그램 출력](evidence/memory.txt), [syscall](evidence/memory.trace.19304)

| Stage | 살아 있는 malloc 요청 B | VmSize KiB | Rss KiB |
|---|---:|---:|---:|
| 1 baseline | 0 | 3,964 | 1,868 |
| 2 allocated, untouched | 67,108,864 | 135,388 | 67,540 |
| 3 touched | 67,108,864 | 135,388 | 133,332 |
| 4 freed, keep last | 4,096 | 135,388 | 133,332 |
| 5 mmapped region removed | 4,096 | 69,852 | 67,796 |

1. 함수 주소는 실행 파일의 `r-xp`, 읽기 전용 전역은 `r--p`, 초기화된 전역은 `rw-p` 파일 매핑이었다. BSS는 파일 끝의 쓰기 가능 페이지와 이후 익명 zero-fill 페이지에 걸쳤고 지역 변수는 `[stack]`에 있었다. maps는 VMA와 권한을 보여 주며 거주량은 smaps의 Rss로 확인했다. [baseline maps](evidence/memory-1/maps)
2. 2단계에 64 MiB 익명 mmap과 16,384개 × 4 KiB malloc이 공간을 확보했다. mmap payload를 아직 건드리지 않았어도 allocator가 작은 chunk의 관리 정보를 쓰면서 heap 일부가 resident가 됐다. 첫 write 후 VmSize는 그대로였고 Rss는 65,792 KiB 늘었다. allocator heap과 새 mmap 모두가 포함된 프로세스 전체 측정이다. [할당 직후](evidence/memory-2/smaps_rollup), [쓰기 후](evidence/memory-3/smaps_rollup)
3. 마지막 chunk를 남기자 살아 있는 요청은 4 KiB로 줄었지만 heap 끝과 측정 Rss가 그대로였다. allocator는 해제한 chunk를 재사용할 수 있고 OS 반환 여부는 allocator 경로와 배치에 달려 있다. 64 MiB munmap은 해당 VMA를 제거해 Rss와 VmSize를 각각 65,536 KiB 줄였으며 strace의 mmap 주소와 일치했다. [free 후 maps](evidence/memory-4/maps), [unmap 후 maps](evidence/memory-5/maps)

Codex로 checkpoint를 저장하고 비교했다. ALLOCATED_UNTOUCHED라는 이름만 보고 RSS가 일정하다고 가정하지 않고 brk와 allocator의 관리 정보 접근을 함께 확인했다.
