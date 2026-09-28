# Assignment1 — Tiny Shell (Level 1)

이 디렉터리는 Assignment1의 shell 구현을 보관합니다.
Assignment2 확장 버전은 [Week2/Assignment2/myshell](../../../Week2/Assignment2/myshell)에 있습니다.

## 빌드와 실행

이 디렉터리에서 실행합니다.

```bash
make
./myshell
```

- `cd [directory]`, `pwd`, `exit`는 shell process에서 실행합니다.
- External command는 `fork()` → `execvp()` → `waitpid()`로 실행합니다.
- 두 명령 pipeline은 두 child를 모두 생성하고 pipe FD를 정리한 뒤 기다립니다.
- `&`, quoting, 세 개 이상의 명령으로 구성된 pipeline은 지원하지 않습니다.
- SIGINT 처리와 비동기 SIGCHLD 회수는 이 버전에 포함하지 않습니다.
- `jobs.c`의 빈 함수와 공통 자료구조는 제공 starter 구조를 유지한 것입니다.

## 관측

```bash
mkdir -p logs
strace -f -o logs/shell.trace ./myshell
```

Shell 안에서 `/bin/echo hello`, `ls | wc -l`, `cd /tmp`, `pwd`, `exit`를
실행하고 생성·exec·wait·FD 연결·정리와 builtin의 실행 PID를 확인합니다.

현재 범위는 Level 1입니다. Kernel 수정이 필요한 Level 2(`myps`)와
Level 3(`mytop`)는 사용자 요청으로 보류되어 있습니다. 이 문서는 제출 보고서를
대체하지 않습니다.
