#include "congestion.h"

enum { MIN_RTO_MS = 1000, MAX_RTO_MS = 60000, MAX_PACKETS = 256 };

int rudp_cc_init(struct rudp_cc *cc, unsigned initial_rto_ms,
                 unsigned max_packets)
{
    if (!cc || initial_rto_ms < MIN_RTO_MS || initial_rto_ms > MAX_RTO_MS ||
        !max_packets || max_packets > MAX_PACKETS) return -1;
    *cc = (struct rudp_cc){0};
    cc->rto_ms = initial_rto_ms;
    cc->max_packets = max_packets;
    cc->cwnd_packets = 1;
    cc->ssthresh_packets = max_packets < 2 ? 2 : max_packets;
    return 0;
}

void rudp_cc_ack(struct rudp_cc *cc, unsigned newly_acked, int64_t rtt_ms)
{
    if (!newly_acked) return;
    if (rtt_ms >= 0) {
        double sample = rtt_ms > 0 ? (double)rtt_ms : 1.0;
        if (!cc->has_rtt) {
            cc->srtt_ms = sample;
            cc->rttvar_ms = sample / 2;
            cc->has_rtt = 1;
        } else {
            double delta = cc->srtt_ms - sample;
            if (delta < 0) delta = -delta;
            cc->rttvar_ms = 0.75 * cc->rttvar_ms + 0.25 * delta;
            cc->srtt_ms = 0.875 * cc->srtt_ms + 0.125 * sample;
        }
        double variation = 4 * cc->rttvar_ms;
        double rto = cc->srtt_ms + (variation > 1 ? variation : 1);
        if (rto < MIN_RTO_MS) rto = MIN_RTO_MS;
        if (rto > MAX_RTO_MS) rto = MAX_RTO_MS;
        cc->rto_ms = (unsigned)rto;
        if ((double)cc->rto_ms < rto) ++cc->rto_ms;
    }
    /* Process cumulative ACK credit per packet, including threshold crossing. */
    for (unsigned i = 0; i < newly_acked && cc->cwnd_packets < cc->max_packets; ++i) {
        cc->cwnd_packets += cc->cwnd_packets < cc->ssthresh_packets
                           ? 1.0 : 1.0 / cc->cwnd_packets;
        if (cc->cwnd_packets > cc->max_packets)
            cc->cwnd_packets = cc->max_packets;
    }
}

void rudp_cc_timeout(struct rudp_cc *cc, unsigned flight_packets, int loss)
{
    cc->rto_ms = cc->rto_ms >= MAX_RTO_MS / 2 ? MAX_RTO_MS : cc->rto_ms * 2;
    if (loss) {
        cc->ssthresh_packets = flight_packets / 2;
        if (cc->ssthresh_packets < 2) cc->ssthresh_packets = 2;
        cc->cwnd_packets = 1;
    }
}

unsigned rudp_cc_window(const struct rudp_cc *cc, unsigned receive_packets)
{
    unsigned window = (unsigned)cc->cwnd_packets;
    if (window > cc->max_packets) window = cc->max_packets;
    if (window > receive_packets) window = receive_packets;
    return window;
}
