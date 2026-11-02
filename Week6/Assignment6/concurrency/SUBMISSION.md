# Assignment 6 제출 안내

## 환경과 완료 범위

Linux 7.2.11-arch1-1 / x86-64 / glibc 2.44 / GCC 16.2.1.
머신의 논리 CPU는 18개이며 본 측정은 `taskset -c 0,1`로 제한했다.
과제의 WSL2 kernel 6.6 환경과 다르다. 페이지 크기는 4096 B다.
빌드 플래그는 제공 Makefile의 O2, debug, frame pointer, pthread, rdynamic;
TSan은 O1과 fsanitize=thread다.

Level 1~3 필수 구현 완료. l1_check 및 네 case의 l3_check 전체 PASS.
의도적 오류 w2/w3/w4a/w4b와 writer-preferring 실행도 별도 소스로 검증했다.
`bpftrace` / `perf`는 실행 환경에 없어 해당 필수 계측은 미완료다.
원문 [availability](evidence/bpftrace-availability.txt)와 보고서의 제한을 함께 제출한다.

## 빌드와 재현

```sh
make -j4
bash tools/l1_check.sh
for c in 1 2 3 4; do bash tools/l3_check.sh "$c"; done
taskset -c 0,1 python3 analysis/measure.py
python3 analysis/report.py
```

측정 드라이버는 기존 evidence의 같은 이름을 덮어쓰므로 재측정 전 별도 디렉터리로
보관한다. 각 반복은 r1/r2/r3로 구분된다. 단위와 prediction, argv, PID, uname,
return code, 소요시간은 텍스트 로그에 보존했다. UI CSV는 프레임별 µs 단위다.

`analysis/trace.sh`는 bpftrace와 perf가 준비된 환경에서 실행할 계측 묶음이다.
제공 tools/l2_run.sh가 sudo를 요청하는 것은 배포 스크립트의 동작이다.
이번 결과에서 이를 실행해 성공한 것처럼 표시하지 않았다.

## 산출물과 제공 코드 경계

| 경로 | 내용 |
|---|---|
| src/bq.c | 큐 put/get/timed_get/close |
| src/album.c, src/albumsvc.c | deep-copy snapshot과 handoff |
| src/sink.c, bus.c, catalog.c, config.c | TODO 범위의 네 교착 수정 |
| analysis/measure.py, report.py, trace.sh | 작성한 측정·표 생성·후속 계측 드라이버 |
| evidence/l1-*, l2-*, l3-* | 원문 로그, 프레임 CSV, 별도 strace |
| evidence/l3check-*.txt | 제공 검사 출력 원문과 verdict |
| evidence/baseline/ | 수정 전 네 case 소스; 최종 구현과 구별 |
| evidence/variants/*.c, *.patch | 잘못된 수정의 독립 소스와 정확한 diff |
| report.md | 수치표, 예측, 5.3/6.3/7.3 문항 답변과 제한 |

주어진 bqtest/common/bq.h/album.h/devsvc/rcu/tools/lib는 수정하지 않았다.
참조 .a는 제공 자료이며 해결 코드로 대체하지 않았다. 성능 비교에만 devsvc_ref를 썼다.
초기 baseline 실행파일은 같은 컴파일러로 원본 소스에서 만들어 보관한 산출물이다.
다시 만들 때는 `src/devsvc.c src/rcu.c`와 `evidence/baseline/*.c`를 `-Isrc`로 연결한다.
`analysis/measure.py`가 원본 baseline과 의도적 오류를 각각 다시 빌드한다.

strace 로그는 별도 승인된 진단 실행이며 성능 표에는 사용하지 않았다.
명령은 `strace -f -tt -T -e trace=futex,write,fsync,clock_nanosleep -o
evidence/l2-MODE.strace ./bin/albumsvc -m MODE -d 2`다.

AI 사용과 검증한 제안의 반례는 보고서 마지막에 명시했다.
