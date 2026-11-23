#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#define MIB (1024UL * 1024)
static size_t page;

static void check(int ok, const char *action)
{
    if (!ok) { perror(action); exit(1); }
}

static void stage(const char *label, const volatile void *p, size_t size, pid_t child)
{
    printf("stage=%s pid=%d range=%lx-%lx bytes=%zu child=%d\n", label, getpid(),
           (unsigned long)p, (unsigned long)p + size, size, child);
    char line[32];
    if (!fgets(line, sizeof line, stdin)) exit(2);
}

static volatile unsigned char *mapping(size_t size, int fd)
{
    int flags = fd < 0 ? MAP_PRIVATE | MAP_ANONYMOUS : MAP_PRIVATE;
    int protection = fd < 0 ? PROT_READ | PROT_WRITE : PROT_READ;
    void *p = mmap(NULL, size, protection, flags, fd, 0);
    check(p != MAP_FAILED, "mmap");
    if (fd < 0) check(!madvise(p, size, MADV_NOHUGEPAGE), "MADV_NOHUGEPAGE");
    return p;
}

static void write_stride(volatile unsigned char *p, size_t size, size_t stride)
{
    for (size_t i = 0; i < size; i += stride) p[i] = 7;
}

static void read_pages(const volatile unsigned char *p, size_t size)
{
    unsigned long checksum = 0;
    for (size_t i = 0; i < size; i += page) checksum += p[i];
    printf("checksum=%lu\n", checksum);
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fputs("usage: memory_work demand|readwrite|cow|dense|sparse|partial|file [PATH default|random]\n", stderr);
        return 2;
    }
    setvbuf(stdout, NULL, _IONBF, 0);
    long base = sysconf(_SC_PAGESIZE);
    check(base > 0, "page size");
    page = (size_t)base;
    const char *mode = argv[1];
    size_t size = 64 * MIB;
    if (!strcmp(mode, "file")) {
        if (argc != 4 || (strcmp(argv[3], "default") && strcmp(argv[3], "random"))) return 2;
        int fd = open(argv[2], O_RDONLY);
        check(fd >= 0, "open");
        struct stat info;
        check(!fstat(fd, &info), "fstat");
        if (!S_ISREG(info.st_mode) || info.st_size < (off_t)size) {
            fputs("file experiment needs a regular file of at least 64 MiB\n", stderr);
            return 2;
        }
        for (int pass = 0; pass < 2; pass++) {
            volatile unsigned char *p = mapping(size, fd);
            if (!strcmp(argv[3], "random"))
                check(!madvise((void *)p, size, MADV_RANDOM), "MADV_RANDOM");
            stage(pass ? "warm-map" : "cold-request-map", p, size, 0);
            read_pages(p, size);
            stage(pass ? "warm-read" : "cold-request-read", p, size, 0);
            check(!munmap((void *)p, size), "munmap");
        }
        return close(fd) != 0;
    }
    if (!strcmp(mode, "dense") || !strcmp(mode, "sparse")) {
        size = !strcmp(mode, "dense") ? page * 256 : page * (page / 8) * 256;
        stage("baseline", NULL, 0, 0);
        volatile unsigned char *p = mapping(size, -1);
        write_stride(p, size, !strcmp(mode, "dense") ? page : page * (page / 8));
        stage("touched", p, size, 0);
        return munmap((void *)p, size) != 0;
    }
    if (!strcmp(mode, "partial")) size *= 2;
    else if (strcmp(mode, "demand") && strcmp(mode, "readwrite") && strcmp(mode, "cow")) return 2;
    volatile unsigned char *p = mapping(size, -1);
    stage("mapped", p, size, 0);
    if (!strcmp(mode, "readwrite")) {
        read_pages(p, size);
        stage("read", p, size, 0);
    }
    write_stride(p, size, page);
    stage("written", p, size, 0);
    if (!strcmp(mode, "demand")) {
        write_stride(p, size, 1);
        stage("every-byte", p, size, 0);
    } else if (!strcmp(mode, "cow")) {
        int gate[2], done[2];
        check(!pipe(gate) && !pipe(done), "pipe");
        pid_t child = fork();
        check(child >= 0, "fork");
        char byte = 'x';
        if (!child) {
            close(gate[1]); close(done[0]);
            if (read(gate[0], &byte, 1) != 1) _exit(2);
            write_stride(p, size / 4, page);
            if (write(done[1], &byte, 1) != 1) _exit(2);
            _exit(read(gate[0], &byte, 1) == 1 ? 0 : 2);
        }
        close(gate[0]); close(done[1]);
        stage("forked", p, size, child);
        check(write(gate[1], &byte, 1) == 1 && read(done[0], &byte, 1) == 1, "child touch");
        stage("child-written", p, size, child);
        check(write(gate[1], &byte, 1) == 1, "child release");
        int status;
        check(waitpid(child, &status, 0) == child && status == 0, "waitpid");
        close(gate[1]); close(done[0]);
    } else if (!strcmp(mode, "partial")) {
        for (size_t i = 0; i < size; i += 2 * page)
            check(!madvise((void *)(p + i), page, MADV_DONTNEED), "MADV_DONTNEED");
        stage("alternate-released", p, size, 0);
    }
    check(!munmap((void *)p, size), "munmap");
    stage("unmapped", p, size, 0);
    return 0;
}
