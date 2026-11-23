CC ?= cc
CFLAGS ?= -O2 -g -Wall -Wextra -Werror
CLANG ?= clang
BPF_CFLAGS ?= -O2 -g -target bpf

all: memory_work slab_events.bpf.o

memory_work: level1/memory_work.c
	$(CC) $(CFLAGS) -o $@ $<

slab_events.bpf.o: level3/slab_events.bpf.c
	$(CLANG) $(BPF_CFLAGS) -I/usr/include/$(shell $(CC) -dumpmachine) -c $< -o $@

clean:
	rm -f memory_work slab_events.bpf.o

.PHONY: all clean
