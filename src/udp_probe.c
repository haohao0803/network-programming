#define _POSIX_C_SOURCE 200809L

#include <arpa/inet.h>
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

/* Loopback environment check, before the file protocol is implemented. */
static const char probe[] = "RUDP-PROBE-1";

static int receive_probe(int fd, int timeout_ms, struct sockaddr_in *peer)
{
    struct pollfd pending = {.fd = fd, .events = POLLIN};
    int ready = poll(&pending, 1, timeout_ms);
    if (ready < 0) {
        perror("poll");
        return -1;
    }
    if (ready == 0) {
        fprintf(stderr, "Timed out waiting for UDP probe\n");
        return -1;
    }
    char buffer[sizeof(probe)];
    socklen_t peer_length = sizeof(*peer);
    ssize_t length = recvfrom(fd, buffer, sizeof(buffer), 0,
                             (struct sockaddr *)peer, &peer_length);
    if (length < 0) {
        perror("recvfrom");
        return -1;
    }
    if (length != (ssize_t)(sizeof(probe) - 1) ||
        memcmp(buffer, probe, sizeof(probe) - 1) != 0) {
        fprintf(stderr, "Unexpected UDP payload\n");
        return -1;
    }
    return 0;
}

static int send_probe(int fd, const struct sockaddr_in *peer)
{
    ssize_t length = sendto(fd, probe, sizeof(probe) - 1, 0,
                           (const struct sockaddr *)peer, sizeof(*peer));
    if (length != (ssize_t)(sizeof(probe) - 1)) {
        perror("sendto");
        return -1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    int listen_mode = argc == 3 && strcmp(argv[1], "listen") == 0;
    int send_mode = argc == 4 && strcmp(argv[1], "send") == 0;
    if (!listen_mode && !send_mode) {
        fprintf(stderr, "Usage: %s listen PORT | send 127.0.0.1 PORT\n", argv[0]);
        return 2;
    }
    char *end = NULL;
    const char *port_text = argv[argc - 1];
    errno = 0;
    long port = strtol(port_text, &end, 10);
    if (errno != 0 || end == port_text || *end != '\0' ||
        port < 1 || port > 65535) {
        fprintf(stderr, "Port must be an integer from 1 to 65535\n");
        return 2;
    }
    if (send_mode && strcmp(argv[2], "127.0.0.1") != 0) {
        fprintf(stderr, "This probe supports loopback address 127.0.0.1 only\n");
        return 2;
    }
    struct sockaddr_in address = {
        .sin_family = AF_INET,
        .sin_port = htons((unsigned short)port)
    };
    if (inet_pton(AF_INET, "127.0.0.1", &address.sin_addr) != 1) {
        fprintf(stderr, "Invalid loopback address\n");
        return 1;
    }
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) {
        perror("socket");
        return 1;
    }
    struct sockaddr_in peer = {0};
    int result;
    if (listen_mode) {
        if (bind(fd, (struct sockaddr *)&address, sizeof(address)) < 0) {
            perror("bind");
            close(fd);
            return 1;
        }
        printf("READY 127.0.0.1:%ld\n", port);
        fflush(stdout);
        result = receive_probe(fd, 10000, &peer);
        if (result == 0) {
            result = send_probe(fd, &peer);
        }
    } else {
        result = send_probe(fd, &address);
        if (result == 0) {
            result = receive_probe(fd, 2000, &peer);
        }
        if (result == 0 && (peer.sin_port != address.sin_port ||
                           peer.sin_addr.s_addr != address.sin_addr.s_addr)) {
            fprintf(stderr, "Response came from an unexpected endpoint\n");
            result = -1;
        }
    }
    close(fd);
    if (result != 0) {
        return 1;
    }
    puts("PASS UDP loopback round trip");
    return 0;
}
