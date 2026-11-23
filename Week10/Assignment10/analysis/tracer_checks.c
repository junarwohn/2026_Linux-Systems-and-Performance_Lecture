#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void *worker(void *arg)
{
    (void)arg;
    for (int i = 0; i < 1000; i++) {
        unsigned char *p = malloc(37);
        assert(p);
        p[0] = (unsigned char)i;
        assert(p[0] == (unsigned char)i);
        free(p);
    }
    return NULL;
}

int main(int argc, char **argv)
{
    (void)argv;
    void *items[64];
    if (argc > 1) {
        for (size_t i = 0; i < 64; i++) {
            items[i] = malloc(64);
            assert(items[i]);
        }
        raise(SIGUSR1);
        free(NULL);
        for (size_t i = 0; i < 64; i++) free(items[i]);
        return 0;
    }
    volatile size_t huge = SIZE_MAX;
    unsigned char *p = malloc(4096);
    assert(p);
    memset(p, 9, 4096);
    unsigned char *q = calloc(1024, 8);
    assert(q);
    for (size_t i = 0; i < 8192; i++) assert(q[i] == 0);
    errno = 0;
    assert(!calloc(huge, 2) && errno == ENOMEM);
    errno = 0;
    assert(!realloc(p, huge) && errno == ENOMEM && p[0] == 9);
    unsigned char *r = realloc(p, 16384);
    assert(r && r[0] == 9);
    raise(SIGUSR1);
    free(NULL);
    free(r);
    free(q);
    pthread_t threads[4];
    for (int i = 0; i < 4; i++) assert(!pthread_create(&threads[i], NULL, worker, NULL));
    for (int i = 0; i < 4; i++) assert(!pthread_join(threads[i], NULL));
    return 0;
}
