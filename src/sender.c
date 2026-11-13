#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

/* Wire layout is encoded explicitly; never transmit a native C struct. */
enum { HEADER = 20, CHUNK = 1024, START = 1, START_ACK = 2,
       DATA = 3, ACK = 4, FIN = 5, FIN_ACK = 6, ERROR_PACKET = 7 };
#define MAX_FILE_BYTES (UINT64_C(1024) * 1024 * 1024)
static uint64_t retransmissions;

static void put16(unsigned char *p, uint16_t n)
{ p[0] = (unsigned char)(n >> 8); p[1] = (unsigned char)n; }
static void put32(unsigned char *p, uint32_t n)
{ put16(p, (uint16_t)(n >> 16)); put16(p + 2, (uint16_t)n); }
static uint16_t get16(const unsigned char *p)
{ return (uint16_t)(((uint16_t)p[0] << 8) | p[1]); }
static uint32_t get32(const unsigned char *p)
{ return ((uint32_t)get16(p) << 16) | get16(p + 2); }

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

static size_t header(unsigned char *p, unsigned type, uint32_t id,
                     uint32_t seq, uint16_t length)
{
    memset(p, 0, HEADER);
    memcpy(p, "RUDP", 4);
    p[4] = 1; p[5] = (unsigned char)type;
    put32(p + 8, id); put32(p + 12, seq); put16(p + 16, length);
    return HEADER + length;
}

/* An irrelevant datagram never resets the deadline or grants another retry. */
static int exchange(int fd, const unsigned char *packet, size_t length,
                    unsigned expected_type, uint32_t id, uint32_t expected_seq,
                    unsigned timeout_ms, unsigned retries, uint16_t *window)
{
    for (unsigned attempt = 0; attempt <= retries; ++attempt) {
        ssize_t sent;
        do { sent = send(fd, packet, length, 0); } while (sent < 0 && errno == EINTR);
        if (sent < 0 || (size_t)sent != length) { perror("send"); return -1; }
        if (attempt) ++retransmissions;
        int64_t start = now_ms();
        if (start < 0) { perror("clock_gettime"); return -1; }
        int64_t deadline = start + timeout_ms;
        for (;;) {
            int64_t current = now_ms();
            if (current < 0) { perror("clock_gettime"); return -1; }
            if (current >= deadline) break;
            struct pollfd pfd = { fd, POLLIN, 0 };
            int ready = poll(&pfd, 1, (int)(deadline - current));
            if (ready < 0 && errno == EINTR) continue;
            if (ready < 0) { perror("poll"); return -1; }
            if (!ready) continue;
            unsigned char reply[HEADER + CHUNK + 1];
            ssize_t n = recv(fd, reply, sizeof reply, MSG_DONTWAIT);
            if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
            if (n < 0) { perror("recv"); return -1; }
            if (n < HEADER || n > HEADER + CHUNK || memcmp(reply, "RUDP", 4) ||
                reply[4] != 1 || get16(reply + 6) != 0 ||
                get32(reply + 8) != id || (size_t)n != (size_t)HEADER + get16(reply + 16)) continue;
            if (reply[5] == ERROR_PACKET && get32(reply + 12) == expected_seq &&
                get16(reply + 16) <= 256) {
                fprintf(stderr, "Receiver reported an error; transfer not confirmed.\n");
                return -1;
            }
            if (reply[5] != expected_type || get32(reply + 12) != expected_seq ||
                get16(reply + 16) != 0) continue;
            *window = get16(reply + 18);
            return 0;
        }
        fprintf(stderr, "timeout type=%u sequence=%" PRIu32 " attempt=%u/%u\n",
                packet[5], get32(packet + 12), attempt + 1, retries + 1);
    }
    fprintf(stderr, "Retry budget exhausted; transfer not confirmed.\n");
    return -1;
}

int main(int argc, char **argv)
{
    unsigned long port, timeout = 1000, retries = 5;
    struct sockaddr_in peer;
    memset(&peer, 0, sizeof peer);
    peer.sin_family = AF_INET;
    if ((argc != 4 && argc != 6) ||
        number(argv[2], 1, 65535, &port) ||
        inet_pton(AF_INET, argv[1], &peer.sin_addr) != 1 ||
        (argc == 6 && (number(argv[4], 10, 10000, &timeout) ||
                       number(argv[5], 0, 20, &retries)))) {
        fprintf(stderr, "Usage: %s IPV4 PORT FILE [TIMEOUT_MS RETRIES]\n", argv[0]);
        return 2;
    }
    peer.sin_port = htons((uint16_t)port);
    /* Nonblocking open also prevents a FIFO path from hanging before fstat. */
    int input = open(argv[3], O_RDONLY | O_NONBLOCK);
    if (input < 0) { perror("open input"); return 2; }
    struct stat st;
    if (fstat(input, &st) || !S_ISREG(st.st_mode) || st.st_size < 0 ||
        (uint64_t)st.st_size > MAX_FILE_BYTES) {
        fprintf(stderr, "Input must be a regular file of at most 1 GiB.\n");
        close(input); return 2;
    }
    uint64_t size = (uint64_t)st.st_size;
    uint32_t chunks = (uint32_t)((size + CHUNK - 1) / CHUNK);
    uint32_t id;
    int random_fd = open("/dev/urandom", O_RDONLY);
    ssize_t random_bytes = -1;
    if (random_fd >= 0) {
        do { random_bytes = read(random_fd, &id, sizeof id); }
        while (random_bytes < 0 && errno == EINTR);
        close(random_fd);
    }
    if (random_bytes != sizeof id) {
        fprintf(stderr, "Unable to create a random transfer ID.\n");
        close(input); return 1;
    }
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) { perror("socket"); close(input); return 1; }
    int result = 1;
    if (connect(fd, (struct sockaddr *)&peer, sizeof peer)) { perror("connect"); goto done; }
    unsigned char packet[HEADER + CHUNK];
    uint16_t window = 0;
    size_t length = header(packet, START, id, 0, 12);
    put32(packet + HEADER, chunks);
    put32(packet + HEADER + 4, (uint32_t)(size >> 32));
    put32(packet + HEADER + 8, (uint32_t)size);
    if (exchange(fd, packet, length, START_ACK, id, 0,
                 (unsigned)timeout, (unsigned)retries, &window)) goto done;
    for (uint32_t seq = 0; seq < chunks; ++seq) {
        if (!window) {
            fprintf(stderr, "Zero receive window: stop-and-wait baseline cannot probe yet.\n");
            goto done;
        }
        size_t wanted = size - (uint64_t)seq * CHUNK > CHUNK ?
                        CHUNK : (size_t)(size - (uint64_t)seq * CHUNK);
        length = header(packet, DATA, id, seq, (uint16_t)wanted);
        size_t have = 0;
        while (have < wanted) {
            ssize_t n = read(input, packet + HEADER + have, wanted - have);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) { fprintf(stderr, "Input read failed or file shrank.\n"); goto done; }
            have += (size_t)n;
        }
        if (exchange(fd, packet, length, ACK, id, seq + 1,
                     (unsigned)timeout, (unsigned)retries, &window)) goto done;
    }
    unsigned char extra;
    ssize_t extra_bytes;
    do { extra_bytes = read(input, &extra, 1); } while (extra_bytes < 0 && errno == EINTR);
    if (extra_bytes != 0 || fstat(input, &st) || (uint64_t)st.st_size != size) {
        fprintf(stderr, "Input changed or could not be checked; completion not sent.\n"); goto done;
    }
    length = header(packet, FIN, id, chunks, 0);
    if (exchange(fd, packet, length, FIN_ACK, id, chunks,
                 (unsigned)timeout, (unsigned)retries, &window)) goto done;
    printf("PASS sender bytes=%" PRIu64 " chunks=%" PRIu32 " retransmissions=%" PRIu64 "\n",
           size, chunks, retransmissions);
    result = 0;
done:
    close(fd); close(input);
    return result;
}
