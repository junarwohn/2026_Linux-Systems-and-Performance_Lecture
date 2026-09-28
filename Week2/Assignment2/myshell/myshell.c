/* Based on the CS:APP shellex.c baseline.
 * make -> ./myshell. Assignment2 extends the separate Assignment1 shell.
 * myps/mytop, event_wait/event_notify and the week3 server stay external
 * programs; do not add their implementation as shell builtins.
 */
#include "myshell.h"
#include <sys/select.h>

/* Read one byte at a time so a foreground child can read subsequent input. */
static int read_command(char *cmdline)
{
    size_t length = 0;
    int too_long = 0;
    for (;;) {
        reap_children();
        fd_set input;
        FD_ZERO(&input);
        FD_SET(STDIN_FILENO, &input);
        int ready = pselect(STDIN_FILENO + 1, &input, NULL, NULL, NULL,
                            &wait_signal_mask);
        if (ready < 0) {
            if (errno == EINTR)
                continue;
            perror("myshell: pselect");
            return -1;
        }
        char ch;
        ssize_t count = read(STDIN_FILENO, &ch, 1);
        if (count < 0) {
            if (errno == EINTR || errno == EAGAIN)
                continue;
            perror("myshell: read");
            return -1;
        }
        if (count == 0 || ch == '\n') {
            cmdline[length] = '\0';
            if (too_long) {
                fprintf(stderr, "myshell: input too long\n");
                cmdline[0] = '\0';
            }
            return count == 0 && !length && !too_long ? 0 : 1;
        }
        if (length < MAXLINE - 1)
            cmdline[length++] = ch;
        else
            too_long = 1;
    }
}

int main(void)
{
    char cmdline[MAXLINE];
    int interactive = isatty(STDIN_FILENO);
    int result = 0;

    init_signals();
    while (1) {
        reap_children();
        if (interactive) {
            printf("myshell> ");
            fflush(stdout);
        }
        int input = read_command(cmdline);
        if (input <= 0) {
            result = input < 0;
            break;
        }
        eval(cmdline);
    }
    cleanup_jobs();
    return result;
}

void eval(char *cmdline)
{
    struct command_line line = {0};
    char original[MAXLINE];

    strcpy(original, cmdline);
    if (parseline(cmdline, &line) < 0 || line.count == 0)
        return;
    if (builtin_command(&line))
        return;
    execute(&line, original);
}

int builtin_command(const struct command_line *line)
{
    for (int i = 0; i < line->count; ++i) {
        const char *name = line->commands[i].argv[0];
        if (strcmp(name, "cd") && strcmp(name, "pwd") && strcmp(name, "exit"))
            continue;
        if (line->count != 1 || line->background) {
            fprintf(stderr, "myshell: use builtins as standalone foreground commands\n");
            return 1;
        }
        if (!strcmp(name, "exit")) {
            if (line->commands[i].argc != 1) {
                fprintf(stderr, "usage: exit\n");
                return 1;
            }
            cleanup_jobs();
            exit(0);
        }
        if (!strcmp(name, "cd")) {
            if (line->commands[i].argc > 2) {
                fprintf(stderr, "usage: cd [directory]\n");
                return 1;
            }
            const char *path = line->commands[i].argc == 2
                ? line->commands[i].argv[1] : getenv("HOME");
            if (path == NULL || *path == '\0') {
                fprintf(stderr, "myshell: cd: empty path or HOME not set\n");
                return 1;
            }
            /* Change the shell's own working directory before the next input. */
            if (chdir(path) < 0)
                perror("myshell: cd");
        } else {
            if (line->commands[i].argc != 1) {
                fprintf(stderr, "usage: pwd\n");
                return 1;
            }
            char cwd[MAXLINE];
            if (getcwd(cwd, sizeof(cwd)) == NULL)
                perror("myshell: pwd");
            else
                puts(cwd);
        }
        return 1;
    }
    return 0;
}
