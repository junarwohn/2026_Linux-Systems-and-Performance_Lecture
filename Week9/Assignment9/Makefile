CC ?= cc
CFLAGS ?= -std=c11 -O2 -g -Wall -Wextra -Werror

all: memhog psimon

memhog: src/memhog.c
	$(CC) $(CFLAGS) -o $@ $<

psimon: src/psimon.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f memhog psimon

.PHONY: all clean
