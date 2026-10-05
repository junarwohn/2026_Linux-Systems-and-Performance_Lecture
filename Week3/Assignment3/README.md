# Assignment 3: Event-Driven Server

Level 1의 nonblocking epoll 서버, Level 2의 blocking/offload 비교 모드, Level 3의
worker pool과 bounded queue를 구현했다. 기능 확인과 로컬 지연 관찰은 마쳤다.
Instructor loadgen과 baseline workload가 저장소에 없어 과제에서 요구한 반복 성능
측정은 아직 하지 않았다.

## 실행

`Week3/Assignment3`에서 실행한다. GCC, Make, pthread를 제공하는 Linux 환경이 필요하다.

```bash
make
./server 9000 --mode level1
```

| 모드 | 실행 예 | 동작 |
| --- | --- | --- |
| Level 1 | `./server 9000 --mode level1` | event loop에서 compute를 실행한다. 기본 모드다. |
| Level 2A | `./server 9000 --mode blocking` | blocking compute 기준선과 5초 주기 통계를 제공한다. |
| Level 2B | `./server 9000 --mode offload` | worker 하나에 compute를 맡기고 eventfd로 완료를 알린다. 한 번에 하나만 처리한다. |
| Level 3 | `./server 9000 --mode pool --workers 4 --queue-capacity 16` | 고정 worker pool과 bounded job queue를 사용한다. |

`--workers N`만 지정하면 pool 모드가 된다. worker 수는 1~256, queue capacity는
1~65536이다. `compute <work>`의 work는 양의 반복 횟수이며 1,000,000,000을 넘으면
`ERROR`를 반환한다.

## 프로토콜

요청과 응답은 newline으로 끝난다. TCP stream이므로 한 번의 `recv()`가 요청 한 줄과
일치한다고 가정하지 않는다. Client별 입력 buffer는 미완성 줄을 보관하고, 출력
queue는 partial `send()` 뒤 남은 응답을 보관한다.

| 요청 | 응답 |
| --- | --- |
| `echo <text>` | `OK <text>` |
| `time` | `TIME YYYY-MM-DD HH:MM:SS` (서버 local time) |
| `compute <work>` | 계산 후 `RESULT compute finished` |
| `quit` | `BYE` 전송 후 연결 종료 |

Offload/pool에서 compute를 받을 수 없으면 `BUSY`를 반환한다. 잘못된 요청은
`ERROR`를 반환하고, 256-byte 입력 buffer를 초과하는 요청은 연결을 닫는다.

## 구조와 동작

```text
listener ─┐
client FD ├─> epoll event loop ─> parse lines ─> response queue ─> client FD
timerfd ──┘                              │
                                        └─ compute job -> worker(s)
eventfd <-------------------------------- completion
```

Level 1과 Level 2A에서는 event loop thread가 compute를 직접 실행한다. 이 시간에는
epoll이 다른 FD의 readiness를 알아도 해당 요청을 처리할 수 없다. Level 2B/3에서는
worker가 계산만 하고 completion을 eventfd로 알린다. Client와 epoll 상태는 event loop가
계속 소유한다. Level 2/3의 timerfd는 5초 간격으로 client 수, 요청 수, 처리량과 완료한
compute 수를 출력한다.

## 확인한 동작

- `make` 빌드 성공.
- 요청을 여러 TCP write로 나눠 보내고, 여러 newline 요청을 한 연결에서 처리했다.
- 네 명령의 응답 순서, `quit` 뒤 `BYE`, malformed 요청의 `ERROR`, oversized 요청의
  연결 종료를 확인했다.
- Worker compute 중 client 연결이 끊겨도 다른 client의 `time` 요청을 처리했다.
- Level 2에서 `compute 500000000`과 두 client를 사용한 단일 로컬 실행: blocking 모드의
  다른 client `time` 응답은 0.825초, offload 모드는 0.001초였다. Compute 완료 시간은
  각각 0.846초와 0.848초였다. 이 결과는 event loop가 compute 동안 막히는 현상을 보여주는
  기능 실험이며, 반복 성능 측정값은 아니다.
- Level 3에서 worker 2개, queue capacity 1, client 6개, 각 `compute 250000000`을 보내
  3개 완료와 3개 `BUSY`를 확인했다.

## 남은 성능 측정

과제는 instructor-provided loadgen과 지정 workload를 사용하고, 각 설정을 최소 3회 실행해
중앙값을 보고하도록 요구한다. 문서에 적힌 실행 형식은 다음과 같다.

```bash
./loadgen SERVER_IP PORT CLIENTS REQUESTS_PER_CLIENT WORKLOAD
```

현재 저장소에는 loadgen 실행 파일과 baseline workload 정의가 없다. 따라서 Level 1 baseline,
Level 2 blocking/offload latency 표, Level 3 worker/queue 비교와 workload 중 `perf stat`
자료는 아직 작성하지 않았다. 다른 benchmark로 대체하지 않았다. 도구와 workload를 받으면
동일한 machine/build/request mix로 3회 이상 측정하고 중앙값을 기록한다.

## AI 사용

AI 도구를 코드 초안과 디버깅에 사용했다. Offload 확인 중 계산 요청 직후 client FD 감시가
해제되어 연결이 닫히는 문제를 발견해 수정했고, 두 client 기능 확인으로 다시 검증했다.
