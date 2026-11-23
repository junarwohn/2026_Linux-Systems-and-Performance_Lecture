# Assignment 9 실행 안내

```bash
make clean
make
./zoneinfo
./memhog --help
./memhog anon 64MiB --rate 32 --hold 2 --retouch random --seed 42
./psimon --interval 1000
```

각 로그는 CLOCK_MONOTONIC 초를 공통 기준으로 사용한다. `probe`와 `psimon`은 SIGINT/SIGTERM으로 종료한다. 크기에 단위를 생략하면 바이트다. `probe --size`의 단위는 KiB다. `memhog burst`는 resident base를 유지하면서 burst를 주기적으로 매핑하고 해제한다. `reserve`와 `--adj`는 Week 10에도 사용한다.

로컬 재현은 새 증거 디렉터리에서 실행한다. 기존 `evidence/<run>`은 그대로 두고 작업 복제본을 사용하면 같은 이름으로 재현할 수 있다.

```bash
systemd-run --user --wait --pipe -p WorkingDirectory="$PWD" \
  -p MemoryMax=384M -p MemorySwapMax=128M -p RuntimeMaxSec=105 \
  -p Delegate=memory -p DelegateSubgroup=observer python3 analysis/local_runs.py
python3 analysis/summarize.py
```

`--trigger-followup`은 짧은 저임계값 비교, `--trigger-extended`는 cache 장시간 비교를 다시 실행한다. `student_support/sample.py`는 강의 제공 전역 계측기이고, 실행기는 별도로 cgroup 파일을 읽는다. 섞인 범위와 상이한 분석 구간을 구분해 [보고서](report.md)에 기록했다.

Level 3의 지정 VM 명령 인터페이스는 `python3 analysis/tune_vm.py --help`에 있다. 완전히 쓴 ext4 파일, pilot에서 고정한 anon/burst/속도와 실제 disk swap이 필요하다. 실행기는 전역 sysctl을 변경한 뒤 원래값을 읽어 복원한다.
