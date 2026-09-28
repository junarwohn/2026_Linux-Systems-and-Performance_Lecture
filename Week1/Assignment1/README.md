# Assignment 1: Tiny Shell

Level 1 shell 구현. Kernel syscall을 사용하는 `myps`, `mytop`은 미구현.
Assignment2 확장 버전은 [Week2/Assignment2](../../Week2/Assignment2/README.md)에 있다.

## 실행

`Week1/Assignment1`에서 실행한다. GCC와 Make가 필요하다.

```bash
make -C myshell
./myshell/myshell
```

```text
myshell> /bin/echo hello
hello
myshell> cd /tmp
myshell> pwd
/tmp
myshell> ls | wc -l
myshell> exit
```

## 파일 구성

- `myshell/myshell.c`: 입력 루프와 `cd`, `pwd`, `exit`
- `myshell/parser.c`: 공백 단위 인자 분리, `|` 파싱
- `myshell/execute.c`: 외부 명령 실행, pipe 연결, 자식 회수
- `myshell/myshell.h`: 자료구조와 함수 선언
- `myshell/jobs.c`: starter의 빈 함수 유지. Assignment1에서는 job 관리에 사용하지 않음
- `cpuwork/`: 강의에서 제공한 CPU workload

## 구현

`cd`는 shell에서 `chdir()`를 호출한다. 인자가 없으면 `HOME`으로 이동한다.
`pwd`는 `getcwd()`로 현재 경로를 출력한다. 잘못된 경로나 인자가 들어오면
오류를 출력하고 다음 명령을 받는다. `exit` 또는 EOF로 종료한다.

외부 명령은 `fork()`로 child를 만든 뒤 child에서 `execvp()`로 실행한다.
Parent는 `waitpid()`로 회수한 다음 입력을 받는다. `execvp()`가 실패하면
child만 `_exit(127)`로 종료하고, `waitpid()`가 `EINTR`를 반환하면 재시도한다.

Pipeline은 두 명령까지 지원한다.

```text
child 1 stdout → pipe → child 2 stdin
```

`dup2()`로 각 child의 표준 입출력을 연결한다. 두 child를 모두 생성한 뒤
parent의 pipe FD를 닫고 기다린다. 먼저 한 child를 기다리면 pipe 버퍼가 찼을 때
멈출 수 있고, write FD를 남기면 reader가 EOF를 받지 못한다.
두 번째 `fork()`가 실패하면 먼저 만든 child를 종료하고 회수한다.

`&`, quoting, 변수 확장, 파일 리다이렉션은 지원하지 않는다.
Builtin을 pipeline에 넣거나 명령을 세 개 이상 연결하면 오류로 처리한다.
SIGINT와 SIGCHLD 처리는 Assignment2에 있다.

## 확인한 동작

- `cd` 후 `pwd`, 존재하지 않는 경로, 잘못된 인자
- 절대 경로와 PATH를 통한 명령 실행, `exec` 실패 후 다음 명령 실행
- `seq 1 1000000 | wc -l`: 큰 출력을 보내도 완료
- `yes | head -n 1`: reader가 먼저 종료해도 완료
- `pipe`, `fork`, `dup2` 실패와 `waitpid`의 `EINTR` 처리

Trace는 다음 명령으로 수집한다. `myshell` 안에서 echo, pipeline, cd, pwd를 실행한다.

```bash
cd myshell
mkdir -p logs
strace -f -o logs/shell.trace ./myshell
```

수집한 trace에서 `clone`, `execve`, `wait4`, `pipe2`, `dup2`, `close`를 확인했다.
`chdir`와 `getcwd`는 shell PID에서 실행됐다. 로그는 `myshell/logs/`에 있으며
Git에는 포함되지 않는다. 제출용 trace 발췌와 보고서는 별도로 정리해야 한다.

제공 workload는 `make -C cpuwork`로 빌드한다.
`./cpuwork/cpuwork --threads 1`의 thread 수는 MAIN을 포함하며 Ctrl-C로 종료한다.
