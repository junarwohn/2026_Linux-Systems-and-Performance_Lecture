#include "myshell.h"
#include <sys/select.h>
#include <time.h>

/* No fg/bg commands or terminal process-group management. */
struct job jobs[MAXJOBS];
sigset_t child_signal_mask;
sigset_t wait_signal_mask;

static void on_child(int signo)
{
    /* Interrupt pselect/sigsuspend; waitpid is the source of child status. */
    (void)signo;
}

void init_signals(void)
{
    sigset_t blocked;
    sigemptyset(&blocked);
    sigaddset(&blocked, SIGCHLD);
    if (sigprocmask(SIG_BLOCK, &blocked, &child_signal_mask) < 0) {
        perror("myshell: block SIGCHLD");
        exit(EXIT_FAILURE);
    }
    wait_signal_mask = child_signal_mask;
    sigdelset(&wait_signal_mask, SIGCHLD);

    struct sigaction sa = {0};
    sa.sa_handler = SIG_IGN;
    sigemptyset(&sa.sa_mask);
    if (sigaction(SIGINT, &sa, NULL) < 0) {
        perror("myshell: sigaction SIGINT");
        exit(EXIT_FAILURE);
    }
    sa.sa_handler = on_child;
    sa.sa_flags = SA_NOCLDSTOP;
    if (sigaction(SIGCHLD, &sa, NULL) < 0) {
        perror("myshell: sigaction SIGCHLD");
        exit(EXIT_FAILURE);
    }
}

int add_job(const pid_t *pids, int count, int background, const char *command)
{
    if (count < 0 || count > 2 || (count && !pids) || !command) {
        errno = EINVAL;
        return -1;
    }
    for (int i = 0; i < MAXJOBS; ++i) {
        if (jobs[i].used)
            continue;
        jobs[i] = (struct job){ .used = 1, .id = i + 1,
                               .background = background, .count = count };
        for (int j = 0; j < count; ++j)
            jobs[i].pids[j] = pids[j];
        snprintf(jobs[i].command, sizeof(jobs[i].command), "%s", command);
        size_t length = strlen(jobs[i].command);
        while (length && strchr(" \t\r\n", jobs[i].command[length - 1]))
            jobs[i].command[--length] = '\0';
        if (background && length && jobs[i].command[length - 1] == '&') {
            jobs[i].command[--length] = '\0';
            while (length && strchr(" \t\r\n", jobs[i].command[length - 1]))
                jobs[i].command[--length] = '\0';
        }
        return i;
    }
    fprintf(stderr, "myshell: job table full\n");
    return -1;
}

int job_finished(const struct job *job)
{
    for (int i = 0; i < job->count; ++i) {
        if (!job->reaped[i])
            return 0;
    }
    return 1;
}

void reap_children(void)
{
    for (;;) {
        int status;
        pid_t pid = waitpid(-1, &status, WNOHANG);
        if (pid < 0 && errno == EINTR)
            continue;
        if (pid <= 0) {
            if (pid < 0 && errno != ECHILD)
                perror("myshell: waitpid");
            break;
        }
        for (int i = 0; i < MAXJOBS; ++i) {
            struct job *job = &jobs[i];
            if (!job->used)
                continue;
            for (int j = 0; j < job->count; ++j) {
                if (job->pids[j] == pid && !job->reaped[j]) {
                    job->status[j] = status;
                    job->reaped[j] = 1;
                    break;
                }
            }
        }
    }
    for (int i = 0; i < MAXJOBS; ++i) {
        struct job *job = &jobs[i];
        if (job->used && job->background && job_finished(job)) {
            printf("[%d] Done  %s\n", job->id, job->command);
            fflush(stdout);
            job->used = 0;
        }
    }
}

void cleanup_jobs(void)
{
    /* Exit/EOF policy: kill only tracked, unreaped direct children.
     * Bound shutdown waiting to one second, even for uninterruptible tasks. */
    reap_children();
    for (int i = 0; i < MAXJOBS; ++i) {
        if (!jobs[i].used)
            continue;
        for (int j = 0; j < jobs[i].count; ++j) {
            if (!jobs[i].reaped[j] &&
                kill(jobs[i].pids[j], SIGKILL) < 0 && errno != ESRCH)
                perror("myshell: kill");
        }
    }
    struct timespec deadline;
    if (clock_gettime(CLOCK_MONOTONIC, &deadline) < 0) {
        perror("myshell: clock_gettime");
        return;
    }
    ++deadline.tv_sec;
    for (;;) {
        reap_children();
        int pending = 0;
        for (int i = 0; i < MAXJOBS; ++i)
            pending |= jobs[i].used && !job_finished(&jobs[i]);
        if (!pending)
            return;
        struct timespec now, remaining;
        if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) {
            perror("myshell: clock_gettime");
            return;
        }
        remaining.tv_sec = deadline.tv_sec - now.tv_sec;
        remaining.tv_nsec = deadline.tv_nsec - now.tv_nsec;
        if (remaining.tv_nsec < 0) {
            --remaining.tv_sec;
            remaining.tv_nsec += 1000000000L;
        }
        if (remaining.tv_sec < 0 ||
            (remaining.tv_sec == 0 && remaining.tv_nsec == 0)) {
            fprintf(stderr, "myshell: shutdown timed out waiting for children\n");
            return;
        }
        if (pselect(0, NULL, NULL, NULL, &remaining, &wait_signal_mask) < 0 &&
            errno != EINTR) {
            perror("myshell: shutdown pselect");
            return;
        }
    }
}
