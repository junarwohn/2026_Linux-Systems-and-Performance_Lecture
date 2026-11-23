# Week 6 제출물 후속 확인

기존 tar를 임시 디렉터리에 풀어 SHA-256 manifest와 작업 트리의 257개 파일을 대조했다. [과제 보고서](../concurrency/report.md)와 [제출 검증](../submission-validation.txt)은 원래 실험을 정리한다.

임시 복제본에서 `make` 한 뒤 아래를 각각 3회 실행했다.

```bash
strace -f -c -e futex ./bin/bqtest -p 2 -c 2 -n 20000 -q 1
strace -f -c -e futex ./bin/bqtest -p 2 -c 2 -n 20000 -q 64
```

queue 1의 futex syscall은 261,397 / 248,343 / 250,877회, queue 64는 23,415 / 14,817 / 17,403회였다. 작은 buffer에서 producer와 consumer의 대기·깨움이 더 자주 나타난 결과와 일치한다. strace 자체는 실행 시간과 scheduling에 영향을 준다. bpftrace caller별 futex, lockwait와 perf sched timehist는 미수집이다. [원본](strace-futex.txt)
