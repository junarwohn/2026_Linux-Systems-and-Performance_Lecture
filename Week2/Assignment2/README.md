# Assignment 2: Signal-Aware Shell

Assignment1 shell에 background 실행과 signal 처리를 추가했다.
현재 Level 1까지 구현했고, kernel syscall을 사용하는 `event_wait`, `event_notify`와
Level 3의 timeout, `event_count`는 미구현이다.

## 실행

`Week2/Assignment2`에서 실행한다. Assignment1과 별도로 빌드한다.

```bash
make -C myshell
./myshell/myshell
```

```text
myshell> sleep 5 &
[1] <PID>
myshell> echo responsive
responsive
```

Background 명령이 끝나면 추가 입력 없이 `Done`이 출력된다.
Foreground의 `sleep 30`이나 `sleep 30 | cat`은 Ctrl-C로 중단할 수 있다.
Shell은 계속 실행된다.

## 파일 구성

- `myshell/myshell.c`: 입력 대기, 명령 분기, builtin
- `myshell/parser.c`: 인자, 두 명령 pipeline, 끝의 `&` 파싱
- `myshell/execute.c`: job 예약, child 생성, signal 복원, foreground 대기
- `myshell/jobs.c`: signal 초기화, job table, 자식 회수, 종료 정리
- `myshell/myshell.h`: 공통 자료구조와 signal mask 선언

## Background와 job 관리

최대 32개 job을 관리한다. Pipeline은 PID 두 개를 가진 job 하나이며,
각 PID의 종료 상태와 회수 여부를 따로 저장한다.

```text
빈 job 자리 확보 → fork → PID 등록
                         ├─ foreground: 완료까지 대기
                         └─ background: 다음 입력
```

SIGCHLD는 shell 초기화 때 차단하고, 대기 함수 안에서만 차단을 푼다.
Child가 등록 전에 종료해도 signal이 pending으로 남으므로 등록과 종료 처리가
엇갈리지 않는다. Table이 꽉 차면 child를 만들기 전에 오류를 반환한다.
명령 문자열은 입력 버퍼와 별도로 복사하고, job ID는 빈 slot에 따라 재사용한다.

## SIGCHLD와 대기

Handler는 즉시 복귀해 대기를 깨우는 역할만 한다.
출력이나 job table 수정은 handler에서 하지 않는다.

`reap_children()`에서 `waitpid(-1, &status, WNOHANG)`를 반복해 종료한 자식을
모두 회수한다. SIGCHLD는 여러 번의 종료가 하나의 알림으로 합쳐질 수 있으므로
한 번만 `waitpid()`를 호출하면 안 된다. `EINTR`는 재시도한다.

Foreground와 background 모두 이 함수가 종료 상태를 기록한다.
Foreground 대기는 기록을 확인하고, 아직 끝나지 않았으면 `sigsuspend()`로 잠든다.
따라서 foreground 대기 중에도 background를 회수할 수 있고, 같은 자식을
두 곳에서 중복으로 기다리지 않는다. Pipeline은 두 PID 모두 회수한 뒤에만
`Done`을 출력한다.

입력은 `pselect()`로 기다리고 `read()`로 한 바이트씩 모은다.
차단 해제와 대기 진입을 함께 처리하므로, 종료 알림을 확인한 직후 잠드는 사이에
signal을 놓치는 문제를 피한다. 줄 일부만 들어온 경우에도 자식 종료를 처리하며,
foreground child가 읽을 입력을 미리 가져오지 않는다.

## Ctrl-C와 종료 정책

- Shell은 SIGINT를 무시한다.
- Child는 `execvp()` 전에 SIGINT와 SIGCHLD를 `SIG_DFL`로 바꾸고,
  shell 시작 때의 signal mask를 복원한다.
- 같은 terminal process group을 사용하므로 background child도 Ctrl-C를 받는다.
- Background 첫 명령의 stdin은 `/dev/null`이다. Pipeline 둘째 명령은 pipe에서 읽는다.
- `exec` 실패 시 child만 `_exit(127)`로 종료한다. 부분 launch 실패 시에는 이미 만든
  child를 종료·회수하고 FD와 job slot을 정리한다.
- `exit`와 EOF에서는 추적 중인 미회수 direct child에 SIGKILL을 보내고 최대 1초간
  회수를 기다린다. 추적하지 않는 descendant까지 종료하지는 않는다.

`fg`/`bg`와 terminal job control은 구현하지 않았다.
Quoting, 변수 확장, 파일 리다이렉션, 세 개 이상의 명령 pipeline도 지원하지 않는다.

## 확인한 동작

- 입력 없이 기다릴 때와 foreground 실행 중의 background 회수
- Pipeline의 마지막 child가 종료된 뒤 `Done` 출력
- 부분 입력 유지, 빠르게 종료하는 child 96개의 회수와 slot 재사용
- Job 32개가 찬 상태에서 추가 실행 거부
- EOF와 `exit`에서 자식 정리
- PTY에서 Ctrl-C 전송 후 자식 회수, shell 생존, 다음 명령 실행
- Child의 SIGCHLD 차단 해제와 기존 SIGUSR1 mask 유지
- `pipe`, `fork`, `dup2`, child `sigaction` 실패 및 `waitpid`의 `EINTR` 처리

로그는 `myshell/logs/`에 있다. `background-tests.txt`와 `background-pty.txt`는
디렉터리 분리 전 기록이다. 분리 후에는 기본 기능, 빠른 종료, 부분 입력,
background 회수, Ctrl-C를 다시 확인했고 `separation-background.txt`와
`separation-ctrl-c.txt`에 기록했다. 로그는 Git에 포함되지 않는다.

필수 signal trace와 제출 보고서는 아직 정리하지 않았다. Trace 수집 명령:

```bash
cd myshell
mkdir -p logs
strace -f -o logs/signal.trace ./myshell
```

`sleep 5 &`, `echo responsive`, foreground `sleep 30` 중 Ctrl-C를 실행하고
`rt_sigaction`, mask 변경, SIGCHLD·SIGINT 전달, `WNOHANG`을 사용하는
wait 계열 호출을 확인한다.
