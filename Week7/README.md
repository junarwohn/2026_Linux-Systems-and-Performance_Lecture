# Week 7 — Process Memory

- [실습 실행 안내](Lab7/README.md) · [실습 보고서](Lab7/report.md)
- [과제 실행 안내](Assignment7/virtual_memory/README.md) · [과제 보고서](Assignment7/virtual_memory/report.md) · [제출물 구성](Assignment7/virtual_memory/SUBMISSION.md)
- [제출 tar.gz](Assignment7/assignment7_submission.tar.gz) · [압축 해제 후 검증 기록](Assignment7/submission-validation.txt)

## 과제 결과

- `/proc/<pid>/smaps`를 직접 파싱하는 vasdump에 VMA 분류·집계·상세 출력·diff·자식 프로세스 정리를 구현했다.
- malloc/calloc/realloc/free 추적기에 요청 횟수, pointer ownership, histogram, live/peak bytes와 SIGUSR1 snapshot을 구현했다.
- ELF·ASLR·스레드·재귀·메모리 증가와 allocation/free 체크포인트를 관측했다.
- 4개 스레드의 malloc 400,000회를 집계하고, allocator edge case·메타데이터 부족·vasdump 테스트를 검증했다.
- 필수 case1~3의 원인과 개선 전후를 각각 새 프로세스로 3회 측정했다. case4·5는 선택 사례다.

기존 과제는 Linux 7.2.11-arch1-1, x86-64, glibc 2.44, GCC 16.2.1, 4 KiB 페이지 환경에서 측정했다. 제출 tar의 파일 624개가 풀린 사본과 일치했으며 SHA-256 manifest를 검증했다.
