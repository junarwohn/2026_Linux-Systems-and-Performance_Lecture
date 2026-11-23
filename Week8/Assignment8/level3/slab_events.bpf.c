#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_core_read.h>

#define CACHE_NAME 64
struct kmem_cache {
    const char *name;
} __attribute__((preserve_access_index));

struct activity { __u64 alloc, free, requested, allocated; };
struct cache_key { char name[CACHE_NAME]; };

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 16);
    __type(key, __u32);
    __type(value, __u64);
} diagnostics SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 4096);
    __type(key, struct cache_key);
    __type(value, struct activity);
} cache_events SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 4096);
    __type(key, __u64);
    __type(value, struct activity);
} size_events SEC(".maps");

static __always_inline void count(__u32 index, __u64 amount)
{
    __u64 *value = bpf_map_lookup_elem(&diagnostics, &index);
    if (value) __sync_fetch_and_add(value, amount);
}

static __always_inline struct activity *entry(void *map, void *key)
{
    struct activity *value = bpf_map_lookup_elem(map, key);
    if (!value) {
        struct activity zero = {};
        bpf_map_update_elem(map, key, &zero, BPF_NOEXIST);
        value = bpf_map_lookup_elem(map, key);
        if (!value) count(10, 1);
    }
    return value;
}

static __always_inline int cache(struct bpf_raw_tracepoint_args *ctx, __u32 event)
{
    count(event, 1);
    if (ctx->args[1] <= 16) return 0;
    count(event + 4, 1);
    const struct kmem_cache *source = (void *)ctx->args[2];
    const char *name = BPF_CORE_READ(source, name);
    struct cache_key key = {};
    long copied = bpf_probe_read_kernel_str(key.name, sizeof key.name, name);
    if (copied <= 1 || copied >= sizeof key.name) {
        count(copied >= sizeof key.name ? 12 : 11, 1);
        return 0;
    }
    struct activity *value = entry(&cache_events, &key);
    if (value && event == 0) __sync_fetch_and_add(&value->alloc, 1);
    if (value && event == 1) __sync_fetch_and_add(&value->free, 1);
    return 0;
}

SEC("raw_tp/kmem_cache_alloc")
int on_cache_alloc(struct bpf_raw_tracepoint_args *ctx) { return cache(ctx, 0); }

SEC("raw_tp/kmem_cache_free")
int on_cache_free(struct bpf_raw_tracepoint_args *ctx) { return cache(ctx, 1); }

SEC("raw_tp/kmalloc")
int on_kmalloc(struct bpf_raw_tracepoint_args *ctx)
{
    count(2, 1);
    if (ctx->args[1] <= 16) return 0;
    count(6, 1);
    __u64 requested = ctx->args[2], allocated = ctx->args[3];
    count(8, requested);
    count(9, allocated);
    struct activity *value = entry(&size_events, &requested);
    if (value) {
        __sync_fetch_and_add(&value->requested, requested);
        __sync_fetch_and_add(&value->allocated, allocated);
        __sync_fetch_and_add(&value->alloc, 1);
    }
    return 0;
}

SEC("raw_tp/kfree")
int on_kfree(struct bpf_raw_tracepoint_args *ctx)
{
    count(3, 1);
    if (ctx->args[1] > 16) count(7, 1);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
