#include "myshell.h"

void execute(const struct command_line *line, const char *original)
{
    (void)original;
    if (line->background) {
        fprintf(stderr, "myshell: background execution belongs to Assignment2\n");
        return;
    }
    if (line->count == 1) {
        /* Flush shell output before the child starts writing to stdout. */
        fflush(NULL);
        pid_t pid = fork();
        if (pid < 0) {
            perror("myshell: fork");
            return;
        }
        if (pid == 0) {
            execvp(line->commands[0].argv[0], line->commands[0].argv);
            perror(line->commands[0].argv[0]);
            _exit(127);
        }
        struct job job = { .count = 1, .pids = {pid} };
        wait_foreground(&job);
    } else {
        int fds[2];
        if (pipe(fds) < 0) {
            perror("myshell: pipe");
            return;
        }
        struct job job = {0};
        fflush(NULL);
        for (int i = 0; i < 2; ++i) {
            pid_t pid = fork();
            if (pid < 0) {
                perror("myshell: fork");
                close(fds[0]);
                close(fds[1]);
                /* Abort a partially launched pipeline, even if it never writes. */
                if (job.count && kill(job.pids[0], SIGKILL) < 0 && errno != ESRCH)
                    perror("myshell: kill");
                wait_foreground(&job);
                return;
            }
            if (pid == 0) {
                int source = i == 0 ? fds[1] : fds[0];
                int target = i == 0 ? STDOUT_FILENO : STDIN_FILENO;
                close(i == 0 ? fds[0] : fds[1]);
                if (dup2(source, target) < 0) {
                    perror("myshell: dup2");
                    _exit(1);
                }
                if (source != target)
                    close(source);
                execvp(line->commands[i].argv[0], line->commands[i].argv);
                perror(line->commands[i].argv[0]);
                _exit(127);
            }
            job.pids[job.count++] = pid;
        }
        /* Launch both children and release the parent's pipe ends before waiting. */
        close(fds[0]);
        close(fds[1]);
        wait_foreground(&job);
    }
}

void wait_foreground(struct job *job)
{
    for (int i = 0; i < job->count; ++i) {
        if (job->reaped[i])
            continue;
        pid_t result;
        do {
            result = waitpid(job->pids[i], &job->status[i], 0);
        } while (result < 0 && errno == EINTR);
        if (result < 0) {
            perror("myshell: waitpid");
            continue;
        }
        job->reaped[i] = 1;
    }
}
