#include "../workloads/common.h"
#include <stdint.h>

int main(void)
{
    setvbuf(stdin, NULL, _IONBF, 0);
    setvbuf(stdout, NULL, _IONBF, 0);
    checkpoint("baseline");
    void *p = malloc(17);
    if (!p) return 1;
    checkpoint("malloc17");
    p = realloc(p, 113);
    if (!p) return 1;
    checkpoint("realloc113");
    volatile size_t huge = SIZE_MAX;
    void *q = realloc(p, huge);
    if (q) return 1;
    checkpoint("failed_realloc");
    void *zero = calloc(0, 9);
    if (!zero) return 1;
    checkpoint("zero_block");
    q = realloc(p, 0);
    if (q) return 1;
    checkpoint("zero_realloc");
    errno = EDOM;
    free(zero);
    free(NULL);
    if (errno != EDOM) return 1;
    checkpoint("released");
    return 0;
}
