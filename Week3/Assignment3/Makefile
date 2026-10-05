CC = gcc
CFLAGS ?= -std=c11 -O2 -g -fno-omit-frame-pointer -Wall -Wextra -Wpedantic -Werror
LDFLAGS ?= -pthread

.PHONY: all clean

all: server

server: src/server.c
	$(CC) $(CFLAGS) $< $(LDFLAGS) -o $@

clean:
	$(RM) server
