#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <math.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define MIB ((size_t)1024 * 1024)
#define MAX_BYTES (UINT64_C(16) * 1024 * MIB)
static volatile sig_atomic_t stop;
static size_t page;

static void interrupted(int sig) { (void)sig; stop = 1; }
static void fail(const char *what) { perror(what); exit(1); }
static void invalid(const char *what) { fprintf(stderr, "invalid argument: %s\n", what); exit(2); }

static double now(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) fail("clock_gettime");
    return t.tv_sec + t.tv_nsec / 1e9;
}

static void until(double end)
{
    while (!stop) {
        double remaining = end - now();
        if (remaining <= 0) return;
        struct timespec t = {(time_t)remaining, (long)((remaining - (time_t)remaining) * 1e9)};
        if (!nanosleep(&t, NULL) || errno != EINTR) return;
    }
}

static double number(const char *text, double low, double high)
{
    char *end;
    errno = 0;
    double value = strtod(text, &end);
    if (errno || text == end || *end || !isfinite(value) || value < low || value > high) invalid(text);
    return value;
}

static uint64_t integer(const char *text, uint64_t max)
{
    char *end;
    errno = 0;
    uint64_t value = strtoull(text, &end, 10);
    if (errno || *text < '0' || *text > '9' || *end || value > max) invalid(text);
    return value;
}

static size_t size_arg(const char *text)
{
    char *end;
    errno = 0;
    uint64_t value = strtoull(text, &end, 10);
    size_t factor = 1;
    if (!strcmp(end, "KiB")) factor = 1024;
    else if (!strcmp(end, "MiB")) factor = MIB;
    else if (!strcmp(end, "GiB")) factor = 1024 * MIB;
    else if (*end) invalid(text);
    if (errno || *text < '0' || *text > '9' || !value || value > MAX_BYTES / factor || value > SIZE_MAX / factor) invalid(text);
    return (size_t)value * factor;
}

static unsigned char *map(size_t size)
{
    void *p = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) fail("mmap");
    return p;
}

static void unmap(unsigned char *p, size_t size)
{
    if (munmap(p, size)) fail("munmap");
}

static uint64_t rng(uint64_t *state)
{
    uint64_t x = *state;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    *state = x;
    return x * UINT64_C(2685821657736338717);
}

static size_t fill(unsigned char *p, size_t bytes, double rate, uint64_t *state)
{
    double start = now();
    printf("t=%.6f phase=fill_begin target_bytes=%zu\n", start, bytes);
    size_t written = 0, pages = 0;
    for (size_t i = 0; i < bytes && !stop; i += page) {
        size_t chunk = bytes - i < page ? bytes - i : page;
        if (state) {
            for (size_t j = 0; j < chunk;) {
                uint64_t value = rng(state);
                size_t n = chunk - j < sizeof value ? chunk - j : sizeof value;
                memcpy(p + i + j, &value, n);
                j += n;
            }
        } else {
            ((volatile unsigned char *)p)[i] = 1;
        }
        pages++;
        written += chunk;
        if (rate > 0 && (pages % 64 == 0 || written == bytes))
            until(start + written / (rate * MIB));
    }
    double end = now();
    printf("t=%.6f phase=fill bytes=%zu pages=%zu elapsed_s=%.6f MiB_s=%.3f\n",
           end, written, pages, end-start, written / (end-start) / MIB);
    return pages;
}

static void anon(size_t size, double rate, double hold, int random, uint64_t seed)
{
    unsigned char *p = map(size);
    uint64_t state = seed ? seed : UINT64_C(0x61cb2ea75);
    size_t count = fill(p, size, rate, random ? &state : NULL);
    double start = now();
    until(start + hold);
    printf("t=%.6f phase=hold elapsed_s=%.6f\n", now(), now()-start);
    if (random && !stop) {
        uint32_t *order = malloc(count * sizeof *order);
        if (!order) fail("retouch order");
        for (size_t i = 0; i < count; i++) order[i] = (uint32_t)i;
        for (size_t i = count; i > 1; i--) {
            size_t j = rng(&state) % i;
            uint32_t swap = order[j];
            order[j] = order[i-1];
            order[i-1] = swap;
        }
        uint64_t sum = 0;
        size_t read = 0;
        start = now();
        while (read < count && !stop) {
            sum += ((volatile unsigned char *)p)[page * order[read]];
            read++;
        }
        printf("t=%.6f phase=retouch pages=%zu elapsed_s=%.6f seed=%"PRIu64" checksum=%"PRIu64"\n",
               now(), read, now()-start, seed, sum);
        free(order);
    }
    unmap(p, size);
    printf("t=%.6f phase=release bytes=%zu\n", now(), size);
}

static void file(const char *path, double rate, unsigned loops)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) fail("open file");
    struct stat st;
    if (fstat(fd, &st)) fail("fstat");
    if (!S_ISREG(st.st_mode)) invalid(path);
    unsigned char *buf = malloc(MIB);
    if (!buf) fail("read buffer");
    uint64_t all = 0;
    double begin = now();
    printf("t=%.6f phase=file_begin file_bytes=%jd loops=%u\n", begin, (intmax_t)st.st_size, loops);
    for (unsigned pass = 1; pass <= loops && !stop; pass++) {
        if (lseek(fd, 0, SEEK_SET) < 0) fail("rewind");
        uint64_t bytes = 0;
        double start = now();
        while (!stop) {
            ssize_t n = read(fd, buf, MIB);
            if (n < 0 && errno == EINTR) continue;
            if (n < 0) fail("read");
            if (!n) break;
            bytes += (uint64_t)n;
            if (rate > 0) until(start + bytes / (rate * MIB));
        }
        double elapsed = now()-start;
        all += bytes;
        printf("t=%.6f pass=%u bytes=%"PRIu64" elapsed_s=%.6f throughput_MiB_s=%.3f\n",
               now(), pass, bytes, elapsed, bytes / elapsed / MIB);
    }
    double elapsed = now()-begin;
    printf("t=%.6f phase=file_end bytes=%"PRIu64" elapsed_s=%.6f throughput_MiB_s=%.3f\n",
           now(), all, elapsed, all / elapsed / MIB);
    free(buf);
    if (close(fd)) fail("close");
}

static int ascending(const void *a, const void *b)
{
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

static void window(double *samples, size_t n, double start, double end)
{
    if (!n) return;
    qsort(samples, n, sizeof *samples, ascending);
    printf("window_start_s=%.6f window_end_s=%.6f n=%zu p50_us=%.2f p99_us=%.2f max_us=%.2f\n",
           start, end, n, samples[(n+1)/2-1], samples[(99*n+99)/100-1], samples[n-1]);
}

static void probe(size_t size, unsigned interval, double seconds)
{
    double samples[5002], start = now();
    size_t n = 0;
    const double deadline = seconds > 0 ? start + seconds : INFINITY;
    while (!stop && now() < deadline) {
        double begin = now();
        unsigned char *p = map(size);
        for (size_t i = 0; i < size; i += page) ((volatile unsigned char *)p)[i] = 1;
        double end = now();
        unmap(p, size);
        samples[n++] = (end-begin) * 1e6;
        printf("t=%.6f probe_us=%.3f\n", end, samples[n-1]);
        if (end-start >= 5 || n == sizeof samples / sizeof *samples) {
            window(samples, n, start, end);
            start = end;
            n = 0;
        }
        double next = begin + interval / 1000.;
        until(next < deadline ? next : deadline);
    }
    window(samples, n, start, now());
}

static void commit(void)
{
    FILE *f = fopen("/proc/meminfo", "r");
    if (!f) fail("meminfo");
    char line[256];
    while (fgets(line, sizeof line, f))
        if (!strncmp(line, "Committed_AS:", 13) || !strncmp(line, "MemAvailable:", 13))
            printf("t=%.6f %s", now(), line);
    fclose(f);
}

static void adjust(int adj)
{
    FILE *f = fopen("/proc/self/oom_score_adj", "w");
    if (!f) fail("open oom_score_adj");
    if (fprintf(f, "%d\n", adj) < 0 || fclose(f)) fail("write oom_score_adj");
    f = fopen("/proc/self/oom_score_adj", "r");
    int value;
    if (!f || fscanf(f, "%d", &value) != 1) fail("read oom_score_adj");
    fclose(f);
    if (value != adj) invalid("oom_score_adj readback differs");
    printf("t=%.6f effective_adj=%d pid=%ld\n", now(), value, (long)getpid());
}

static void reserve(size_t size, double fraction, size_t step, double hold)
{
    commit();
    unsigned char *p = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    int error = p == MAP_FAILED ? errno : 0;
    printf("t=%.6f phase=reserve requested_B=%zu address=%p errno=%d\n", now(), size, p, error);
    if (error) { errno = error; fail("reserve mmap"); }
    commit();
    size_t target = (size_t)(size * fraction);
    size_t pages_per_step = step / page;
    if (!pages_per_step) pages_per_step = 1;
    size_t written = 0, batch = 0;
    for (size_t i = 0; i < target && !stop; i += page) {
        ((volatile unsigned char *)p)[i] = 1;
        written++;
        if (++batch == pages_per_step || target-i <= page) {
            printf("t=%.6f phase=touch pages=%zu touched_B=%zu\n", now(), written, written*page);
            commit();
            until(now() + .3);
            batch = 0;
        }
    }
    until(now() + hold);
    unmap(p, size);
}

int main(int argc, char **argv)
{
    if (argc < 2 || !strcmp(argv[1], "--help")) {
        puts("memhog anon SIZE [--rate MiB/s] [--hold SEC] [--retouch random] [--seed N]\n"
             "memhog file PATH [--rate MiB/s] [--loops N]\n"
             "memhog probe [--interval MS] [--size KiB] [--seconds SEC]\n"
             "memhog burst BASE --burst SIZE [--cycles N] [--period SEC]\n"
             "memhog reserve SIZE [--touch FRACTION] [--step SIZE] [--hold SEC]\n"
             "All modes accept --adj N; Ctrl-C/SIGTERM stop the workload.");
        return argc < 2 ? 2 : 0;
    }
    setvbuf(stdout, NULL, _IOLBF, 0);
    struct sigaction action = {.sa_handler=interrupted};
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) || sigaction(SIGTERM, &action, NULL)) fail("sigaction");
    long base = sysconf(_SC_PAGESIZE);
    if (base <= 0) fail("page size");
    page = (size_t)base;
    int a = !strcmp(argv[1], "anon"), f = !strcmp(argv[1], "file"),
        p = !strcmp(argv[1], "probe"), b = !strcmp(argv[1], "burst"),
        r = !strcmp(argv[1], "reserve");
    if (!(a || f || p || b || r) || (!p && argc < 3)) invalid(argv[1]);
    size_t size = p ? 64 * 1024 : f ? 0 : size_arg(argv[2]);
    size_t burst = 0, step = MIB;
    unsigned loops = 1, interval = 100, cycles = 12;
    double rate = 0, hold = a ? 30 : 0, period = 1, fraction = 0, seconds = 0;
    uint64_t seed = 1;
    int random = 0, adj = 1001;
    for (int i = p ? 2 : 3; i < argc; i += 2) {
        if (i + 1 == argc) invalid(argv[i]);
        const char *key = argv[i], *value = argv[i+1];
        if ((a || f) && !strcmp(key, "--rate")) rate = number(value, 0, 65536);
        else if ((a || r) && !strcmp(key, "--hold")) hold = number(value, 0, 3600);
        else if (a && !strcmp(key, "--seed")) seed = integer(value, UINT64_MAX);
        else if (a && !strcmp(key, "--retouch") && !strcmp(value, "random")) random = 1;
        else if (f && !strcmp(key, "--loops")) loops = (unsigned)integer(value, 100000);
        else if (p && !strcmp(key, "--interval")) interval = (unsigned)integer(value, 60000);
        else if (p && !strcmp(key, "--seconds")) seconds = number(value, .01, 3600);
        else if (p && !strcmp(key, "--size")) size = (size_t)integer(value, (64 * MIB)/1024) * 1024;
        else if (b && !strcmp(key, "--burst")) burst = size_arg(value);
        else if (b && !strcmp(key, "--cycles")) cycles = (unsigned)integer(value, 3600);
        else if (b && !strcmp(key, "--period")) period = number(value, .01, 3600);
        else if (r && !strcmp(key, "--touch")) fraction = number(value, 0, 1);
        else if (r && !strcmp(key, "--step")) step = size_arg(value);
        else if (!strcmp(key, "--adj")) adj = *value == '-' ? -(int)integer(value+1, 1000) : (int)integer(value, 1000);
        else invalid(key);
    }
    if (!loops || !interval || (p && !size) || (b && (!cycles || !burst || burst > MAX_BYTES - size || burst > SIZE_MAX - size))) invalid(argv[1]);
    if (adj != 1001) adjust(adj);
    if (a) anon(size, rate, hold, random, seed);
    if (f) file(argv[2], rate, loops);
    if (p) probe(size, interval, seconds);
    if (r) reserve(size, fraction, step, hold);
    if (b) {
        unsigned char *working = map(size);
        fill(working, size, 0, NULL);
        for (unsigned i = 0; i < cycles && !stop; i++) {
            double start = now();
            unsigned char *p = map(burst);
            fill(p, burst, 0, NULL);
            printf("t=%.6f phase=burst cycle=%u alloc_s=%.6f\n", now(), i+1, now()-start);
            until(start + period / 2);
            unmap(p, burst);
            until(start + period);
        }
        unmap(working, size);
    }
    return 0;
}
