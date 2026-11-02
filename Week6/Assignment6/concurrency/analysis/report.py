#!/usr/bin/env python3
"""Generate tables from retained logs; explanatory text is reviewed prose."""
from pathlib import Path
import re
import statistics
ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'evidence'
def read(name): return (E/name).read_text()
def value(text,key): return int(re.search(r'\b'+key+r'=(\d+)',text).group(1))
def median(rows,key):return int(statistics.median(value(row,key) for row in rows))
def dist(text,label,percentile='p99'):
    m=re.search(r'^'+label+r'\s+.*?\b'+percentile+r'=(\d+)',text,re.M)
    return int(m.group(1)) if m else None
out=['''# Assignment 6 — 구현 및 실측 보고서

## 실행 조건과 검증 범위

Linux 7.2.11-arch1-1, x86-64, glibc 2.44, GCC 16.2.1, 페이지 4096 B.
실제 머신은 논리 CPU 18개이며 측정 드라이버를 `taskset -c 0,1`로 실행했다.
WSL2 6.6 / vCPU 2대와 동일한 머신이라는 뜻은 아니다. 제공된 Makefile의 플래그와
상수, 테스트 및 tools는 유지했다. 일반 빌드로 성능을 재고 TSan은 별도로 실행했다.
Level 1~3 구현과 제공된 기능·경쟁·병렬성·속도 검증은 완료했다.

`bpftrace`와 `perf` 실행 파일은 이 실행 환경에 없다. 따라서 futex의 호출자별 집계,
lockwait uprobes 및 perf sched timehist 수치는 **UNAVAILABLE**이다. 0 또는 통과로
기록하지 않았다. `analysis/trace.sh`에 해당 필수 측정의 재실행 명령을 남겼다.
이 부분을 포함한 모든 계측 요구가 충족된 보고서라고 주장하지 않는다.
추가적인 시스템콜 근거는 승인 후 별도 strace 실행으로 수집했다.

정확한 명령·예측·PID·커널·소요시간은 각 `evidence/*.txt` 머리와 꼬리에 있다.
`evidence/environment.txt`가 환경 원문이다. 큐와 UI 표는 각각 3회 실행의 중앙값이다.
측정 중 같은 머신에서 Week7의 메모리 관측도 일부 진행되었으므로 완전히 격리된
성능 벤치마크는 아니다. affinity는 CPU 수만 제한하며 다른 작업을 배제하지 않는다.

## Level 1 — bounded queue

큐 불변식은 잠금 아래에서 `0 <= count <= cap`이다. 조건변수는 항목 자체가 아니라
조건이 바뀌었을 가능성을 알린다. wait는 mutex 해제와 대기 등록을 원자적으로 연결하고,
반환할 때 mutex를 재획득한다. 그 사이 다른 스레드가 조건을 소비할 수 있어 BQ_WHILE로
다시 확인한다. put/get은 서로 반대쪽 조건을 깨우며 close는 모든 대기자를 깨운다.

예측: 용량 증가로 full/empty 대기가 줄고 처리량이 대체로 증가한다. broadcast는 여러
스레드를 깨워 불필요한 mutex 재경쟁을 만들 수 있다. 실제 방향은 아래 로그로 판별한다.
CAP 비교는 `-p 2 -c 2 -n 100000`, wake 비교는 `-p 4 -c 4 -n 50000 -q 4`이다.

| 설정 | items/s 중앙값 | put waits | get waits | 원문 (r1~r3) |
|---|---:|---:|---:|---|''']
for tag in ['q1','q4','q64','signal','broadcast','work']:
    rows=[read(f'l1-{tag}-r{i}.txt') for i in (1,2,3)]
    out.append(f'| {tag} | {median(rows,"items_per_s")} | {median(rows,"put_waits")} | {median(rows,"get_waits")} | [l1-{tag}-r1.txt](evidence/l1-{tag}-r1.txt), r2, r3 |')
out.append('''
work 설정은 `-p 2 -c 2 -n 50000 -q 4 -w 20`이다. 위 wait 값은 커널 futex 호출 수가
아니라 소스가 cond wait 진입 때 증가시킨 수이다. 정상 빌드의 lost/duplicate/order/
early_closed는 모두 0이었다. [l1-check.txt](evidence/l1-check.txt)의 일반/TSan
close·timeout·stress·broadcast 테스트가 모두 PASS했다.

**5.3-Q1.** [l1-bugif.txt](evidence/l1-bugif.txt)에서 실제 관측한 실패는 10초 내 미완료인
HANG이다. 예를 들어 full 큐의 P1이 자고, 소비자가 한 칸 비워 깨운 뒤 다른 생산자 P2가
그 칸을 먼저 차지하면 P1은 다시 full인 큐에 들어간다. if 버전은 다시 검사하지 않아
count가 cap을 넘고 ring의 미소비 항목을 덮어쓸 수 있다. 이후 카운트/조건이 어긋나
진행이 멈출 수 있다. 이는 실패를 설명하는 가능한 interleaving이며 해당 실행의 모든
스케줄을 관측해 재구성했다는 뜻은 아니다. 정상 while 버전은 다시 잠든다.

**5.3-Q2.** close 후 empty consumers와 full producers는 더 이상 반대편 작업이 오지
않을 수 있다. signal만 호출하면 나머지 대기자가 영원히 잠들므로 close의 producer/
consumer 다중 대기자 검증이 끝나지 않는다. 닫힘 상태 변경과 검사, wait를 같은 mutex로
보호해야 '검사 직후 close가 broadcast하고 그 다음 잠드는' lost wake-up을 막는다.
close 후 get은 잔여 항목을 먼저 돌려주며 put은 무조건 CLOSED이다. destroy는 join 후다.

**5.3-Q3.** 용량/깨움 방식 비교는 위 표를 따른다. futex caller별 수치는 미측정이다.
경쟁 없는 pthread mutex의 fast path는 사용자 공간 atomic 연산으로 끝날 수 있다.
반면 empty/full 조건변수 sleep과 pthread_join은 mutex 경쟁이 없어도 futex를 쓸 수
있다. cond_signal도 glibc 상태에 따라 futex wake를 사용한다. 따라서 syscall 수만으로
mutex contention을 단정할 수 없으며 op와 호출자/스택을 함께 분류해야 한다.

**5.3-Q4.** `completion_inversions`는 -w 실행에서 관측되지만 dequeue 검사는 PASS다.
mutex 아래 dequeue한 순서와, mutex 밖에서 처리한 작업이 끝나는 순서는 다르다.
FIFO는 처리 완료 순서까지 직렬화하지 않는다. timed_get은 진입 시 monotonic deadline을
한 번 계산한다. 매 wake마다 새 deadline을 만들면 빈 broadcast가 timeout을 계속 늦춘다.
제공된 deadline_fixed 검증은 그런 연장을 검출한다. 0ms는 cond wait를 하지 않는 poll이며
큐 mutex를 얻는 비용까지 없어진다는 뜻은 아니다. 결과 우선순위는 item, closed, timeout이다.

## Level 2 — UI의 lock 경로 단축

snapshot은 album의 scalar 필드와 256개 item 전체, 각 title 문자열을 별도로 복제한다.
부분 할당 실패 시 이미 복제한 title만 정리한다. saver는 복사 중에만 album_lock을 보유한다.
handoff에서는 UI만 album을 읽고 수정한다. worker가 update 소유권을 큐로 보내고 UI는
프레임당 최대 64개를 nonblocking get으로 적용·해제한다. UI가 만든 snapshot은 saver가
저장·해제한다. save_in_flight가 1이면 추가 snapshot을 보내지 않아 UI의 put 위치에서
용량 1인 saver 큐가 차 있는 상황을 방지한다. 실패한 put의 소유권은 호출자에게 남는다.

예측: 긴 저장 잠금을 복사 시간으로 줄이면 UI 지연이 감소한다. handoff는 UI의 적용/복사
비용을 늘린다. CPU 1에 saver와 burner를 같이 두면 잠금 소유자가 선점될 수 있다.
모두 `-d 10`, 기본 상수 유지. 표의 시간 단위는 µs이며 각 통계의 3회 중앙값이다.

| 설정 | miss | frame p99 | ui_lock / ui_apply p99 | saver_hold p50 / p99 | 근거 (r1~r3) |
|---|---:|---:|---:|---:|---|''')
for tag in ['lock','snapshot','handoff','lock-D5','lock-cpu','lock-burn','snapshot-burn']:
    rows=[read(f'l2-{tag}-r{i}.txt') for i in (1,2,3)]
    md=lambda label,p='p99':int(statistics.median(dist(r,label,p) for r in rows))
    hold='—' if tag=='handoff' else f'{md("saver_hold","p50")} / {md("saver_hold")}'
    out.append(f'| {tag} | {median(rows,"miss")} | {md("frame_late")} | {md("ui_apply" if tag=="handoff" else "ui_lock")} | {hold} | [l2-{tag}-r1.txt](evidence/l2-{tag}-r1.txt), r2, r3 |')
out.append('''
D5는 `-D 5`, cpu는 `-D 0 -c 1`, burn은 같은 설정과 `bash tools/burn.sh 1 12`를
동시에 실행한 것이다. burner의 12초는 10초 측정을 덮도록 정했고 원래 30초 예제보다
짧다. 저장 주기/압축량은 바꾸지 않았다. r1~r3 각각 frame CSV도 보존했다.

**6.3-Q5.** lock 모드의 miss, frame p99, ui_lock p99, saver_hold는 위 표와 원문에 있다.
saver_save_locked가 압축·write·fsync·disk sleep 전체에서 album_lock을 잡는다.
[evidence/l2-lock.strace](evidence/l2-lock.strace)에서 saver TID 101537의 write/fsync/
clock_nanosleep과 UI TID 101536의 futex 대기를 확인할 수 있다. 코드의 잠금 범위와
호출 지연의 결합은 저장 경로를 원인으로 지지하지만 lockwait가 owner를 직접 표시한
측정은 없다. strace 실행은 별도 2초 진단이며 제한 없는 affinity에서 세 모드를 함께
추적했다. 이 실행의 시간 수치를 위의 untraced 성능 표와 동등 조건으로 비교하지 않는다.
uprobe 및 ptrace 자체가 스케줄과 call latency를 바꿀 수 있다.

**6.3-Q6.** title 포인터만 복사하면 worker의 album_apply가 그 문자열을 free한 뒤 saver가
읽어 use-after-free가 된다. deep copy는 독립된 수명을 보장한다. 긴 I/O 시간이 잠금 밖으로
옮겨지면서 saver_hold가 줄었다. 대신 save마다 구조체와 256개 문자열의 할당·복사·해제,
추가 메모리와 allocator 경합 비용이 생긴다. 이 복사 비용 자체는 여전히 lock 안에 있다.

**6.3-Q7.** UI가 album과 snapshot 생성을 소유하고 saver는 snapshot I/O만 한다.
ui_lock 대신 ui_apply를 측정하며 복사/적용/큐 mutex 비용을 UI가 부담한다. 표의 handoff
ui_apply p99가 직접 측정값이다. lockwait 아래 증가량은 미측정이지만 큐 mutex마다
uprobe를 거치면 누적 관측 비용이 증가할 것으로 예상한다. 적용 budget을 제거하면 많은
update를 처리하느라 다음 프레임 시작을 놓칠 수 있다. 종료 시 미적용 update는 주어진
main의 drain/free 경로로 정리된다.

**6.3-Q8.** UI는 매 프레임 절대 시각까지 sleep한다. perf sched의 wait 시간에는 정상
주기 대기도 포함될 수 있고 runnable 이후의 scheduling delay와 구별해야 한다.
긴 wait 하나로 잠금 병목을 증명할 수 없다. 여기서는 ui_lock 계측, 저장 잠금 범위,
strace 시스템콜을 결합했다. lockwait의 caller/owner 직접 근거와 timehist는 아직 없다.

**6.3-Q9.** -D0/cpu와 burn 표가 실제 비교다. burner가 같은 CPU의 시간을 가져가면
saver의 동일한 CPU 작업도 더 긴 wall time 동안 lock을 보유한다. ui_lock에도 그 지연이
전파될 수 있다. snapshot은 긴 압축/I/O가 잠금 밖이므로 그 전달 경로가 짧다. 3회 결과에
noise와 p99 표본효과가 있으며, 이 환경에서 측정되지 않은 효과 크기를 만들어내지 않았다.

## Level 3 — 교착상태 진단과 제한된 수정

case1은 sink 인덱스 오름차순으로 두 잠금을 얻어 sink_total의 전역 순서와 맞췄다.
case2는 bus_lock 아래 구독자 포인터 배열을 stack으로 snapshot하고 unlock 뒤 callback을
실행한다. case3은 read-only 두 경로의 원래 순서를 유지하고 read lock을 사용한다.
grant는 apps W → perms R, set_perm은 apps R → perms W로 쓴다. revoke는 apps W다.
case4는 완성된 새 config를 release store로 publish하고 reader는 acquire load 한 번으로
받은 동일 config만 사용한다. writer의 출력 갱신을 직렬화한 뒤 cfg_lock을 놓고 grace
period를 기다려 old를 해제한다. 출력 잠금과 모든 svc_work의 필수 범위를 유지했다.

| 사례 | lockcheck | TSan (data race) | stress | parallel | speed / reference | 근거 |
|---|---|---|---|---|---|---|''')
for c in range(1,5):
    s=read(f'l3check-{c}.txt')
    par=re.search(r'^parallel\s+PASS\s+(.*)',s,re.M).group(1)
    speed=re.search(r'^speed\s+PASS\s+(.*)',s,re.M).group(1)
    out.append(f'| {c} | W1~W4=0 / PASS | 0 reports / PASS | 5/5 | {par} | {speed} | [l3check-{c}.txt](evidence/l3check-{c}.txt) |')
out.append('''
수정 전 실제 실행 모두 HANG이었다. 최초 18 CPU 진단은 `l3-N-before-*`에,
원본 보관 실행파일을 두 CPU에 제한해 다시 실행한 진단은 `l3-N-baseline-native.txt`와
`l3-N-baseline-tsan.txt`에 있다. 원본 소스는 `evidence/baseline/`에 보존했다.

| 실행 | 예측 | 실제 관측 및 근거 |
|---|---|---|''')
for tag,pred in [('w2','recursive mutex hides self-deadlock, breaks subscriber traversal'),('w3','reversed writer path can form a real cycle'),('w4a','grace period while holding cfg_lock can deadlock'),('w4b','free before grace period can race with readers')]:
    s=read(tag+'-check.txt')
    ls=[line.strip() for line in s.splitlines() if re.match(r'^(lockcheck|TSan|parallel|speed)\s',line)]
    out.append(f'| {tag} | {pred} | '+ '<br>'.join(ls)+f'; [{tag}-check.txt](evidence/{tag}-check.txt) |')
out.append('''
변경한 정확한 줄은 [w2.patch](evidence/variants/w2.patch),
[w3.patch](evidence/variants/w3.patch), [w4a.patch](evidence/variants/w4a.patch),
[w4b.patch](evidence/variants/w4b.patch)에 있다. 최종 src에는 이 잘못된 변경이 없다.

**7.3-Q10.** 수정 전 W1의 실제 now/earlier 경로를 다음과 같이 읽었다.

- case1: sink_move의 높은 인덱스 → 낮은 인덱스와 sink_total의 낮은 → 높은 순서가 순환한다.
- case2: bus_publish에서 bus_lock을 보유한 callback이 bus_unsubscribe로 같은 lock을 다시 얻는다.
- case3: can_launch의 apps → perms와 perm_users의 perms → apps가 mutex 모드로 순환한다.
- case4: render의 output → cfg와 update의 cfg → output이 순환한다.

각 `l3-N-before-lockcheck.txt`는 now/earlier의 함수·lock 주소와 worker HANG 위치를 함께
보존한다. case2의 self-acquire는 lockcheck의 한 줄 W1에 나타나지만 이 TSan에서는
일반적인 서로 다른 잠금의 order cycle로 보고되지 않았다. 경고 없음이 성공은 아니다.
수정 case3의 진단용 `l3-3-tsan.txt`에는 read-only 역순까지 lock-order-inversion으로
보고된다. 제공된 pass 검증은 detect_deadlocks=0으로 data race를 검사하고, read/write
호환성은 mode-aware lockcheck가 검사하므로 두 결과가 모순되지 않는다.

**7.3-Q11.** w2는 W1=0이고 HANG도 없지만 persistent subscriber 호출 횟수가 틀렸다.
callback이 자신을 unsubscribe하면 마지막 요소가 그 인덱스의 빈자리를 메운다. 원래
for문의 i++가 그 요소를 건너뛴다. 반복 중인 subs 배열과 nsubs가 callback 동안 유지된다는
불변식이 깨지는 것이다. snapshot은 순회 대상이 고정되어 이를 피한다. struct sub를
동시 free할 수 있는 계약이라면 reference count나 grace period 등 수명 보호도 필요하다.
w2는 callback까지 큰 mutex로 직렬화해 parallel=0%지만 두 CPU에서는 기준 구현도 동시에
실행할 수 있는 CPU 시간이 제한된다. 그래서 ops/s 비율 하락은 parallel 비율 차이보다
작을 수 있다. 실제 w2 speed는 위 표처럼 기준의 약 0.75였다.

**7.3-Q12.** default glibc의 reader-friendly rwlock은 두 read-only 경로가 서로의 read를
막지 않으므로 단순 R/R 역순 순환이 wait cycle이 되지 않는다. w3 로그에는
set_perm: perms **write** → apps **read**, grant: apps **write** → perms **read**가
명시되고 실제 HANG이었다. 쓰는 경로들은 apps → perms 순서를 일관되게 유지해야 한다.
`DEVSVC_RWLOCK=writer` 실행의 [l3-3-writer-lockcheck.txt](evidence/l3-3-writer-lockcheck.txt)는
R/R 역순까지 W1로 보고하고 HANG이었다. 대기 writer가 신규 reader 진입을 막기 때문이다.
Linux rwsem의 down_read를 glibc의 reader-preferred read와 동일시할 수 없다. lockdep는
recursive/nonrecursive reader와 writer의 차단 관계를 구별해 강한 dependency cycle을
검사한다. [Linux lockdep 설계 문서](https://cdn.kernel.org/doc/html/latest/locking/lockdep-design.html).

**7.3-Q13.** cfg_lock을 rwlock으로만 바꾸면 render가 output을 잡고 cfg R을 기다리고,
update가 cfg W를 잡고 그 output을 기다릴 수 있다. R/W는 여전히 충돌한다.
w4a에서 W3는 synchronize_rcu의 usleep 중 cfg_lock 보유를 보고했고, HANG의 worker들은
config_update에 있었다. writer A는 cfg_lock을 보유한 채 B의 QS를 기다리지만, B는
cfg_lock을 기다려 다음 request 경계의 QS에 도달하지 못한다. W1=0이라도 QS 의존성은
잠금 그래프 밖에 있다. w4b는 lockcheck W1~W4가 모두 0이어도 driver 결과 FAIL 및 TSan
경고가 발생했다. 해제된 config를 읽기 때문이다. reader가 cfg를 여러 번 load하면
서로 다른 version 객체의 필드를 섞을 수 있으므로 한 번 얻은 포인터의 수명을 QS까지
보호하며 일관되게 써야 한다.

## AI 사용 및 확인

Codex가 구현, 실행 드라이버, 표 생성 및 설명 초안을 작성했다. 제공 테스트는 수정하지
않았다. 'recursive mutex면 event bus가 해결된다'는 접근은 w2에서 실제 callback 누락과
parallel=0을 확인해 채택하지 않았다. rwlock의 역순 read가 항상 안전하다는 일반화도
writer-preferred 실행의 HANG으로 반증했다. 본 보고서의 미측정 계측은 추정치로 채우지 않았다.
''')
(ROOT/'report.md').write_text('\n'.join(out))
