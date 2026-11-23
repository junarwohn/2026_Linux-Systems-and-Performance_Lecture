#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef LIVE_CAP
#define LIVE_CAP (1u << 16)
#endif
#define SITES (1u << 12)
#define TOMBSTONE ((uintptr_t)1)
#define BOOT_BYTES (256u * 1024)

struct allocation { uintptr_t pointer, site; size_t size; };
struct aggregate { uintptr_t site; size_t bytes, objects; };

static struct allocation live[LIVE_CAP];
static struct aggregate sites[SITES];
static size_t used, bytes, dropped;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_once_t once = PTHREAD_ONCE_INIT;
static _Thread_local int internal;
_Static_assert(ATOMIC_INT_LOCK_FREE == 2, "signal flag must be lock-free");
static _Atomic int requested;
static void *(*real_malloc)(size_t);
static void *(*real_calloc)(size_t, size_t);
static void *(*real_realloc)(void *, size_t);
static void (*real_free)(void *);
static _Alignas(max_align_t) unsigned char boot[BOOT_BYTES];
static _Atomic size_t boot_used;

static int boot_pointer(const void *pointer)
{
    uintptr_t p = (uintptr_t)pointer, base = (uintptr_t)boot;
    return p > base && p < base + sizeof boot;
}

static void *bootstrap(size_t size)
{
    const size_t header = _Alignof(max_align_t), mask = header - 1;
    if (size > BOOT_BYTES - 2 * header) { errno = ENOMEM; return NULL; }
    size_t length = header + (((size ? size : 1) + mask) & ~mask);
    size_t offset = atomic_load(&boot_used);
    do {
        if (offset > sizeof boot - length) { errno = ENOMEM; return NULL; }
    } while (!atomic_compare_exchange_weak(&boot_used, &offset, offset + length));
    memcpy(boot + offset, &size, sizeof size);
    return boot + offset + header;
}

static size_t bootstrap_size(const void *pointer)
{
    size_t size;
    memcpy(&size, (const unsigned char *)pointer - _Alignof(max_align_t), sizeof size);
    return size;
}

static void request(int sig)
{
    (void)sig;
    atomic_store_explicit(&requested, 1, memory_order_relaxed);
}

static void resolve(void)
{
    real_malloc = dlsym(RTLD_NEXT, "malloc");
    real_calloc = dlsym(RTLD_NEXT, "calloc");
    real_realloc = dlsym(RTLD_NEXT, "realloc");
    real_free = dlsym(RTLD_NEXT, "free");
    if (!real_malloc || !real_calloc || !real_realloc || !real_free) {
        const char message[] = "alloctrace: unable to resolve allocator\n";
        (void)write(STDERR_FILENO, message, sizeof message - 1);
        _exit(126);
    }
    struct sigaction action = {.sa_handler = request, .sa_flags = SA_RESTART};
    sigemptyset(&action.sa_mask);
    sigaction(SIGUSR1, &action, NULL);
}

static void begin(void)
{
    int saved = errno;
    internal = 1;
    pthread_once(&once, resolve);
    errno = saved;
}

static size_t hash(uintptr_t pointer, size_t capacity)
{
    pointer ^= pointer >> 17;
    pointer *= UINT64_C(0x9e3779b97f4a7c15);
    return (pointer ^ (pointer >> 28)) % capacity;
}

static struct allocation remove_entry(uintptr_t pointer)
{
    struct allocation absent = {0};
    if (!pointer) return absent;
    size_t first = hash(pointer, LIVE_CAP);
    for (size_t n = 0; n < LIVE_CAP; n++) {
        struct allocation *row = &live[(first+n) % LIVE_CAP];
        if (!row->pointer) break;
        if (row->pointer == pointer) {
            struct allocation value = *row;
            row->pointer = TOMBSTONE;
            bytes -= value.size;
            used--;
            return value;
        }
    }
    return absent;
}

static void insert(struct allocation value)
{
    size_t first = hash(value.pointer, LIVE_CAP);
    struct allocation *slot = NULL;
    for (size_t n = 0; n < LIVE_CAP; n++) {
        struct allocation *row = &live[(first+n) % LIVE_CAP];
        if (row->pointer <= TOMBSTONE && !slot) slot = row;
        if (!row->pointer) break;
    }
    if (slot && SIZE_MAX - bytes >= value.size) {
        *slot = value;
        bytes += value.size;
        used++;
    } else {
        dropped++;
    }
}

static void output(const char *s)
{
    size_t length = strlen(s);
    while (length) {
        ssize_t n = write(STDERR_FILENO, s, length);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        s += n;
        length -= (size_t)n;
    }
}

static void number(size_t value, int base)
{
    char text[3 * sizeof(size_t) + 1], *end = text + sizeof text;
    *--end = 0;
    do {
        *--end = "0123456789abcdef"[value % (size_t)base];
        value /= (size_t)base;
    } while (value);
    output(end);
}

static void field(const char *key, size_t value)
{
    output(key);
    number(value, 10);
}

static void dump(const char *reason)
{
    struct aggregate top[10] = {{0}};
    size_t site_drops = 0, total_bytes, total_objects, total_drops;
    pthread_mutex_lock(&lock);
    memset(sites, 0, sizeof sites);
    for (size_t i = 0; i < LIVE_CAP; i++) {
        const struct allocation *a = &live[i];
        if (a->pointer <= TOMBSTONE) continue;
        size_t first = hash(a->site, SITES);
        size_t n = 0;
        for (; n < SITES; n++) {
            struct aggregate *s = &sites[(first+n) % SITES];
            if (!s->objects || s->site == a->site) {
                s->site = a->site;
                s->objects++;
                s->bytes += a->size;
                break;
            }
        }
        if (n == SITES) site_drops++;
    }
    for (size_t i = 0; i < SITES; i++) {
        if (!sites[i].objects) continue;
        for (size_t j = 0; j < 10; j++) {
            if (!top[j].objects || sites[i].bytes > top[j].bytes) {
                memmove(top+j+1, top+j, (9-j) * sizeof *top);
                top[j] = sites[i];
                break;
            }
        }
    }
    total_bytes = bytes;
    total_objects = used;
    total_drops = dropped;
    pthread_mutex_unlock(&lock);
    output("alloctrace reason=");
    output(reason);
    field(" pid=", (size_t)getpid());
    field(" live_bytes=", total_bytes);
    field(" live_objects=", total_objects);
    field(" dropped=", total_drops);
    field(" ungrouped_objects=", site_drops);
    output("\n");
    for (size_t i = 0; i < 10 && top[i].objects; i++) {
        Dl_info location;
        output("site=0x");
        number(top[i].site, 16);
        if (dladdr((void *)top[i].site, &location)) {
            output(" module=");
            output(location.dli_fname);
            output(" offset=0x");
            number(top[i].site - (uintptr_t)location.dli_fbase, 16);
        }
        field(" bytes=", top[i].bytes);
        field(" objects=", top[i].objects);
        output("\n");
    }
}

static void finish(struct allocation value, int saved)
{
    if (value.pointer) {
        pthread_mutex_lock(&lock);
        insert(value);
        pthread_mutex_unlock(&lock);
    }
    if (atomic_exchange_explicit(&requested, 0, memory_order_relaxed))
        dump("SIGUSR1");
    internal = 0;
    errno = saved;
}

#define SITE ((uintptr_t)__builtin_extract_return_addr(__builtin_return_address(0)))

void *malloc(size_t size)
{
    if (internal) return real_malloc ? real_malloc(size) : bootstrap(size);
    begin();
    void *pointer = real_malloc(size);
    finish((struct allocation){(uintptr_t)pointer, SITE, size}, errno);
    return pointer;
}

void *calloc(size_t count, size_t size)
{
    if (internal) {
        if (real_calloc) return real_calloc(count, size);
        size_t n;
        if (__builtin_mul_overflow(count, size, &n)) { errno = ENOMEM; return NULL; }
        void *p = bootstrap(n);
        if (p) memset(p, 0, n);
        return p;
    }
    begin();
    void *pointer = real_calloc(count, size);
    size_t n = 0;
    if (!pointer || __builtin_mul_overflow(count, size, &n)) n = 0;
    finish((struct allocation){(uintptr_t)pointer, SITE, n}, errno);
    return pointer;
}

void free(void *pointer)
{
    if (internal) {
        if (real_free && !boot_pointer(pointer)) real_free(pointer);
        return;
    }
    begin();
    int saved = errno;
    if (!boot_pointer(pointer)) {
        pthread_mutex_lock(&lock);
        remove_entry((uintptr_t)pointer);
        pthread_mutex_unlock(&lock);
        real_free(pointer);
    }
    finish((struct allocation){0}, saved);
}

void *realloc(void *pointer, size_t size)
{
    if (boot_pointer(pointer)) {
        if (!size) return NULL;
        void *result = internal ? (real_malloc ? real_malloc(size) : bootstrap(size)) : malloc(size);
        if (result) {
            size_t available = bootstrap_size(pointer);
            memcpy(result, pointer, size < available ? size : available);
        }
        return result;
    }
    if (internal) {
        if (real_realloc) return real_realloc(pointer, size);
        if (!pointer) return bootstrap(size);
        errno = ENOMEM;
        return NULL;
    }
    begin();
    pthread_mutex_lock(&lock);
    struct allocation old = remove_entry((uintptr_t)pointer);
    pthread_mutex_unlock(&lock);
    void *result = real_realloc(pointer, size);
    struct allocation after = result ? (struct allocation){(uintptr_t)result, SITE, size} :
                                         size ? old : (struct allocation){0};
    finish(after, errno);
    return result;
}

__attribute__((destructor)) static void final_dump(void)
{
    begin();
    dump("exit");
    internal = 0;
}
