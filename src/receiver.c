#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

/* Every wire field is encoded explicitly, independently of C struct layout. */
enum { HEADER = 20, CHUNK = 1024, START = 1, START_ACK = 2,
       DATA = 3, ACK = 4, FIN = 5, FIN_ACK = 6, ERROR_PACKET = 7 };
#define MAX_FILE_BYTES (UINT64_C(1024) * 1024 * 1024)

static volatile sig_atomic_t desired_paused;
static volatile sig_atomic_t interrupted;

struct slot {
    unsigned char data[CHUNK];
    uint32_t sequence;
    uint16_t length;
    int present;
};

struct receiver {
    int socket_fd, file_fd;
    int active, complete, paused, temporary_exists;
    char *temporary;
    const char *output;
    struct sockaddr_in peer;
    struct slot *slots;
    unsigned capacity, buffered, peak_buffered;
    uint32_t id, chunks, base;
    uint64_t size, written, duplicates;
};

static void control_signal(int signo)
{
    if (signo == SIGUSR1) desired_paused = 1;
    else if (signo == SIGUSR2) desired_paused = 0;
    else interrupted = signo;
}

static int install_signals(void)
{
    struct sigaction action;
    memset(&action, 0, sizeof action);
    action.sa_handler = control_signal;
    if (sigemptyset(&action.sa_mask) != 0 ||
        sigaction(SIGUSR1, &action, NULL) != 0 ||
        sigaction(SIGUSR2, &action, NULL) != 0 ||
        sigaction(SIGINT, &action, NULL) != 0 ||
        sigaction(SIGTERM, &action, NULL) != 0) return -1;
    /* File size limits should surface as write errors and run normal cleanup. */
    action.sa_handler = SIG_IGN;
    return sigaction(SIGXFSZ, &action, NULL);
}

static void put16(unsigned char *p, uint16_t n)
{ p[0] = (unsigned char)(n >> 8); p[1] = (unsigned char)n; }
static void put32(unsigned char *p, uint32_t n)
{ put16(p, (uint16_t)(n >> 16)); put16(p + 2, (uint16_t)n); }
static uint16_t get16(const unsigned char *p)
{ return (uint16_t)(((uint16_t)p[0] << 8) | p[1]); }
static uint32_t get32(const unsigned char *p)
{ return ((uint32_t)get16(p) << 16) | get16(p + 2); }
static uint64_t get64(const unsigned char *p)
{ return ((uint64_t)get32(p) << 32) | get32(p + 4); }

static int64_t now_ms(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return -1;
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static int number(const char *s, unsigned long low, unsigned long high,
                  unsigned long *out)
{
    char *end;
    if (!*s || strspn(s, "0123456789") != strlen(s)) return -1;
    errno = 0;
    *out = strtoul(s, &end, 10);
    return errno || *end || *out < low || *out > high ? -1 : 0;
}

static uint16_t receive_window(const struct receiver *r)
{
    if (r->paused || r->complete) return 0;
    if (!r->active) return (uint16_t)r->capacity;
    uint32_t remaining = r->chunks - r->base;
    return (uint16_t)(remaining < r->capacity ? remaining : r->capacity);
}

static int reply(const struct receiver *r, unsigned type, uint32_t sequence,
                 const char *error)
{
    unsigned char packet[HEADER + 256] = {0};
    size_t length = error ? strlen(error) : 0;
    if (length > 256) length = 256;
    memcpy(packet, "RUDP", 4);
    packet[4] = 1; packet[5] = (unsigned char)type;
    put32(packet + 8, r->id); put32(packet + 12, sequence);
    put16(packet + 16, (uint16_t)length);
    put16(packet + 18, receive_window(r));
    if (length) memcpy(packet + HEADER, error, length);
    ssize_t sent;
    do {
        sent = sendto(r->socket_fd, packet, HEADER + length, MSG_DONTWAIT,
                      (const struct sockaddr *)&r->peer, sizeof r->peer);
    } while (sent < 0 && errno == EINTR && !interrupted);
    /* A full UDP send queue acts like a lost confirmation; retries recover it. */
    if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return 0;
    if (sent < 0 || (size_t)sent != HEADER + length) {
        if (sent >= 0) errno = EIO;
        perror("send confirmation");
        return -1;
    }
    return 0;
}

static void report_file_error(const struct receiver *r, uint32_t sequence,
                              const char *operation)
{
    int saved_errno = errno;
    fprintf(stderr, "%s: %s\n", operation, strerror(saved_errno));
    (void)reply(r, ERROR_PACKET, sequence, operation);
}

static int create_temporary(struct receiver *r)
{
    struct stat st;
    if (lstat(r->output, &st) == 0) {
        fprintf(stderr, "Output already exists; choose a new output path.\n");
        return -1;
    }
    if (errno != ENOENT) { perror("inspect output"); return -1; }
    const char *slash = strrchr(r->output, '/');
    size_t prefix = slash ? (size_t)(slash - r->output) + 1 : 0;
    const char suffix[] = ".rudp-recv-XXXXXX";
    r->temporary = malloc(prefix + sizeof suffix);
    if (!r->temporary) { perror("allocate temporary path"); return -1; }
    memcpy(r->temporary, r->output, prefix);
    memcpy(r->temporary + prefix, suffix, sizeof suffix);
    r->file_fd = mkstemp(r->temporary);
    if (r->file_fd < 0) { perror("create temporary output"); return -1; }
    r->temporary_exists = 1;
    return 0;
}

static int write_chunk(int fd, const unsigned char *data, size_t length)
{
    size_t written = 0;
    while (written < length) {
        ssize_t n = write(fd, data + written, length - written);
        if (n < 0 && errno == EINTR && !interrupted) continue;
        if (n <= 0) {
            if (!n) errno = EIO;
            return -1;
        }
        written += (size_t)n;
    }
    return 0;
}

static int save_output(struct receiver *r)
{
    int synced;
    do { synced = fsync(r->file_fd); }
    while (synced < 0 && errno == EINTR && !interrupted);
    if (synced < 0) { report_file_error(r, r->chunks, "sync output"); return -1; }
    int closed = close(r->file_fd);
    r->file_fd = -1;
    if (closed < 0) { report_file_error(r, r->chunks, "close output"); return -1; }
    /* Both names are in the same directory. link fails atomically on EEXIST. */
    if (link(r->temporary, r->output) < 0) {
        report_file_error(r, r->chunks, "publish output");
        return -1;
    }
    if (unlink(r->temporary) == 0) r->temporary_exists = 0;
    else perror("remove published temporary name");
    return 0;
}

static int same_peer(const struct sockaddr_in *a, const struct sockaddr_in *b)
{
    return a->sin_family == b->sin_family && a->sin_port == b->sin_port &&
           a->sin_addr.s_addr == b->sin_addr.s_addr;
}

static int run_receiver(struct receiver *r, unsigned idle_ms, unsigned linger_ms)
{
    int64_t clock = now_ms();
    if (clock < 0) { perror("clock_gettime"); return 1; }
    int64_t deadline = clock + idle_ms;
    for (;;) {
        if (interrupted) {
            fprintf(stderr, "Receiver interrupted by signal %d.\n", (int)interrupted);
            return 1;
        }
        clock = now_ms();
        if (clock < 0) { perror("clock_gettime"); return 1; }
        if (clock >= deadline) {
            if (!r->complete) {
                fprintf(stderr, "Timed out waiting for progress at sequence=%" PRIu32 ".\n",
                        r->base);
                return 1;
            }
            return 0;
        }
        if (r->paused != (int)desired_paused) {
            r->paused = (int)desired_paused;
            /* State changes use the same ACK format as DATA responses. */
            if (r->active && !r->complete && reply(r, ACK, r->base, NULL)) return 1;
            printf("WINDOW paused=%d base=%" PRIu32 " window=%u\n",
                   r->paused, r->base, (unsigned)receive_window(r));
            fflush(stdout);
        }
        int wait_ms = deadline - clock > 50 ? 50 : (int)(deadline - clock);
        struct pollfd pfd = { r->socket_fd, POLLIN, 0 };
        int ready = poll(&pfd, 1, wait_ms);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0) { perror("poll receiver"); return 1; }
        if (!ready) continue;
        if (pfd.revents & (POLLERR | POLLHUP | POLLNVAL)) {
            fprintf(stderr, "Receiver socket reported an error.\n");
            return 1;
        }
        unsigned char packet[HEADER + CHUNK + 1];
        struct sockaddr_in source;
        memset(&source, 0, sizeof source);
        socklen_t source_length = sizeof source;
        ssize_t n = recvfrom(r->socket_fd, packet, sizeof packet, MSG_DONTWAIT,
                             (struct sockaddr *)&source, &source_length);
        if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (n < 0) { perror("recvfrom"); return 1; }
        if (source_length != sizeof source || source.sin_family != AF_INET ||
            n < HEADER || n > HEADER + CHUNK || memcmp(packet, "RUDP", 4) ||
            packet[4] != 1 || get16(packet + 6) != 0 || get16(packet + 18) != 0 ||
            (size_t)n != HEADER + (size_t)get16(packet + 16)) continue;
        unsigned type = packet[5];
        uint32_t id = get32(packet + 8), sequence = get32(packet + 12);
        uint16_t length = get16(packet + 16);
        if (type != START && type != DATA && type != FIN) continue;
        if (r->active && (id != r->id || !same_peer(&source, &r->peer))) continue;
        if (type == START) {
            if (sequence != 0 || length != 12) continue;
            uint32_t chunks = get32(packet + HEADER);
            uint64_t size = get64(packet + HEADER + 4);
            if (size > MAX_FILE_BYTES || chunks != (size + CHUNK - 1) / CHUNK) continue;
            if (r->active) {
                if (chunks != r->chunks || size != r->size) continue;
                ++r->duplicates;
            } else {
                r->active = 1; r->id = id; r->peer = source;
                r->chunks = chunks; r->size = size;
                clock = now_ms();
                if (clock < 0) { perror("clock_gettime"); return 1; }
                deadline = clock + idle_ms;
            }
            if (reply(r, START_ACK, 0, NULL)) return 1;
            continue;
        }
        if (!r->active) continue;
        if (type == FIN) {
            if (length != 0 || sequence != r->chunks) continue;
            if (r->base != r->chunks || r->written != r->size) {
                if (reply(r, ACK, r->base, NULL)) return 1;
                continue;
            }
            if (!r->complete) {
                if (save_output(r)) return 1;
                r->complete = 1;
                clock = now_ms();
                if (clock < 0) { perror("clock_gettime"); return 1; }
                deadline = clock + linger_ms;
            } else ++r->duplicates;
            if (reply(r, FIN_ACK, r->chunks, NULL)) return 1;
            continue;
        }
        if (sequence >= r->chunks) continue;
        uint64_t remaining = r->size - (uint64_t)sequence * CHUNK;
        uint16_t expected = (uint16_t)(remaining > CHUNK ? CHUNK : remaining);
        if (length != expected) continue;
        if (sequence < r->base) {
            ++r->duplicates;
            if (reply(r, ACK, r->base, NULL)) return 1;
            continue;
        }
        /* The allowed interval is [base, base + window); holes retain slots. */
        if (r->paused || sequence - r->base >= receive_window(r)) {
            if (reply(r, ACK, r->base, NULL)) return 1;
            continue;
        }
        struct slot *slot = &r->slots[sequence % r->capacity];
        if (slot->present) {
            ++r->duplicates;
            if (reply(r, ACK, r->base, NULL)) return 1;
            continue;
        }
        memcpy(slot->data, packet + HEADER, length);
        slot->sequence = sequence; slot->length = length; slot->present = 1;
        ++r->buffered;
        if (r->buffered > r->peak_buffered) r->peak_buffered = r->buffered;
        uint32_t previous_base = r->base;
        while (r->base < r->chunks) {
            slot = &r->slots[r->base % r->capacity];
            if (!slot->present || slot->sequence != r->base) break;
            if (write_chunk(r->file_fd, slot->data, slot->length)) {
                report_file_error(r, r->base + 1, "write output");
                return 1;
            }
            r->written += slot->length;
            slot->present = 0; --r->buffered; ++r->base;
        }
        if (r->base != previous_base) {
            clock = now_ms();
            if (clock < 0) { perror("clock_gettime"); return 1; }
            deadline = clock + idle_ms;
        }
        if (reply(r, ACK, r->base, NULL)) return 1;
    }
}

int main(int argc, char **argv)
{
    unsigned long port, capacity = 64, idle_ms = 15000, linger_ms = 7000;
    struct sockaddr_in address;
    memset(&address, 0, sizeof address);
    address.sin_family = AF_INET;
    if (argc < 4 || argc > 7 ||
        inet_pton(AF_INET, argv[1], &address.sin_addr) != 1 ||
        number(argv[2], 1, 65535, &port) || !*argv[3] ||
        (argc >= 5 && number(argv[4], 1, 256, &capacity)) ||
        (argc >= 6 && number(argv[5], 1, 600000, &idle_ms)) ||
        (argc >= 7 && number(argv[6], 1, 600000, &linger_ms))) {
        fprintf(stderr, "Usage: %s BIND_IPV4 PORT OUTPUT_FILE "
                "[WINDOW_PACKETS [IDLE_TIMEOUT_MS [LINGER_MS]]]\n", argv[0]);
        return 2;
    }
    address.sin_port = htons((uint16_t)port);
    struct receiver r;
    memset(&r, 0, sizeof r);
    r.socket_fd = r.file_fd = -1;
    r.output = argv[3]; r.capacity = (unsigned)capacity;
    int result = 1;
    if (install_signals()) { perror("install signal handlers"); goto done; }
    r.slots = calloc(r.capacity, sizeof *r.slots);
    if (!r.slots) { perror("allocate receive window"); goto done; }
    if (create_temporary(&r)) goto done;
    r.socket_fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (r.socket_fd < 0) { perror("socket"); goto done; }
    if (bind(r.socket_fd, (struct sockaddr *)&address, sizeof address) < 0) {
        perror("bind"); goto done;
    }
    printf("READY %s:%lu pid=%ld\n", argv[1], port, (long)getpid());
    fflush(stdout);
    result = run_receiver(&r, (unsigned)idle_ms, (unsigned)linger_ms);
done:
    if (r.socket_fd >= 0) close(r.socket_fd);
    if (r.file_fd >= 0 && close(r.file_fd) < 0) {
        perror("close partial output"); result = 1;
    }
    if (r.temporary_exists && unlink(r.temporary) < 0) {
        perror("remove temporary output"); result = 1;
    }
    if (result == 0) {
        printf("PASS receiver bytes=%" PRIu64 " chunks=%" PRIu32
               " duplicates=%" PRIu64 " peak_buffered=%u\n",
               r.written, r.chunks, r.duplicates, r.peak_buffered);
    }
    free(r.temporary);
    free(r.slots);
    return result;
}
