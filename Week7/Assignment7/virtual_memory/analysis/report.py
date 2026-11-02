#!/usr/bin/env python3
"""Build evidence tables using saved native measurements."""
from pathlib import Path
import csv
import json
import re
import statistics
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from level1.vasdump import parse_smaps
E=ROOT/'evidence'
def text(name):return (E/name).read_text()
def load(name):return json.loads(text(name))
def median(values):return statistics.median(values)
def span(values):
    values=list(values)
    m=median(values)
    show=lambda x:f'{x:.3f}'.rstrip('0').rstrip('.') if isinstance(x,float) else str(x)
    return show(m) if min(values)==max(values) else f'{show(m)} ({show(min(values))}–{show(max(values))})'
def table(header,rows):
    return '\n'.join(['| '+' | '.join(header)+' |','|'+'---|'*len(header)]+['| '+' | '.join(map(str,row))+' |' for row in rows])
def cat(row,name,field):return row['categories'].get(name,{}).get(field,0)
def series(tag,phase):
    groups=[]
    for repeat in (1,2,3):
        rows=list(csv.DictReader((E/f'{tag}-r{repeat}.csv').open()))
        stable=[r for r in rows if r['phase']==phase]
        last=stable[-1]
        groups.append((rows[0],last,len(rows)))
    return groups

out=['''# Assignment 7 — Process Memory 관측·계측·진단

## 환경 및 측정 방법

Linux 7.2.11-arch1-1, x86-64, glibc 2.44, GCC 16.2.1, 페이지 4096 B,
stack limit 8192 kB. Makefile의 O1/debug/no-builtin/frame-pointer, ELF PIE,
recursion no-sibling-call 플래그를 사용했다. allocator tunable은 개선 실험 외에는
별도로 설정하지 않았다. 환경 원문은 [environment.stdout](evidence/environment.stdout),
빌드 명령은 [build.stdout](evidence/build.stdout), 세부 사용법은 [SUBMISSION.md](SUBMISSION.md)다.

원문 command.json은 argv, 환경 변수, PID, 종료 상태와 경과 시간을 담는다. 같은 종류를
새 프로세스에서 3회 반복했고 아래 표는 중앙값, 변동이 있으면 (최소–최대)를 병기한다.
메모리는 kB=1024 B, library 통계는 요청 bytes다. Size는 주소공간, Rss는 resident,
Pss는 공유 물리 페이지의 비례 지분으로 구분한다. /proc 관측은 프로세스 전체를 freeze하지
않으므로 여러 파일/카운터의 시점이 완전히 같지는 않다.

체크포인트 workload는 marker를 출력한 채 입력을 기다린다. SIGUSR1 → 완전한
`end snapshot=N` 확인 → vasdump/smaps 수집 → Enter 순서로 진행했다.
자동 진행 case는 1초 고정 간격으로 READY부터 상태와 snapshot을 기록했다.
phase는 최근 marker이며 할당/해제 전이 중 표본이 섞일 수 있어 표에서는 안정된
before_release / after_release / steady의 마지막 표본을 사용했다.

## Level 1 — 직접 구현한 vasdump

smaps VMA header에서 주소/권한/offset/device/inode/path를 읽고 8개 kB field를 파싱한다.
경로 내부 공백과 (deleted)를 보존한다. heap → main stack → bracketed special을
우선 분류하고 그 뒤 exe device/inode, shared library, 기타 file을 판별한다.
이름 없는 .bss tail은 anonymous 그대로 둔다. category와 permission partition 합은
각각 전체 합과 같다. missing field/종료 대상은 명시적 incomplete 오류로 처리한다.
PID 모드는 관찰만 하고 command 모드는 TERM, 1초 bounded wait, KILL, reap 순으로 정리한다.
diff는 address range로 매칭하며 split/merge는 removed/added가 될 수 있다.
절댓값 Rss 변화, Size 변화, 시작 주소 순으로 정렬한다.

### 필수 기능 실험

예측: 작은 블록은 heap에 모이고 큰 요청은 별도 anonymous 영역에 나타날 가능성이 높다.
스레드는 큰 가상 stack과 작은 resident 사용을 더하며, 더 깊은 재귀는 main stack의 touched
페이지를 늘린다. 매초 touched 1 MiB를 유지하면 5초 diff에서 anonymous Size/Rss가 증가한다.
아래 각 실험은 r1~r3을 독립 실행했다.
''']
rows=[]
for tag,description in [('idle-sleep','sleep 1000'),('idle-own','직접 작성한 sleep workload')]:
    vals=[]
    for r in (1,2,3):
        s=text(f'{tag}-r{r}.stdout')
        m=re.search(r'^TOTAL (\d+) (\d+) (\d+)',s,re.M)
        vals.append(tuple(map(int,m.groups())))
    rows.append([description,span(v[0] for v in vals),span(v[1] for v in vals),f'[ {tag}-r1.stdout ](evidence/{tag}-r1.stdout), r2, r3'])
for depth in (8,64):
    vals=[load(f'stack{depth}-r{r}.measurements.json')[0] for r in (1,2,3)]
    rows.append([f'재귀 depth={depth}, [stack]',span(cat(v,'stack','Size') for v in vals),span(cat(v,'stack','Rss') for v in vals),f'[stack{depth}-r1-deepest.vasdump](evidence/stack{depth}-r1-deepest.vasdump), r2, r3'])
vals=[load(f'threads-r{r}.measurements.json')[0] for r in (1,2,3)]
rows.append(['4 worker, anonymous 합',span(cat(v,'anonymous','Size') for v in vals),span(cat(v,'anonymous','Rss') for v in vals),'[threads-r1-four_threads.vasdump](evidence/threads-r1-four_threads.vasdump), r2, r3'])
out.append(table(['실험/대상','Size kB','Rss kB','원문'],rows))
out.append('''
minimal sleep도 실행파일, dynamic loader, libc, stack, heap, vDSO/vvar 등 여러 VMA가
필요하다. worker는 직접 malloc하지 않는 제공 관측 workload의 4개 스레드이며 pthread
runtime의 메모리 할당과 stack mapping은 별개다. stack limit은 8192 kB이며 각 재귀 frame은
4096 B volatile array를 touch하고 함수는 noinline, tail-call 최적화는 껐다.

과제 예시의 `---p` guard VMA는 이번 환경에서 **관측되지 않았다**. 이는 guard 부재의
증거가 아니다. [case3.strace](evidence/case3.strace)에 8392704 B MAP_STACK mmap 후
4096 B에 대한 `MADV_GUARD_INSTALL` 성공이 반복된다. glibc는 이 방식으로 guard를
설치해 별도 PROT_NONE VMA 생성을 줄일 수 있다. 인접 stack들은 하나의 큰 anonymous
VMA로 합쳐져 보이므로 VMA 개수와 스레드 수가 일치하지 않을 수 있다.
[glibc의 guard 구현 변경](https://sourceware.org/pipermail/glibc-cvs/2025q1/087326.html).

작은/큰 allocation 실험은 아래 connection의 A/B 원문 덤프에도 있다. `growth.stdout`,
`growth-r2.stdout`, `growth-r3.stdout`은 `--delay 0.2 --diff 5 -- ./observation_workload growth 12`
실행이다. r1의 anonymous Size/Rss delta는 각각 +5140 kB였다. 요청한 5 MiB=5120 kB 외에
각 큰 mmap의 페이지 정렬/관리 비용이 반영된다. 합쳐진 VMA가 커져 기존 범위 removed,
새 범위 added로 보이지만 이를 누수 두 건으로 해석하지 않는다. command mode는 snapshot
후 정상 동작 중인 workload를 종료하므로 이 growth 실행은 allocator 종료 통계를 위한 것이 아니다.

### ELF segment와 실제 VMA 대응

`elf_workload`는 initialized_global, second_global, read_only_object, 8 MiB large_bss의
양 끝 주소를 출력하고 멈춘다. `readelf-hW/-lW/-SW.stdout`, `nm.stdout`을 보존했다.
''')
symbols={line.split()[-1]:int(line.split()[0],16) for line in text('nm.stdout').splitlines() if len(line.split())>=3 and re.fullmatch(r'[0-9a-f]+',line.split()[0])}
elf=text('elf-r1.stdout')
a=int(re.search(r'initialized_global=(0x[0-9a-f]+)',elf).group(1),16)
b=int(re.search(r'second_global=(0x[0-9a-f]+)',elf).group(1),16)
bias=a-symbols['initialized_global'];assert bias==b-symbols['second_global']
out.append(f'첫 실행 load bias는 `{a:#x} - {symbols["initialized_global"]:#x} = {bias:#x}`다. '
           f'두 번째 symbol도 `{b:#x} - {symbols["second_global"]:#x} = {bias:#x}`로 일치했다.\n')
vmas=parse_smaps(E/'elf-r1-layout.smaps')
rows=[]
for i,line in enumerate([line for line in text('readelf-lW.stdout').splitlines() if line.strip().startswith('LOAD')],1):
    fields=line.split(); offset,addr,physical,filesz,memsz=(int(s,16) for s in fields[1:6]); flags=''.join(fields[6:-1])
    start=(bias+addr)//4096*4096;end=(bias+addr+memsz+4095)//4096*4096
    matches=[v for v in vmas if v['start']<end and start<v['end']]
    matched='<br>'.join(f"{v['start']:x}-{v['end']:x} {v['permissions']} off={v['offset']:x} {v['path'] or 'anonymous'}" for v in matches)
    rows.append([f'LOAD {i}',f'{offset:#x} / {addr:#x} / {filesz:#x} / {memsz:#x}',flags,matched])
out.append(table(['segment','Offset / VirtAddr / FileSiz / MemSiz','flags','runtime VMA'],rows))
out.append(f'''
LOAD4의 file offset 0x2dd0는 page align 후 0x2000이고 runtime start는
{bias+0x3000:#x}이다. RELRO로 앞쪽은 r--p이고 뒤쪽 data는 rw-p로 분리된다.
''')
out.append(f'''large_bss 시작은 `{bias+symbols['large_bss']:#x}`, 마지막은
`{bias+symbols['large_bss']+8*1024*1024-1:#x}`이며 출력과 일치한다. file-backed 마지막 page의
일부 zero-fill과 뒤의 anonymous tail을 함께 사용한다. anonymous tail을 executable
category에 더하지 않았다. LOAD1은 aligned p_vaddr=0이므로 첫 VMA 시작이 bias와 같지만
일반적인 ELF에서 무조건 같다고 가정하지 않았다.
''')
biases=[]
for r in (1,2,3):
    runtime=int(re.search(r'initialized_global=(0x[0-9a-f]+)',text(f'elf-r{r}.stdout')).group(1),16)
    biases.append(f'r{r}: {runtime-symbols["initialized_global"]:#x}')
out.append('ASLR 반복 load bias: '+', '.join(biases)+'. ELF symbol 값과 file offset은 그대로였다.\n')
out.append('''
**5.5-Q1.** libc는 PT_LOAD의 코드/읽기 전용/data 권한과 file offset, RELRO 경계가 달라
여러 VMA로 나뉜다. 서로 다른 mapping이 libc 사본 여러 개를 의미하지는 않는다.

**5.5-Q2.** 8 MiB bss tail과 thread stack은 Size가 Rss보다 훨씬 크다. 예약한 주소 범위 중
실제로 touch한 page가 적다는 가설과 일치한다. bss workload는 주소를 출력하지만 배열
전체를 쓰지는 않는다. 다음 주제인 demand paging의 페이지 상태 관측으로 확인할 수 있다.

**5.5-Q3.** Rss는 공유 page도 프로세스마다 전체 크기로 센다. Pss는 각 page의 공유자 수에
따라 나눈 지분이다. 덤프의 libc r-x/r-- 및 loader에서 차이가 크고 private dirty heap/stack은
비슷하다. Pss는 다른 프로세스의 공유 상태 변화에도 달라져 반복 범위로 해석한다.

**5.5-Q4.** 위 LOAD4의 MemSiz가 FileSiz보다 커 8 MiB .bss가 file 내용 없이 zero 초기화된다.
section 목록과 runtime VMA는 일대일 대응하지 않는다. 마지막 file page의 zero tail과
anonymous 후속 영역이 합쳐져 .bss를 표현한다.

**5.5-Q5.** 세 실행의 PIE bias, stack, heap 및 library 절대 주소는 ASLR로 달라질 수 있다.
같은 바이너리의 symbol 값, segment file offset과 symbol 간 간격은 유지된다.

## Level 2 — LD_PRELOAD allocator 추적

provided support.c가 dlsym(RTLD_NEXT)로 네 glibc allocator를 resolve하고 atomic으로
준비 완료를 publish한다. 해결 도중 같은 스레드의 재귀 호출은 max_align_t 정렬된 1 MiB
정적 bootstrap arena를 쓴다. bootstrap free는 no-op, realloc은 min(old,new) 바이트를
별도 arena block에 복사하며 프로세스 종료까지 저장소를 유지한다. capacity/overflow는
ENOMEM을 반환하고 다른 초기화 스레드는 완전한 함수 pointer publish까지 기다린다.

작성한 mtrace_student.c는 static open-addressed pointer table 262144개로 requested size를
기록한다. 해제된 주소 자체를 dereference하지 않고 주소 identity에 해당하는 metadata를
제거한다. in-place realloc도 old를 제거한 후 new size를 넣고, 실패한 nonzero realloc은
old를 유지한다. NULL free나 소유하지 않은 pointer는 live를 빼지 않는다. metadata가
부족하면 complete=no와 exhaustion count를 계속 남겨 부분 통계를 성공으로 오인하지 않는다.

모든 outer call은 실패를 포함해 한 번씩 센다. histogram은 성공한 non-NULL 새 요청의
requested bytes를 0, 1, 2–3, 4–7 … 마지막 2^63–(2^64−1) bucket으로 센다.
zero-size non-NULL도 live block이고 glibc realloc(p,0)의 NULL은 old를 제거한다.
제공 stats mutex가 forwarding까지 직렬화하므로 snapshot과 live/peak가 서로 일관된다.
free는 errno를 보존하고 출력에는 write와 stack 숫자 buffer만 쓴다.

SIGUSR1 handler는 errno를 보존한 pipe write만 수행한다. reporter thread가 lock을 잡고
통계를 출력한다. pipe가 차면 알림이 누락되거나 합쳐질 수 있다. paused workload의 단일
요청 후 end marker를 기다리는 방식으로 이를 다룬다. reporter 자체 allocation과 정적
metadata는 통계에서 제외하지만 vasdump에는 포함된다. signal 기본 종료/_exit에서는
exit report가 없을 수 있어 필요한 통계는 먼저 snapshot으로 받는다.

### API 및 workload 검증
''')
rows=[]
for tag in ['ls','python','edges']:
    for r in [1]:
        s=text(f'{tag}-r{r}.trace'); counts=re.search(r'^calls (.*)',s,re.M).group(1)
        live=re.search(r'^live_blocks=(.*)',s,re.M).group(1)
        rows.append([tag,counts,'live_blocks='+live,f'[{tag}-r1.trace](evidence/{tag}-r1.trace), r2, r3'])
out.append(table(['실험','r1 API 호출','r1 live/peak bytes','원문'],rows))
out.append('''
ls의 stdout은 원래 ls 출력을 유지하며 trace는 별도 파일에 저장된다. size histogram은
작은 문자열/배열 및 출력 buffer 등 요청 크기의 분포로 해석하며 object 의미까지 알 수는 없다.
Python의 출력은 `499999500000`이다. Python은 작은 object allocator/pool을 재사용하고
일부 메모리를 직접 mmap할 수도 있어 Python object 수와 malloc 호출 수가 다르다.

known_count와 직접 작성한 thread_student는 각각 worker 4개가 100000 malloc/free pair를
실행한다. before_pairs → after_pairs의 malloc delta는 3회 모두 정확히 **400000**이다.
free delta에는 runtime 종료 정리 호출이 추가된다. 원문 snapshot과 수치는 각
known-rN/own-thread-rN.trace 및 measurements.json에 있다. 이를 측정 드라이버의 assert로
검증했다. 32–63 bucket의 workload 증분도 400000이고 concurrent worker 수에 따른
peak는 serialized allocator hook에서의 정확한 관측값이다.

edge_cases는 free(NULL)의 errno, malloc(0), zero calloc, realloc(NULL,n),
realloc(p,0), overflow, nonzero realloc 실패 후 기존 내용 보존을 검사해 EDGE_OK였다.
추가 accounting_test는 순서대로 live delta (blocks,bytes)가
(0,0) → (1,17) → (1,113) → 실패 후 (1,113) → zero calloc 후 (2,113) →
zero realloc 후 (1,0) → 모두 free 후 (0,0)임을 SIGUSR1 snapshot으로 검사했다.
[evidence/accounting.measurements.json](evidence/accounting.measurements.json).

TABLE_CAP=8로 별도 빌드한 exhaustion 검증은 1001개의 동시 workload allocation 중
993개의 metadata 부족을 명시하고 complete=no를 출력했다. 프로그램 동작은 유지되고
live counter underflow는 없었다. 실제 제출 library는 262144개 table이다.
[exhaustion-test.txt](evidence/exhaustion-test.txt).
추가 vasdump parser·partition·PID 유지·exec 오류·early exit·SIGKILL fallback·diff 정렬
6개 테스트도 통과했다. [vasdump-tests.txt](evidence/vasdump-tests.txt).

### Level 1 + Level 2 connection

baseline 및 A–D는 같은 PID에서 측정했다. A는 touched 1000 × 1024 B, B는 추가 touched
16 MiB, C는 큰 블록 해제, D는 작은 블록 모두 해제 상태다. 괄호는 baseline 대비 delta이며
live bytes는 1024로 나누어 소수 kB도 유지했다. 표는 r1; r2/r3의 원문도 보존했다.
''')
conn=load('connection-r1.measurements.json');base=conn[0]
rows=[]
for row in conn:
    lv=row['trace']['live_bytes']/1024; bl=base['trace']['live_bytes']/1024
    def cell(name,field):
        val=cat(row,name,field);delta=val-cat(base,name,field)
        return f'{val} ({delta:+d})'
    rows.append([row['stage'],f'{lv:.3f} ({lv-bl:+.3f})',cell('heap','Size'),cell('heap','Rss'),cell('anonymous','Size'),f"[connection-r1-{row['stage']}.vasdump](evidence/connection-r1-{row['stage']}.vasdump)"])
out.append(table(['단계','live kB (Δ)','heap Size kB (Δ)','heap Rss kB (Δ)','anonymous Size kB (Δ)','근거'],rows))
out.append('''
세 실행 모두 workload live delta는 A/C=1000.000 kB, B=17384.000 kB, D=0.000 kB다.
작은 블록은 heap을 확장하고 큰 16 MiB는 anonymous mmap에 나타났다. C에서 그 VMA가
사라지지만 D에서 heap은 즉시 줄지 않았다. 요청 바이트에는 chunk header/alignment,
allocator free list, TLS, reporter stack, 정적 pointer table, library mapping이 없다.
추적 table의 touched page 증가도 anonymous Rss에 반영된다. 따라서 workload live와
heap Rss, 전체 anonymous Size는 다른 양이다. allocator retention 가설은 brk/munmap,
trim 전후 또는 allocation threshold를 바꾼 별도 실행으로 검증할 수 있다.

**6.5-Q6.** printf/fprintf는 내부 buffer 할당이나 stdio lock을 사용할 수 있어 wrapper로
재진입하거나 이미 보유한 lock과 순환한다. TLS guard와 allocation-free mt_text/mt_number,
write를 사용하고 handler에서는 metadata/stdio/mutex를 전혀 만지지 않는다.

**6.5-Q7.** RTLD_NEXT는 현재 interposer 다음의 실제 libc symbol을 찾는다. dlsym 자체의
allocation이 먼저 들어오면 미초기화 pointer 호출 또는 무한 재귀가 생긴다. 정적 bootstrap과
완전한 초기화 state publish가 필요하다. bootstrap pointer를 glibc free에 넘기면 안 된다.

**6.5-Q8.** 매 allocation마다 전역 mutex, table hash/probe, counter/histogram 비용을
추가하며 동시 allocator 호출을 직렬화한다. peak는 이 관측된 실행의 정확한 요청 live의
최댓값이지만 library 없는 실행의 스케줄과 성능까지 그대로 재현하는 값은 아니다.

**6.5-Q9.** direct mmap, thread stacks와 loader mapping은 네 allocator interposition으로
보이지 않는다. aligned allocation API도 범위 밖이다. case3의 큰 anonymous stack과
작은 calloc/TLS live bytes 차이가 직접 증거다. observer의 static .bss도 같은 한계의 예다.

**6.5-Q10.** leak이 없어도 glibc는 free chunk를 재사용하려 heap에 보유할 수 있고 Rss에는
metadata와 runtime도 들어간다. D의 live=0과 유지된 heap Rss는 모순이 아니다.

## Level 3 — 메모리 이상 진단

다음 표는 각 프로세스의 안정된 같은 phase 마지막 표본을 비교한다. 값은 3회 중앙값이며
변동 시 범위를 표시한다. case1 개선만 별도 source comparison이고 나머지는 원래 binary다.
''')
rows=[]
for c,phase in [(1,'running'),(2,'after_release'),(3,'steady')]:
    before=series(f'case{c}',phase); after=series(f'case{c}-remedy',phase)
    bsize=span(int(x[1]['VmSize_kB']) for x in before);asize=span(int(x[1]['VmSize_kB']) for x in after)
    brss=span(int(x[1]['VmRSS_kB']) for x in before);arss=span(int(x[1]['VmRSS_kB']) for x in after)
    blv=span(int(x[1]['live_bytes'])/1024 for x in before);alv=span(int(x[1]['live_bytes'])/1024 for x in after)
    causes={1:'각 tick에서 소유권을 잃는 touched allocation',2:'마지막 live chunk 때문에 brk heap의 freed chunk 잔류',3:'24개 worker의 큰 가상 stack 예약'}
    remedies={1:'별도 source에서 사용 후 free',2:'mmap_threshold=1024, mmap_max=65536',3:'새 프로세스 ulimit -s 1024'}
    rows.append([f'case{c}',causes[c],remedies[c],f'VSZ {bsize} → {asize}<br>Rss {brss} → {arss}<br>live {blv} → {alv}',f'[case{c}-r1.csv](evidence/case{c}-r1.csv), remedy-r1, r2, r3'])
out.append(table(['case','원인','개선','전 → 후 (kB)','근거'],rows))
out.append('''
### case1 — 실제 해제 누락

**Evidence.** 각 기본 실행은 READY부터 14개 표본을 기록했다. 초당 약 1 MiB씩 live bytes가
늘어 마지막 working sample은 12582912 B=12288 kB였다. [case1-r1.csv](evidence/case1-r1.csv),
동일 run의 .trace/.vasdump/.smaps 및 r2/r3 참조. strace에서는 1052672 B 크기의
anonymous mmap들이 반복되고 작업 중에 그 요청에 대응하는 해제가 나타나지 않는다.

**Hypothesis.** 매 tick의 1 MiB 객체를 계속 보유하며 이전 포인터 소유권을 잃는 경로다.
단순 allocator retention이라면 free 후 요청 live가 줄어야 하는데 여기서는 함께 증가한다.
source를 확인하면 p가 loop마다 새로 할당되고 sink에 마지막 주소만 덮어쓴다.
실측만으로 '다시는 쓰지 않을 객체'를 증명할 수 없지만 이 소유권 흐름이 누수 진단을 지지한다.

**Remedy.** tunable로 필요 없는 live allocation의 논리적 수명을 고칠 수 없으므로 PDF 7.3의
source-change 예외를 적용했다. baseline은 보존하고 별도 case1_remedy에서 touch와
sink 기록 후 free(p)를 추가했다. [정확한 diff](analysis/case1-remedy.patch).
예측은 live 증가가 멈추고 정상 종료 live=0, peak는 대략 1 MiB가 되는 것이다. 같은 기본
부하로 3회 다시 실행한 결과는 위 표와 case1-remedy-rN.trace/CSV에 있다. 이 비교는
원래 baseline binary에 환경 변수만 적용한 실험으로 표시하지 않았다.

**Confidence.** 높음. 소유권 흐름, live trend, VMA trend, source 수정 전후가 일치한다.
실제 서비스라면 요청의 유효 수명을 확인한 후 free 위치를 정해야 한다. 한 분 뒤 예상은
주어진 유한 workload가 이미 종료되어 /proc가 없어지는 것이다. 이 케이스는 무한 부하가 아니다.

### case2 — 해제했지만 allocator가 보유하는 페이지

**Evidence.** 각 실행은 15개 표본이다. before_release live는 33619968 B이며 after_release는
69632 B까지 감소하지만 원래 Rss는 거의 유지된다. 남은 69632 B는 pointer 배열 65536 B와
마지막 4096 B 객체와 일치한다. after_release smaps의 heap이 큰 private dirty 영역이다.
raw before_release/after_release 덤프와 trace를 함께 보존했다.

**Hypothesis and alternative.** 수많은 객체의 실제 누수라면 live가 큰 값으로 남아야 한다.
대부분의 요청 소유권은 해제되었으나 아직 마지막 chunk가 live라 heap 꼭대기를 줄이기
어렵다. 단순 '페이지를 touch하지 않았다'는 설명도 before_release의 큰 Rss로 배제된다.
내부 free hole과 allocator retention이며, 외부에서 free가 곧 munmap이라는 보장은 없다.

**Remedy.** `GLIBC_TUNABLES=glibc.malloc.mmap_threshold=1024:glibc.malloc.mmap_max=65536`
으로 같은 binary를 새로 실행했다. 충분한 mmap 개수를 허용해 4096 B 요청을 개별 mmap
경로로 유도했다. 같은 after_release의 live는 유지되고 Rss/VSZ는 위 표처럼 감소했다.
대신 before_release VSZ/Rss와 mapping/syscall 비용은 커졌다. 이 설정이 일반 프로그램의
최적 allocator 설정이라는 뜻은 아니다. syscall 진단의 기본 실행은 [case2.strace](evidence/case2.strace)다.

**Confidence.** 높음. live 감소와 높은 heap Rss, allocation 경로 변경 후 Rss 감소가
일치한다. 더 정밀하게는 malloc_info 또는 별도 malloc_trim 비교로 top chunk/내부 hole과
반환 가능한 page를 구분할 수 있다. glibc가 내부 free page를 madvise할 수도 있어 단순히
'heap 가운데면 절대 반환 불가능'이라고 일반화하지 않는다.

### case3 — 스레드 가상 stack 예약

**Evidence.** 기본/개선 각각 38개 표본이다. worker 24개는 직접 malloc하지 않는다.
steady live=6912 B는 worker당 288 B의 runtime calloc과 일치한다. 반면 thread마다
기본 가상 주소공간은 약 8196 kB 증가한다. baseline 대비 steady VSZ delta는
196704 kB이며 24×8196과 정확히 맞는다. [case3-r1-steady.vasdump](evidence/case3-r1-steady.vasdump)의
큰 rw anonymous 영역과 [case3.strace](evidence/case3.strace)의 MAP_STACK/madvise가
이를 지지한다. 인접 stack이 VMA로 합쳐질 수 있다는 점은 Level 1에서 설명했다.

**Alternative.** 대규모 malloc 누수나 24개의 거대한 allocator arena라면 live와 heap/mmap
근거가 달라야 한다. 이 workload는 runtime의 작은 calloc 이외에 거의 할당하지 않고
strace에서 8392704 B MAP_STACK이 반복된다. 적은 Rss는 touched stack 부분이 작다는
가설에 부합한다. shared library의 Pss 변화로 이 큰 Size 증가를 설명할 수 없다.

**Remedy.** 새 프로세스에서 `bash -c 'ulimit -s 1024; exec ./case3'`를 실행했다.
worker 수/작업은 같고 default stack만 작아진다. steady VSZ delta가 24672 kB로 줄어
24×1028과 일치한다. raw totals의 감소에는 추적 reporter 자신의 stack 감소도 포함되므로
workload 비교에는 각 프로세스 baseline을 빼야 한다. Rss는 원래도 작아 감소가 작거나
공유/관측 noise에 따라 다르며 Rss 감소만을 성공 조건으로 삼지 않았다.

**Confidence.** 높음. VSZ delta, MAP_STACK 호출 크기, 작은 live, stack limit 변경이
서로 일치한다. 실제 application에서는 작은 stack의 worst-case recursion/로컬 배열을
검토해야 한다. 여기서는 idle worker라 세 반복이 정상 종료했다.

### 필수 분석 문항

**7.6-Q11.** allocator가 보는 live requested bytes와 resident Rss를 시계열로 함께 비교한다.
case1은 둘이 증가하고 case2는 live가 급감해도 Rss가 남는다. 다만 live가 큰 것만으로
누수를 확정할 수는 없고 ownership/lifetime을 검증해야 한다.

**7.6-Q12.** free는 glibc에 반환하는 동작이다. tcache/bin 보유, 아직 live인 이웃과의
fragmentation, heap top 위치 때문에 Rss가 남을 수 있다. mmap chunk의 free는 munmap,
충분한 top free chunk는 trim threshold에 따른 brk 축소, 조건에 맞는 interior page는
madvise/trim 경로로 반환될 수 있다. threshold와 현재 allocator 상태를 함께 봐야 한다.

**7.6-Q13.** 주소공간의 예약 크기와 실제 resident page는 별개다. case3의 대부분 Size는
anonymous stack에 있고 malloc live는 수 kB다. 이 환경에서는 guard madvise와 VMA 병합
때문에 스레드별 별도 ---p 행을 전제로 분석하면 틀린다.

**7.6-Q14.** 한 번만 재면 case2의 free 이전/이후를 구분하지 못한다. Rss만 보면 retention을
누수로 오인할 수 있고, 최종 상태만 보면 exit가 모든 mapping을 회수해 case1의 증가도
놓친다. VSZ만 보면 stack 예약을 실제 수백 MiB 물리 사용으로 오인한다. 여러 시점과
allocator/API 및 kernel mapping 관측의 결합이 필요했다.

## AI 사용 및 검증한 제안

Codex가 구현, 관측 드라이버, 추가 테스트, 표와 설명 초안을 작성했다. 기존 tests와 case
baseline은 수정하지 않았다. '스레드마다 반드시 ---p guard VMA가 생긴다'는 초기 가정을
실제 smaps 및 MADV_GUARD_INSTALL trace로 반증해 수정했다. free/realloc 이후 포인터의
malloc_usable_size를 읽는 접근도 채택하지 않고 metadata의 요청 크기만 사용했다.
숫자는 이 시스템 실행 결과이고 제공 sample의 측정값을 사용하지 않았다.
''')
(ROOT/'report.md').write_text('\n'.join(out))
