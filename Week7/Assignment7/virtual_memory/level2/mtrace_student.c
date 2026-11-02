#include "mtrace_student.h"
#include <limits.h>
#include <unistd.h>

/* All access is serialized by support.c, including allocator forwarding.
 * Static open addressing avoids recursively allocating observer metadata.
 * UINTPTR_MAX is a tombstone; real glibc pointers cannot have this value.
 */
#ifndef TABLE_CAP
#define TABLE_CAP (1u << 18)
#endif
_Static_assert(TABLE_CAP > 0 && (TABLE_CAP & (TABLE_CAP - 1)) == 0,
               "TABLE_CAP must be a power of two");
#define TOMBSTONE UINTPTR_MAX
#define HIST_COUNT (sizeof(size_t) * CHAR_BIT + 1)
static struct entry { uintptr_t address; size_t size; } table[TABLE_CAP];
static uint64_t calls[4], failures[3], histogram[HIST_COUNT];
static uint64_t live_blocks, live_bytes, peak_bytes, snapshots;
static uint64_t metadata_exhaustions, untracked_releases;

static size_t hash(uintptr_t address)
{
    address >>= 4;
    address ^= address >> 17;
    address *= (uintptr_t)0x9e3779b97f4a7c15ULL;
    return (size_t)address & (TABLE_CAP - 1);
}

static void release(uintptr_t address)
{
    if (!address)
        return;
    size_t pos = hash(address);
    for (size_t i = 0; i < TABLE_CAP; i++, pos = (pos + 1) & (TABLE_CAP - 1)) {
        if (!table[pos].address)
            break;
        if (table[pos].address == address) {
            live_blocks--;
            live_bytes -= table[pos].size;
            table[pos].address = TOMBSTONE;
            return;
        }
    }
    /* Aligned APIs and observer allocations may not belong to this table. */
    untracked_releases++;
}

static void acquire(void *pointer, size_t size)
{
    uintptr_t address = (uintptr_t)pointer;
    size_t pos = hash(address), available = TABLE_CAP;
    for (size_t i = 0; i < TABLE_CAP; i++, pos = (pos + 1) & (TABLE_CAP - 1)) {
        if (table[pos].address == TOMBSTONE && available == TABLE_CAP)
            available = pos;
        if (!table[pos].address) {
            if (available == TABLE_CAP)
                available = pos;
            break;
        }
    }
    if (available == TABLE_CAP) {
        metadata_exhaustions++;
        return;
    }
    table[available].address = address;
    table[available].size = size;
    live_blocks++;
    live_bytes += size;
    if (live_bytes > peak_bytes)
        peak_bytes = live_bytes;
}

void mt_event(enum mt_op op, uintptr_t old_addr, void *result,
              size_t requested, int overflow)
{
    calls[op]++;
    if (op == MT_FREE) {
        release(old_addr);
        return;
    }
    if (op == MT_REALLOC && old_addr && (result || requested == 0))
        release(old_addr);
    if (!result || overflow) {
        failures[op]++;
        return;
    }
    size_t remaining = requested;
    unsigned bucket = 0;
    while (remaining) {
        bucket++;
        remaining >>= 1;
    }
    histogram[bucket]++;
    acquire(result, requested);
}

static void field(int fd, const char *label, uint64_t value)
{
    mt_text(fd, label);
    mt_number(fd, value);
}

void mt_report(int fd, const char *reason)
{
    field(fd, "snapshot=", ++snapshots);
    field(fd, " pid=", (uint64_t)getpid());
    mt_text(fd, " reason="); mt_text(fd, reason);
    mt_text(fd, " accounting=requested units=bytes complete=");
    mt_text(fd, metadata_exhaustions ? "no\n" : "yes\n");
    field(fd, "calls malloc=", calls[MT_MALLOC]);
    field(fd, " calloc=", calls[MT_CALLOC]);
    field(fd, " realloc=", calls[MT_REALLOC]);
    field(fd, " free=", calls[MT_FREE]); mt_text(fd, "\n");
    field(fd, "live_blocks=", live_blocks);
    field(fd, " live_bytes=", live_bytes);
    field(fd, " peak_live_bytes=", peak_bytes); mt_text(fd, "\n");
    field(fd, "metadata_capacity=", TABLE_CAP);
    field(fd, " metadata_exhaustions=", metadata_exhaustions);
    field(fd, " untracked_releases=", untracked_releases); mt_text(fd, "\n");
    field(fd, "null_results malloc=", failures[MT_MALLOC]);
    field(fd, " calloc=", failures[MT_CALLOC]);
    field(fd, " realloc=", failures[MT_REALLOC]); mt_text(fd, "\n");
    mt_text(fd, "histogram requested_bytes successful_nonnull_requests\n");
    for (unsigned i = 0; i < HIST_COUNT; i++) {
        uint64_t low = i ? (UINT64_C(1) << (i - 1)) : 0;
        uint64_t high = i == HIST_COUNT - 1 ? SIZE_MAX : i ? (UINT64_C(1) << i) - 1 : 0;
        mt_number(fd, low);
        if (high != low) { mt_text(fd, "-"); mt_number(fd, high); }
        field(fd, "=", histogram[i]); mt_text(fd, "\n");
    }
    field(fd, "end snapshot=", snapshots); mt_text(fd, "\n");
}
