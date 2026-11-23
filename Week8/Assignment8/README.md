# Assignment 8 실행 안내

```bash
make clean
make
./faultwatch --interval .25 PID
./faultwatch -- /bin/true
./kmemwatch --buddy --watermarks --migrate --top 10
./kmemwatch --slab dentry --top 10 --diff 2
python3 workloads/measure.py
```

Python 3, C compiler, clang의 BPF backend, Linux UAPI와 libbpf 헤더/라이브러리가 필요하다. Python 명령은 실행 파일 링크로 연결된다. `workloads/measure.py`는 신선한 프로세스에서 각 조건을 세 번 실행하고 `evidence/`에 JSON checkpoint를 저장한다. 기존 측정값을 보존하려면 작업 복제본에서 실행한다.

Level 3은 코스 지정 Linux 6.8의 raw tracepoint ABI와 BTF를 확인해야 한다. 현재 CO-RE 선언에는 필요한 `struct kmem_cache::name`만 포함했다. 조건을 만족하는 환경에서는 `bpftool btf dump file /sys/kernel/btf/vmlinux format c`로 대상 BTF도 확인할 수 있다. 네 hook을 모두 붙인 뒤의 rate만 출력한다.

```bash
sudo ./myslabtop --interval 2 --top 10 --sort alloc
sudo ./myslabtop --interval 2 --view kmalloc
sudo python3 workloads/phase_activity.py files evidence/bpf-files-1
sudo python3 workloads/phase_activity.py processes evidence/bpf-processes-1
sudo python3 workloads/phase_activity.py idle evidence/bpf-idle-1
```

정확한 이벤트 대조에는 같은 범위에서 독립 tracefs/perf 수집이 필요하다. 로컬 도구는 btrfs의 per-file cache 힌트와 제한된 권한으로 실행했으며 raw tracepoint 실행·root slabinfo 검증이 남아 있다. [보고서](report.md)
