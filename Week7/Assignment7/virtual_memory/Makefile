CC ?= cc
CFLAGS ?= -O1 -g -std=c11 -Wall -Wextra -Wpedantic -fno-builtin -fno-omit-frame-pointer
CPPFLAGS += -D_GNU_SOURCE
PROGRAMS = checkpoint_workload elf_workload observation_workload known_count edge_cases idle_student thread_student accounting_test case1_remedy
CASE_DIR = level3/cases
.PHONY: all clean smoke case-programs
all: case-programs $(PROGRAMS) libmtrace_lite.so vasdump
	chmod +x case1 case2 case3 case4 case5
case-programs:
	$(MAKE) -C $(CASE_DIR) all
checkpoint_workload: workloads/checkpoint_workload.c workloads/common.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $< -o $@
elf_workload: workloads/elf_workload.c workloads/common.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -fPIE -pie $< -o $@
elf_workload_nopie: workloads/elf_workload.c workloads/common.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -fno-pie -no-pie $< -o $@
observation_workload: workloads/observation_workload.c workloads/common.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -fno-optimize-sibling-calls $< -pthread -o $@
known_count: tests/known_count.c workloads/common.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $< -pthread -o $@
edge_cases: tests/edge_cases.c workloads/common.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $< -o $@
idle_student: workloads/idle_student.c workloads/common.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $< -o $@
thread_student: workloads/thread_student.c workloads/common.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $< -pthread -o $@
accounting_test: tests/accounting.c workloads/common.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $< -o $@
case1_remedy: workloads/case1_remedy.c level3/cases/common.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $< -pthread -o $@
libmtrace_lite.so: level2/support.c level2/mtrace_student.c level2/mtrace_student.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -fPIC -shared level2/support.c level2/mtrace_student.c -ldl -pthread -o $@
vasdump: level1/vasdump.py
	cp $< $@
	chmod +x $@
smoke: all
	timeout 20 env LD_PRELOAD=./libmtrace_lite.so ./edge_cases
	timeout 30 env LD_PRELOAD=./libmtrace_lite.so ./known_count
clean:
	$(MAKE) -C $(CASE_DIR) clean
	rm -f $(PROGRAMS) elf_workload_nopie libmtrace_lite.so vasdump
