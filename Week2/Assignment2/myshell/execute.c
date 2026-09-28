#include "myshell.h"
#include <fcntl.h>

static void restore_child_signals(void)
{
    struct sigaction sa = {0};
    sa.sa_handler = SIG_DFL;
    sigemptyset(&sa.sa_mask);
    if (sigaction(SIGINT, &sa, NULL) < 0 ||
        sigaction(SIGCHLD, &sa, NULL) < 0) {
        perror("myshell: child sigaction");
        _exit(1);
    }
    if (sigprocmask(SIG_SETMASK, &child_signal_mask, NULL) < 0) {
        perror("myshell: restore child signal mask");
        _exit(1);
    }
}

void execute(const struct command_line *line, const char *original)
{
    /* SIGCHLD stays blocked from reservation through PID registration. */
    reap_children();
    int slot = add_job(NULL, 0, line->background, original);
    if (slot < 0)
        return;
    struct job *job = &jobs[slot];
    int fds[2] = {-1, -1};
    if (line->count == 2 && pipe(fds) < 0) {
        perror("myshell: pipe");
        job->used = 0;
        return;
    }
    fflush(NULL);
    for (int i = 0; i < line->count; ++i) {
        pid_t pid = fork();
        if (pid < 0) {
            perror("myshell: fork");
            if (fds[0] >= 0) {
                close(fds[0]);
                close(fds[1]);
            }
            /* A partially launched job must not escape untracked. */
            job->background = 0;
            for (int j = 0; j < job->count; ++j) {
                if (kill(job->pids[j], SIGKILL) < 0 && errno != ESRCH)
                    perror("myshell: kill");
            }
            wait_foreground(job);
            return;
        }
        if (pid == 0) {
            restore_child_signals();
            if (line->count == 2) {
                int source = i == 0 ? fds[1] : fds[0];
                int target = i == 0 ? STDOUT_FILENO : STDIN_FILENO;
                close(i == 0 ? fds[0] : fds[1]);
                if (dup2(source, target) < 0) {
                    perror("myshell: dup2");
                    _exit(1);
                }
                if (source != target)
                    close(source);
            }
            /* Background commands must not consume the shell's command input. */
            if (line->background && i == 0) {
                int input = open("/dev/null", O_RDONLY);
                if (input < 0 || dup2(input, STDIN_FILENO) < 0) {
                    perror("myshell: background stdin");
                    _exit(1);
                }
                if (input != STDIN_FILENO)
                    close(input);
            }
            execvp(line->commands[i].argv[0], line->commands[i].argv);
            perror(line->commands[i].argv[0]);
            _exit(127);
        }
        job->pids[job->count++] = pid;
    }
    if (fds[0] >= 0) {
        close(fds[0]);
        close(fds[1]);
    }
    if (job->background) {
        printf("[%d]", job->id);
        for (int i = 0; i < job->count; ++i)
            printf(" %ld", (long)job->pids[i]);
        putchar('\n');
        fflush(stdout);
    } else {
        wait_foreground(job);
    }
}

void wait_foreground(struct job *job)
{
    for (;;) {
        /* One reaper owns all statuses, including background completions. */
        reap_children();
        if (job_finished(job))
            break;
        sigsuspend(&wait_signal_mask);
    }
    job->used = 0;
}
