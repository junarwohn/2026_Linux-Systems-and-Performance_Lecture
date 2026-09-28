# Assignment2 — Signal-Aware Shell (Level 1)

[Assignment1 shell](../../../Week1/Assignment1/myshell)을 확장한 독립 복사본입니다.
각 디렉터리에서 별도로 빌드하며, 소스나 실행 파일을 서로 링크하지 않습니다.

## 빌드와 실행

이 디렉터리에서 실행합니다.

```bash
make
./myshell
```

Assignment1의 builtin, external command, 두 명령 pipeline에 다음을 추가했습니다.

- 끝의 `&`로 background 실행. Pipeline 하나는 두 PID를 가진 job 하나입니다.
- SIGCHLD를 차단한 상태에서 job 자리를 확보하고 child PID를 등록합니다.
- Handler는 대기를 깨우기만 합니다. 정상 실행 흐름에서 `waitpid(-1, ..., WNOHANG)`를
  반복해 종료 상태를 회수하고 공통 job table에 기록합니다.
- 입력은 `pselect()`와 `read()`로 처리합니다. Foreground 대기는 공통 reaper와
  `sigsuspend()`를 사용하므로 background 회수와 종료 상태를 공유합니다.
- Shell은 SIGINT를 무시하고, child는 `execvp()` 전에 기본 signal 처리와
  shell 시작 시의 signal mask를 복원합니다.

## 동작 범위

- 같은 terminal process group을 사용합니다. Ctrl-C는 background child에도 전달됩니다.
- Background 첫 명령의 stdin은 `/dev/null`입니다.
- Job ID는 빈 slot에 따라 재사용됩니다. 두 child 모두 회수해야 pipeline이 `Done`입니다.
- `exit`와 EOF에서는 추적 중인 미회수 direct child에 SIGKILL을 보내고 최대 1초간
  회수를 기다립니다. 임의의 descendant나 다른 process group은 종료하지 않습니다.
- `fg`/`bg`, terminal job control, quoting, 세 개 이상의 명령 pipeline은 구현하지 않습니다.

## 관측과 진행 상태

```bash
mkdir -p logs
strace -f -o logs/signal.trace ./myshell
```

`sleep 5 &`와 `echo responsive`, foreground `sleep 30` 중 Ctrl-C를 관측합니다.
Signal 등록·전달, `waitpid`에 대응하는 `wait4`의 `WNOHANG`, 자식 종료·회수를
PID와 함께 확인합니다.

`logs/`에 복사된 기존 테스트 기록은 디렉터리 분리 전 실행 결과입니다.
Level 1 기능 구현과 동작 테스트를 마쳤지만, 필수 signal trace 증거와 제출 보고서
정리는 남아 있습니다. Kernel syscall을 사용하는 Level 2·3의
`event_wait`/`event_notify`는 아직 구현하지 않았습니다.
