#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <netinet/in.h>
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/eventfd.h>
#include <sys/socket.h>
#include <sys/timerfd.h>
#include <time.h>
#include <unistd.h>

#define MAX_EVENTS 64
#define REQUEST_CAPACITY 256
#define OUTPUT_CAPACITY 4096
#define MAX_COMPUTE_WORK 1000000000ULL

enum server_mode {
    MODE_LEVEL1,
    MODE_BLOCKING,
    MODE_OFFLOAD,
    MODE_POOL,
};

struct client {
    int fd;
    char input[REQUEST_CAPACITY];
    size_t used;
    char output[OUTPUT_CAPACITY];
    size_t sent;
    size_t queued;
    bool alive;
    bool input_eof;
    bool closing;
    bool compute_pending;
    struct client *next;
};

struct job {
    struct client *client;
    uint64_t work;
};

struct completion {
    struct client *client;
};

struct worker_pool {
    pthread_mutex_t mutex;
    pthread_cond_t jobs_ready;
    pthread_cond_t completions_ready;
    pthread_t *threads;
    unsigned int worker_count;
    bool stopping;
    struct job *jobs;
    size_t job_capacity;
    size_t job_head;
    size_t job_tail;
    size_t job_count;
    struct completion *completions;
    size_t completion_capacity;
    size_t completion_head;
    size_t completion_tail;
    size_t completion_count;
    size_t in_flight;
    int event_fd;
};

struct server {
    enum server_mode mode;
    int epoll_fd;
    int listener_fd;
    int timer_fd;
    struct worker_pool workers;
    struct client *clients;
    size_t active_clients;
    uint64_t interval_requests;
    uint64_t interval_computes;
};

static volatile sig_atomic_t stop_requested;
static atomic_uint_fast64_t compute_sink;
static char listener_tag;
static char timer_tag;
static char worker_tag;

static void handle_signal(int signo) {
    (void)signo;
    stop_requested = 1;
}

static void fail(const char *operation) {
    perror(operation);
    exit(EXIT_FAILURE);
}

static unsigned long parse_unsigned(const char *text, unsigned long minimum,
                                    unsigned long maximum, const char *name) {
    char *end = NULL;
    errno = 0;
    unsigned long value = strtoul(text, &end, 10);
    if (errno || !end || *end || value < minimum || value > maximum) {
        fprintf(stderr, "%s must be an integer from %lu to %lu\n", name, minimum,
                maximum);
        exit(EXIT_FAILURE);
    }
    return value;
}

static uint16_t parse_port(const char *text) {
    return (uint16_t)parse_unsigned(text, 1, 65535, "Port");
}

static int create_listener(uint16_t port) {
    int fd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        fail("socket");
    }

    int reuse = 1;
    if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof reuse) < 0) {
        fail("setsockopt SO_REUSEADDR");
    }

    struct sockaddr_in address = {
        .sin_family = AF_INET,
        .sin_port = htons(port),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    if (bind(fd, (struct sockaddr *)&address, sizeof address) < 0) {
        fail("bind");
    }
    if (listen(fd, SOMAXCONN) < 0) {
        fail("listen");
    }
    return fd;
}

static uint64_t run_compute(uint64_t work) {
    uint64_t value = 0x9e3779b97f4a7c15ULL;
    for (uint64_t i = 0; i < work; ++i) {
        value = value * 6364136223846793005ULL + (i ^ (value >> 17));
    }
    atomic_store_explicit(&compute_sink, value, memory_order_relaxed);
    return value;
}

static void unlink_client(struct server *server, struct client *client) {
    struct client **link = &server->clients;
    while (*link && *link != client) {
        link = &(*link)->next;
    }
    if (*link) {
        *link = client->next;
    }
    free(client);
}

static void close_client(struct server *server, struct client *client) {
    if (client->alive) {
        int fd = client->fd;
        if (epoll_ctl(server->epoll_fd, EPOLL_CTL_DEL, fd, NULL) < 0 && errno != ENOENT) {
            perror("epoll_ctl DEL client");
        }
        close(fd);
        printf("CLOSE fd=%d\n", fd);
        client->alive = false;
        client->fd = -1;
        --server->active_clients;
    }
    if (!client->compute_pending) {
        unlink_client(server, client);
    }
}

static bool queue_reply(struct client *client, const char *data, size_t length) {
    size_t pending = client->queued - client->sent;
    if (length > sizeof client->output - pending) {
        return false;
    }
    if (client->sent && pending) {
        memmove(client->output, client->output + client->sent, pending);
    }
    client->sent = 0;
    client->queued = pending;
    memcpy(client->output + client->queued, data, length);
    client->queued += length;
    return true;
}

static bool flush_reply(struct client *client) {
    while (client->sent < client->queued) {
        ssize_t n = send(client->fd, client->output + client->sent,
                         client->queued - client->sent, MSG_NOSIGNAL);
        if (n > 0) {
            client->sent += (size_t)n;
            continue;
        }
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            return true;
        }
        return false;
    }
    client->sent = 0;
    client->queued = 0;
    return true;
}

static bool pool_submit(struct worker_pool *pool, struct client *client, uint64_t work,
                        bool single_outstanding) {
    pthread_mutex_lock(&pool->mutex);
    bool full = pool->job_count == pool->job_capacity ||
                (single_outstanding && pool->in_flight != 0);
    if (pool->stopping || full) {
        pthread_mutex_unlock(&pool->mutex);
        return false;
    }
    pool->jobs[pool->job_tail] = (struct job){.client = client, .work = work};
    pool->job_tail = (pool->job_tail + 1) % pool->job_capacity;
    ++pool->job_count;
    ++pool->in_flight;
    pthread_cond_signal(&pool->jobs_ready);
    pthread_mutex_unlock(&pool->mutex);
    return true;
}

static void notify_event_loop(int event_fd) {
    uint64_t one = 1;
    for (;;) {
        ssize_t n = write(event_fd, &one, sizeof one);
        if (n == (ssize_t)sizeof one || (n < 0 && errno == EAGAIN)) {
            return;
        }
        if (n < 0 && errno == EINTR) {
            continue;
        }
        perror("write eventfd");
        return;
    }
}

static void *worker_main(void *argument) {
    struct worker_pool *pool = argument;
    for (;;) {
        pthread_mutex_lock(&pool->mutex);
        while (!pool->stopping && pool->job_count == 0) {
            pthread_cond_wait(&pool->jobs_ready, &pool->mutex);
        }
        if (pool->stopping) {
            pthread_mutex_unlock(&pool->mutex);
            return NULL;
        }
        struct job job = pool->jobs[pool->job_head];
        pool->job_head = (pool->job_head + 1) % pool->job_capacity;
        --pool->job_count;
        pthread_mutex_unlock(&pool->mutex);

        (void)run_compute(job.work);

        pthread_mutex_lock(&pool->mutex);
        while (!pool->stopping && pool->completion_count == pool->completion_capacity) {
            pthread_cond_wait(&pool->completions_ready, &pool->mutex);
        }
        if (pool->stopping) {
            pthread_mutex_unlock(&pool->mutex);
            return NULL;
        }
        pool->completions[pool->completion_tail].client = job.client;
        pool->completion_tail = (pool->completion_tail + 1) % pool->completion_capacity;
        ++pool->completion_count;
        pthread_mutex_unlock(&pool->mutex);
        notify_event_loop(pool->event_fd);
    }
}

static void initialize_workers(struct worker_pool *pool, unsigned int workers,
                               size_t queue_capacity) {
    memset(pool, 0, sizeof *pool);
    pool->event_fd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
    if (pool->event_fd < 0) {
        fail("eventfd");
    }
    pool->worker_count = workers;
    pool->job_capacity = queue_capacity;
    pool->completion_capacity = queue_capacity + workers;
    pool->jobs = calloc(pool->job_capacity, sizeof *pool->jobs);
    pool->completions = calloc(pool->completion_capacity, sizeof *pool->completions);
    pool->threads = calloc(workers, sizeof *pool->threads);
    if (!pool->jobs || !pool->completions || !pool->threads) {
        fail("calloc worker state");
    }
    int error = pthread_mutex_init(&pool->mutex, NULL);
    if (error) {
        fprintf(stderr, "pthread_mutex_init: %s\n", strerror(error));
        exit(EXIT_FAILURE);
    }
    error = pthread_cond_init(&pool->jobs_ready, NULL);
    if (error) {
        fprintf(stderr, "pthread_cond_init: %s\n", strerror(error));
        exit(EXIT_FAILURE);
    }
    error = pthread_cond_init(&pool->completions_ready, NULL);
    if (error) {
        fprintf(stderr, "pthread_cond_init: %s\n", strerror(error));
        exit(EXIT_FAILURE);
    }
    for (unsigned int i = 0; i < workers; ++i) {
        error = pthread_create(&pool->threads[i], NULL, worker_main, pool);
        if (error) {
            fprintf(stderr, "pthread_create: %s\n", strerror(error));
            exit(EXIT_FAILURE);
        }
    }
}

static void stop_workers(struct worker_pool *pool) {
    if (!pool->worker_count) {
        return;
    }
    pthread_mutex_lock(&pool->mutex);
    pool->stopping = true;
    pthread_cond_broadcast(&pool->jobs_ready);
    pthread_cond_broadcast(&pool->completions_ready);
    pthread_mutex_unlock(&pool->mutex);
    for (unsigned int i = 0; i < pool->worker_count; ++i) {
        pthread_join(pool->threads[i], NULL);
    }
}

static bool watch_client(struct server *server, struct client *client) {
    struct epoll_event event = {.events = 0, .data.ptr = client};
    if (!client->input_eof && !client->closing && !client->compute_pending) {
        event.events |= EPOLLIN | EPOLLRDHUP;
    }
    if (client->sent < client->queued) {
        event.events |= EPOLLOUT;
    }
    if (epoll_ctl(server->epoll_fd, EPOLL_CTL_MOD, client->fd, &event) < 0) {
        perror("epoll_ctl MOD client");
        return false;
    }
    return true;
}

static bool finish_if_ready(struct server *server, struct client *client) {
    if (!client->compute_pending && (client->input_eof || client->closing) &&
        client->sent == client->queued) {
        close_client(server, client);
        return false;
    }
    return true;
}

static bool dispatch_line(struct server *server, struct client *client, const char *line,
                          size_t length) {
    if (length && line[length - 1] == '\r') {
        --length;
    }
    char reply[REQUEST_CAPACITY + 4];
    size_t reply_length = 0;

    if (length == 4 && !memcmp(line, "quit", 4)) {
        memcpy(reply, "BYE\n", 4);
        reply_length = 4;
        client->closing = true;
    } else if (length == 4 && !memcmp(line, "time", 4)) {
        time_t now = time(NULL);
        struct tm local_time;
        char timestamp[32];
        if (now == (time_t)-1 || !localtime_r(&now, &local_time) ||
            !strftime(timestamp, sizeof timestamp, "%Y-%m-%d %H:%M:%S", &local_time)) {
            memcpy(reply, "ERROR\n", 6);
            reply_length = 6;
        } else {
            int n = snprintf(reply, sizeof reply, "TIME %s\n", timestamp);
            if (n < 0 || (size_t)n >= sizeof reply) {
                return false;
            }
            reply_length = (size_t)n;
        }
    } else if (length > 8 && !memcmp(line, "compute ", 8)) {
        char work_text[REQUEST_CAPACITY];
        size_t work_length = length - 8;
        memcpy(work_text, line + 8, work_length);
        work_text[work_length] = '\0';
        char *end = NULL;
        errno = 0;
        unsigned long long work = strtoull(work_text, &end, 10);
        if (errno || end == work_text || *end || work == 0 || work > MAX_COMPUTE_WORK) {
            memcpy(reply, "ERROR\n", 6);
            reply_length = 6;
        } else if (server->mode == MODE_OFFLOAD || server->mode == MODE_POOL) {
            bool single_outstanding = server->mode == MODE_OFFLOAD;
            if (pool_submit(&server->workers, client, (uint64_t)work,
                            single_outstanding)) {
                client->compute_pending = true;
                return true;
            }
            memcpy(reply, "BUSY\n", 5);
            reply_length = 5;
        } else {
            (void)run_compute((uint64_t)work);
            memcpy(reply, "RESULT compute finished\n",
                   sizeof "RESULT compute finished\n" - 1);
            reply_length = sizeof "RESULT compute finished\n" - 1;
            ++server->interval_computes;
        }
    } else if (length >= 5 && !memcmp(line, "echo ", 5)) {
        size_t text_length = length - 5;
        memcpy(reply, "OK ", 3);
        memcpy(reply + 3, line + 5, text_length);
        reply[3 + text_length] = '\n';
        reply_length = 4 + text_length;
    } else {
        memcpy(reply, "ERROR\n", 6);
        reply_length = 6;
    }

    if (!queue_reply(client, reply, reply_length)) {
        fprintf(stderr, "Output queue full on fd=%d\n", client->fd);
        return false;
    }
    return flush_reply(client);
}

static bool process_complete_lines(struct server *server, struct client *client) {
    char *newline;
    while (!client->closing && !client->compute_pending &&
           (newline = memchr(client->input, '\n', client->used)) != NULL) {
        size_t length = (size_t)(newline - client->input);
        printf("REQUEST fd=%d length=%zu line=%.*s\n", client->fd, length, (int)length,
               client->input);
        ++server->interval_requests;
        size_t consumed = length + 1;
        if (!dispatch_line(server, client, client->input, length)) {
            return false;
        }
        memmove(client->input, client->input + consumed, client->used - consumed);
        client->used -= consumed;
    }
    return true;
}

static bool read_client(struct server *server, struct client *client) {
    if (client->compute_pending || client->closing || client->input_eof) {
        return true;
    }
    for (;;) {
        if (client->used == sizeof client->input) {
            fprintf(stderr, "Request exceeds %d bytes on fd=%d\n", REQUEST_CAPACITY,
                    client->fd);
            close_client(server, client);
            return false;
        }
        ssize_t n = recv(client->fd, client->input + client->used,
                         sizeof client->input - client->used, 0);
        if (n > 0) {
            client->used += (size_t)n;
            if (!process_complete_lines(server, client)) {
                close_client(server, client);
                return false;
            }
            if (client->compute_pending || client->closing) {
                return true;
            }
            continue;
        }
        if (!n) {
            client->input_eof = true;
            if (!memchr(client->input, '\n', client->used)) {
                client->used = 0;
            }
            return true;
        }
        if (errno == EINTR) {
            continue;
        }
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            return true;
        }
        perror("recv");
        close_client(server, client);
        return false;
    }
}

static void accept_clients(struct server *server) {
    for (;;) {
        int client_fd = accept4(server->listener_fd, NULL, NULL, SOCK_NONBLOCK | SOCK_CLOEXEC);
        if (client_fd < 0 && errno == EINTR) {
            continue;
        }
        if (client_fd < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            return;
        }
        if (client_fd < 0) {
            perror("accept4");
            return;
        }
        struct client *client = calloc(1, sizeof *client);
        if (!client) {
            perror("calloc client");
            close(client_fd);
            continue;
        }
        client->fd = client_fd;
        client->alive = true;
        client->next = server->clients;
        server->clients = client;

        struct epoll_event event = {
            .events = EPOLLIN | EPOLLRDHUP,
            .data.ptr = client,
        };
        if (epoll_ctl(server->epoll_fd, EPOLL_CTL_ADD, client_fd, &event) < 0) {
            perror("epoll_ctl ADD client");
            close(client_fd);
            client->alive = false;
            unlink_client(server, client);
            continue;
        }
        ++server->active_clients;
        printf("ACCEPT fd=%d registered\n", client_fd);
    }
}

static void drain_eventfd(struct worker_pool *pool) {
    uint64_t value;
    for (;;) {
        ssize_t n = read(pool->event_fd, &value, sizeof value);
        if (n == (ssize_t)sizeof value) {
            continue;
        }
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            return;
        }
        if (n < 0) {
            perror("read eventfd");
        }
        return;
    }
}

static bool pop_completion(struct worker_pool *pool, struct completion *completion) {
    pthread_mutex_lock(&pool->mutex);
    if (!pool->completion_count) {
        pthread_mutex_unlock(&pool->mutex);
        return false;
    }
    *completion = pool->completions[pool->completion_head];
    pool->completion_head = (pool->completion_head + 1) % pool->completion_capacity;
    --pool->completion_count;
    --pool->in_flight;
    pthread_cond_broadcast(&pool->completions_ready);
    pthread_mutex_unlock(&pool->mutex);
    return true;
}

static void handle_completion(struct server *server, struct completion completion) {
    struct client *client = completion.client;
    client->compute_pending = false;
    if (!client->alive) {
        unlink_client(server, client);
        return;
    }

    static const char response[] = "RESULT compute finished\n";
    ++server->interval_computes;
    if (!queue_reply(client, response, sizeof response - 1) || !flush_reply(client) ||
        !process_complete_lines(server, client)) {
        close_client(server, client);
        return;
    }
    if (!finish_if_ready(server, client)) {
        return;
    }
    if (!watch_client(server, client)) {
        close_client(server, client);
    }
}

static void handle_worker_event(struct server *server) {
    drain_eventfd(&server->workers);
    struct completion completion;
    while (pop_completion(&server->workers, &completion)) {
        handle_completion(server, completion);
    }
}

static void handle_timer_event(struct server *server) {
    uint64_t expirations;
    ssize_t n;
    do {
        n = read(server->timer_fd, &expirations, sizeof expirations);
    } while (n < 0 && errno == EINTR);
    if (n != (ssize_t)sizeof expirations) {
        if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
            perror("read timerfd");
        }
        return;
    }
    double interval = (double)expirations * 5.0;
    double throughput = interval > 0 ? (double)server->interval_requests / interval : 0;
    printf("[STATS] interval=%.0f sec active_clients=%zu requests=%" PRIu64
           " throughput=%.2f req/s completed_compute=%" PRIu64 "\n",
           interval, server->active_clients, server->interval_requests, throughput,
           server->interval_computes);
    server->interval_requests = 0;
    server->interval_computes = 0;
}

static void initialize_timer(struct server *server) {
    server->timer_fd = timerfd_create(CLOCK_MONOTONIC, TFD_NONBLOCK | TFD_CLOEXEC);
    if (server->timer_fd < 0) {
        fail("timerfd_create");
    }
    struct itimerspec timer = {
        .it_interval = {.tv_sec = 5, .tv_nsec = 0},
        .it_value = {.tv_sec = 5, .tv_nsec = 0},
    };
    if (timerfd_settime(server->timer_fd, 0, &timer, NULL) < 0) {
        fail("timerfd_settime");
    }
    struct epoll_event event = {.events = EPOLLIN, .data.ptr = &timer_tag};
    if (epoll_ctl(server->epoll_fd, EPOLL_CTL_ADD, server->timer_fd, &event) < 0) {
        fail("epoll_ctl ADD timerfd");
    }
}

static void register_worker_event(struct server *server) {
    struct epoll_event event = {.events = EPOLLIN, .data.ptr = &worker_tag};
    if (epoll_ctl(server->epoll_fd, EPOLL_CTL_ADD, server->workers.event_fd, &event) < 0) {
        fail("epoll_ctl ADD eventfd");
    }
}

static void cleanup_server(struct server *server) {
    stop_workers(&server->workers);
    struct client *client = server->clients;
    while (client) {
        struct client *next = client->next;
        if (client->alive) {
            close(client->fd);
        }
        free(client);
        client = next;
    }
    if (server->listener_fd >= 0) {
        close(server->listener_fd);
    }
    if (server->timer_fd >= 0) {
        close(server->timer_fd);
    }
    if (server->workers.event_fd >= 0) {
        close(server->workers.event_fd);
    }
    if (server->epoll_fd >= 0) {
        close(server->epoll_fd);
    }
}

static void usage(const char *program) {
    fprintf(stderr,
            "Usage: %s PORT [--mode level1|blocking|offload|pool] [--workers N] "
            "[--queue-capacity N]\n",
            program);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        usage(argv[0]);
        return 2;
    }
    struct server server = {
        .mode = MODE_LEVEL1,
        .epoll_fd = -1,
        .listener_fd = -1,
        .timer_fd = -1,
        .workers = {.event_fd = -1},
    };
    unsigned int worker_count = 1;
    size_t queue_capacity = 16;
    bool mode_set = false;
    bool workers_set = false;

    for (int i = 2; i < argc; ++i) {
        if (!strcmp(argv[i], "--mode") && i + 1 < argc) {
            const char *mode = argv[++i];
            mode_set = true;
            if (!strcmp(mode, "level1")) {
                server.mode = MODE_LEVEL1;
            } else if (!strcmp(mode, "blocking")) {
                server.mode = MODE_BLOCKING;
            } else if (!strcmp(mode, "offload")) {
                server.mode = MODE_OFFLOAD;
            } else if (!strcmp(mode, "pool")) {
                server.mode = MODE_POOL;
            } else {
                usage(argv[0]);
                return 2;
            }
        } else if (!strcmp(argv[i], "--workers") && i + 1 < argc) {
            worker_count = (unsigned int)parse_unsigned(argv[++i], 1, 256, "Workers");
            workers_set = true;
        } else if (!strcmp(argv[i], "--queue-capacity") && i + 1 < argc) {
            queue_capacity = (size_t)parse_unsigned(argv[++i], 1, 65536, "Queue capacity");
        } else {
            usage(argv[0]);
            return 2;
        }
    }
    if (workers_set && !mode_set) {
        server.mode = MODE_POOL;
    }
    if (server.mode == MODE_OFFLOAD) {
        worker_count = 1;
        queue_capacity = 1;
    }
    if ((server.mode == MODE_LEVEL1 || server.mode == MODE_BLOCKING) && workers_set) {
        fprintf(stderr, "--workers requires --mode offload or --mode pool\n");
        return 2;
    }
    if (server.mode == MODE_LEVEL1 || server.mode == MODE_BLOCKING) {
        worker_count = 0;
    }

    setvbuf(stdout, NULL, _IOLBF, 0);
    struct sigaction action = {.sa_handler = handle_signal};
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) < 0 || sigaction(SIGTERM, &action, NULL) < 0) {
        fail("sigaction");
    }

    server.listener_fd = create_listener(parse_port(argv[1]));
    server.epoll_fd = epoll_create1(EPOLL_CLOEXEC);
    if (server.epoll_fd < 0) {
        fail("epoll_create1");
    }
    struct epoll_event listener_event = {
        .events = EPOLLIN,
        .data.ptr = &listener_tag,
    };
    if (epoll_ctl(server.epoll_fd, EPOLL_CTL_ADD, server.listener_fd, &listener_event) < 0) {
        fail("epoll_ctl ADD listener");
    }

    if (server.mode != MODE_LEVEL1) {
        initialize_timer(&server);
    }
    if (worker_count) {
        initialize_workers(&server.workers, worker_count, queue_capacity);
        register_worker_event(&server);
    }

    printf("READY pid=%ld epfd=%d listener=%d port=%s mode=%d workers=%u queue=%zu\n",
           (long)getpid(), server.epoll_fd, server.listener_fd, argv[1], server.mode,
           worker_count, queue_capacity);

    while (!stop_requested) {
        struct epoll_event events[MAX_EVENTS];
        int count = epoll_wait(server.epoll_fd, events, MAX_EVENTS, -1);
        if (count < 0 && errno == EINTR) {
            continue;
        }
        if (count < 0) {
            fail("epoll_wait");
        }
        for (int i = 0; i < count; ++i) {
            void *tag = events[i].data.ptr;
            if (tag == &listener_tag) {
                accept_clients(&server);
                continue;
            }
            if (tag == &timer_tag) {
                handle_timer_event(&server);
                continue;
            }
            if (tag == &worker_tag) {
                handle_worker_event(&server);
                continue;
            }

            struct client *client = tag;
            uint32_t flags = events[i].events;
            if (flags & (EPOLLERR | EPOLLHUP)) {
                close_client(&server, client);
                continue;
            }
            if ((flags & (EPOLLIN | EPOLLRDHUP)) &&
                !read_client(&server, client)) {
                continue;
            }
            if (client->alive && (flags & EPOLLOUT) && !flush_reply(client)) {
                close_client(&server, client);
                continue;
            }
            if (!client->alive) {
                continue;
            }
            if (!finish_if_ready(&server, client)) {
                continue;
            }
            if (!watch_client(&server, client)) {
                close_client(&server, client);
            }
        }
    }

    printf("STOP active_clients=%zu\n", server.active_clients);
    cleanup_server(&server);
    return 0;
}
