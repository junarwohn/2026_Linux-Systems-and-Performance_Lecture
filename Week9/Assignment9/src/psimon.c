#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t stop;
static void interrupted(int sig) { (void)sig; stop = 1; }

static double now(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

static long positive(const char *s, long max)
{
    char *end;
    errno = 0;
    long n = strtol(s, &end, 10);
    return !errno && *s >= '0' && *s <= '9' && !*end && n > 0 && n <= max ? n : -1;
}

static int polling(const char *path, int milliseconds)
{
    unsigned long long old[2] = {0};
    double prev = 0;
    while (!stop) {
        FILE *f = fopen(path, "r");
        if (!f) { perror(path); return 1; }
        double today = now(), avg[2];
        unsigned long long total[2];
        int n = fscanf(f, "some avg10=%lf avg60=%*f avg300=%*f total=%llu\n"
                       "full avg10=%lf avg60=%*f avg300=%*f total=%llu",
                       &avg[0], &total[0], &avg[1], &total[1]);
        fclose(f);
        if (n != 4) { fputs("invalid or unsupported PSI format\n", stderr); return 1; }
        printf("t=%.6f elapsed_s=%.6f", today, prev ? today-prev : 0);
        for (int i = 0; i < 2; i++) {
            const char *label = i ? "full" : "some";
            printf(" %s_avg10=%.2f %s_total_us=%llu %s_pct=", label, avg[i], label, total[i], label);
            if (prev && total[i] >= old[i]) printf("%.4f", (total[i]-old[i])/((today-prev)*10000));
            else fputs("NA", stdout);
            old[i] = total[i];
        }
        puts("");
        prev = today;
        if (!stop) poll(NULL, 0, milliseconds);
    }
    return 0;
}

int main(int argc, char **argv)
{
    const char *path = "/proc/pressure/memory", *kind = NULL;
    long interval = 1000, stall = 0, window = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--interval") && i+1 < argc) interval = positive(argv[++i], 60000);
        else if (!strcmp(argv[i], "--path") && i+1 < argc) path = argv[++i];
        else if (!strcmp(argv[i], "--trigger") && !kind && i+3 < argc) {
            kind = argv[++i];
            stall = positive(argv[++i], 10000000);
            window = positive(argv[++i], 10000000);
        } else goto usage;
    }
    if (interval < 1 || (kind && ((strcmp(kind, "some") && strcmp(kind, "full")) ||
                                  window < 500000 || stall < 1 || stall > window))) goto usage;
    struct sigaction action = {.sa_handler=interrupted};
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) || sigaction(SIGTERM, &action, NULL)) { perror("sigaction"); return 1; }
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (kind) {
        int fd = open(path, O_RDWR | O_NONBLOCK | O_CLOEXEC);
        char request[64];
        int size = snprintf(request, sizeof request, "%s %ld %ld", kind, stall, window);
        if (fd < 0 || write(fd, request, (size_t)size) != size) {
            fprintf(stderr, "trigger registration failed: %s; fallback=polling\n", strerror(errno));
            if (fd >= 0) close(fd);
        } else {
            printf("t=%.6f registered metric=%s stall_us=%ld window_us=%ld\n", now(), kind, stall, window);
            struct pollfd f = {fd, POLLPRI, 0};
            int status = 0;
            while (!stop) {
                if (poll(&f, 1, 500) < 0 && errno != EINTR) { perror("poll PSI"); status=1; break; }
                if (f.revents & (POLLERR | POLLHUP | POLLNVAL)) { fputs("PSI descriptor failed\n", stderr); status=1; break; }
                if (f.revents & POLLPRI) printf("t=%.6f event metric=%s\n", now(), kind);
            }
            close(fd);
            return status;
        }
    }
    return polling(path, (int)interval);

usage:
    fputs("usage: psimon [--interval MS] [--trigger some|full STALL_US WINDOW_US] [--path PRESSURE_FILE]\n", stderr);
    return 2;
}
