#include "../src/congestion.h"
#include <assert.h>
#include <stdio.h>

static int close_to(double a, double b)
{ double d = a - b; return d < 0.00001 && d > -0.00001; }

int main(void)
{
    struct rudp_cc cc;
    assert(rudp_cc_init(&cc, 1000, 16) == 0);
    assert(rudp_cc_init(&cc, 999, 16) == -1);
    assert(rudp_cc_init(&cc, 1000, 0) == -1);
    assert(rudp_cc_init(&cc, 1000, 257) == -1);
    assert(cc.rto_ms == 1000 && cc.cwnd_packets == 1);
    rudp_cc_ack(&cc, 1, 1000);
    assert(cc.has_rtt && cc.rto_ms == 3000);
    assert(close_to(cc.srtt_ms, 1000) && close_to(cc.rttvar_ms, 500));
    assert(cc.cwnd_packets == 2);
    rudp_cc_ack(&cc, 2, 1200);
    assert(close_to(cc.srtt_ms, 1025) && close_to(cc.rttvar_ms, 425));
    assert(cc.rto_ms == 2725 && cc.cwnd_packets == 4);
    rudp_cc_ack(&cc, 0, 50000); /* Duplicate ACK cannot alter any parameter. */
    assert(cc.rto_ms == 2725 && cc.cwnd_packets == 4);
    rudp_cc_timeout(&cc, 8, 1);
    assert(cc.rto_ms == 5450 && cc.ssthresh_packets == 4 && cc.cwnd_packets == 1);
    rudp_cc_ack(&cc, 3, -1);
    assert(cc.rto_ms == 5450 && cc.cwnd_packets == 4);
    rudp_cc_ack(&cc, 1, -1);
    assert(close_to(cc.cwnd_packets, 4.25));
    assert(rudp_cc_window(&cc, 2) == 2 && rudp_cc_window(&cc, 0) == 0);
    rudp_cc_timeout(&cc, 0, 0);
    assert(cc.rto_ms == 10900 && close_to(cc.cwnd_packets, 4.25));
    for (int i = 0; i < 20; ++i) rudp_cc_timeout(&cc, 1, 1);
    assert(cc.rto_ms == 60000 && cc.ssthresh_packets == 2 && cc.cwnd_packets == 1);
    rudp_cc_ack(&cc, 1, 0);
    assert(cc.rto_ms >= 1000 && cc.rto_ms <= 60000);
    for (int i = 0; i < 2000; ++i) rudp_cc_ack(&cc, 256, -1);
    assert(cc.cwnd_packets == 16 && rudp_cc_window(&cc, 256) == 16);
    assert(rudp_cc_init(&cc, 1000, 1) == 0);
    rudp_cc_ack(&cc, 1, 1000000);
    assert(cc.rto_ms == 60000 && rudp_cc_window(&cc, 100) == 1);
    puts("PASS congestion: RTT formula, Karn input, duplicate ACK, slow start, congestion avoidance, loss, persist, caps");
    return 0;
}
