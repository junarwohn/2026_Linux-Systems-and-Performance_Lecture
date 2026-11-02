#include "common.h"
#include <pthread.h>
#include <stdatomic.h>

static atomic_int ready, go;
static void *worker(void *unused)
{
    (void)unused;
    atomic_fetch_add(&ready, 1);
    while (!atomic_load(&go))
        wait_ms(1);
    for (unsigned i = 0; i < 100000; i++) {
        void *p = alloc_bytes(32);
        ((volatile unsigned char *)p)[0] = (unsigned char)i;
        free(p);
    }
    return NULL;
}
int main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stdin, NULL, _IONBF, 0);
    pthread_t threads[4];
    for (int i = 0; i < 4; i++)
        if (pthread_create(&threads[i], NULL, worker, NULL))
            return 1;
    while (atomic_load(&ready) != 4)
        wait_ms(1);
    checkpoint("before_pairs");
    atomic_store(&go, 1);
    for (int i = 0; i < 4; i++)
        pthread_join(threads[i], NULL);
    checkpoint("after_pairs");
    return 0;
}
