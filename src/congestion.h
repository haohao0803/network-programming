#ifndef RUDP_CONGESTION_H
#define RUDP_CONGESTION_H

#include <stdint.h>

/* Caller owns the monotonic clock, ACK validation, timer and retry budget.
 * All times are milliseconds; all windows/counts are DATA packets. */
struct rudp_cc {
    double srtt_ms, rttvar_ms, cwnd_packets, ssthresh_packets;
    unsigned rto_ms, max_packets;
    int has_rtt;
};

/* Returns -1 for an invalid configuration, leaving state unchanged. */
int rudp_cc_init(struct rudp_cc *cc, unsigned initial_rto_ms,
                 unsigned max_packets);
/* newly_acked > 0 only. rtt_ms < 0 means no unambiguous RTT sample. */
void rudp_cc_ack(struct rudp_cc *cc, unsigned newly_acked, int64_t rtt_ms);
/* loss=0 is a persist timeout: back off RTO without shrinking cwnd. */
void rudp_cc_timeout(struct rudp_cc *cc, unsigned flight_packets, int loss);
unsigned rudp_cc_window(const struct rudp_cc *cc, unsigned receive_packets);

#endif
