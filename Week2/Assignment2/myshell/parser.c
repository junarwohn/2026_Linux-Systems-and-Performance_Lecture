#include "myshell.h"

/* Provided: ordinary whitespace-separated arguments, with a bounds check.
 * No quoting/expansion is provided. argv points into buf and is temporary.
 * Return 0 on success (count == 0 for blank input), -1 on an error.
 */
int parseline(char *buf, struct command_line *line)
{
    char *save = NULL;
    char *word;
    struct command *cmd;

    memset(line, 0, sizeof(*line));
    if (strpbrk(buf, "\"'")) {
        fprintf(stderr, "myshell: quoting is not supported by this starter\n");
        return -1;
    }
    char *ampersand = strchr(buf, '&');
    if (ampersand) {
        if (ampersand[1 + strspn(ampersand + 1, " \t\r\n")] != '\0') {
            fprintf(stderr, "myshell: '&' is only allowed at the end\n");
            return -1;
        }
        *ampersand = '\0';
        line->background = 1;
    }
    char *parts[2] = {buf, NULL};
    char *separator = strchr(buf, '|');
    int count = separator ? 2 : 1;
    if (separator) {
        if (strchr(separator + 1, '|')) {
            fprintf(stderr, "myshell: only two-command pipelines are supported\n");
            return -1;
        }
        *separator = '\0';
        parts[1] = separator + 1;
    }
    for (int i = 0; i < count; ++i) {
        cmd = &line->commands[i];
        save = NULL;
        for (word = strtok_r(parts[i], " \t\r\n", &save); word != NULL;
             word = strtok_r(NULL, " \t\r\n", &save)) {
            if (cmd->argc >= MAXARGS - 1) {
                fprintf(stderr, "myshell: too many arguments\n");
                return -1;
            }
            cmd->argv[cmd->argc++] = word;
        }
        cmd->argv[cmd->argc] = NULL;
        if (count == 2 && cmd->argc == 0) {
            fprintf(stderr, "myshell: empty pipeline command\n");
            return -1;
        }
    }
    line->count = line->commands[0].argc ? count : 0;
    if (line->background && !line->count) {
        fprintf(stderr, "myshell: missing background command\n");
        return -1;
    }
    return 0;
}
