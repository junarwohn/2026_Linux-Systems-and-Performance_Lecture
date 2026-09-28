#include "myshell.h"

/* Preserve the starter hooks as no-ops for Assignment1.
 * Signal and background job handling lives in Week2/Assignment2/myshell.
 */
struct job jobs[MAXJOBS];

void init_signals(void)
{
}

int add_job(const pid_t *pids, int count, int background, const char *command)
{
    (void)pids;
    (void)count;
    (void)background;
    (void)command;
    return -1;
}

void reap_children(void)
{
}

void cleanup_jobs(void)
{
}
