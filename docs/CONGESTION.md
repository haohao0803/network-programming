# LPCAQ: adaptive timer and congestion control

Branch: `feature/congestion-control`. Based on integration PR #3 at
`a37017a06ff42175169431c5ea847eed8ac440d5`; does not merge #3 or change main.
Sender/receiver and earlier test contributions retain their existing authors.
This contribution adds the parameter module, optional sender wiring, parameter
traces, deterministic formula tests and adaptive transfer checks.

## Run

```sh
make all
make check-all          # unchanged fixed-mode baseline
make check-adaptive     # formulas, simulated peers and real receiver
# Terminal A: choose a new output path
./build/rudp-recv 127.0.0.1 19090 received.bin 64 20000 7000
# Terminal B: 1000 ms initial RTO, 2 extra retries, 16-packet allocation cap
./build/rudp-send 127.0.0.1 19090 input.bin 1000 2 16 --adaptive --trace run.csv
```

Without `--adaptive`, timing/window behavior and CSV schema 1 remain the fixed
comparison baseline. Adaptive mode uses CSV schema 2. `--trace` creates a new
file exclusively. Initial CLI RTO in adaptive mode is 1000–10000 ms; subsequent
DATA RTO is clamped to 1000–60000 ms. This conservative floor deliberately differs
from the 10 ms minimum allowed for fixed-mode tests.

## C interface and ownership

`src/congestion.h` has no socket, OS or logging dependencies. Link
`src/congestion.c` alongside the sender. State is per transfer, single-threaded.

| API | Inputs | Result |
| --- | --- | --- |
| `rudp_cc_init(cc, initial_rto_ms, max_packets)` | RTO 1000–60000 ms, cap 1–256 DATA packets | 0 success; -1 invalid, state unchanged |
| `rudp_cc_ack(cc, newly_acked, rtt_ms)` | number of newly acknowledged packets; sample in ms or -1 for ambiguous | updates estimator and cwnd; zero credit changes nothing |
| `rudp_cc_timeout(cc, flight_packets, loss)` | number of outstanding packets; nonzero loss for DATA timeout, zero for persist | doubles RTO, optionally reduces cwnd |
| `rudp_cc_window(cc, receive_packets)` | advertised sequence span in packets | floor(min(cwnd, configured cap, receive span)) |

Caller validates IDs, framing and `base <= ACK <= next` before calling. It owns
the monotonic clock, packet cache, timers, retransmission, retry budget and CSV.
Module calls require successfully initialized state. Duplicate/stale/invalid
ACKs do not update RTT, cwnd or deadlines. A duplicate valid ACK may update rwnd.

## Exact rules and hand calculation

- Initial cwnd=1 packet; initial ssthresh=max(2, configured packet cap).
- First valid RTT sample R: SRTT=R, RTTVAR=R/2. A measured zero becomes 1 ms
  because the sender clock has 1 ms resolution.
- Later samples: RTTVAR=0.75*old_RTTVAR+0.25*abs(old_SRTT-R), then
  SRTT=0.875*old_SRTT+0.125*R.
- RTO=ceil(SRTT+max(1 ms,4*RTTVAR)), clamped to 1000–60000 ms.
- For each newly acknowledged packet, cwnd grows by 1 while below ssthresh;
  otherwise by 1/cwnd. Growth stops at configured cap. Cumulative ACK credit is
  processed per packet, including crossing ssthresh.
- On DATA timeout: ssthresh=max(2,floor(outstanding_packets/2)), cwnd=1,
  RTO=min(2*RTO,60000). On zero-window persist timeout: back off RTO only.
- No fast retransmit or fast recovery: this is a course slow-start/congestion-
  avoidance controller using cumulative ACK and go-back-N, not full TCP Reno.

Example: samples 1000 ms and 1200 ms give (SRTT,RTTVAR,RTO) of
(1000,500,3000) then (1025,425,2725). Timeout with 8 packets outstanding gives
RTO=5450, ssthresh=4, cwnd=1. Three new packet acknowledgments without a valid
sample grow cwnd to 4 and retain RTO=5450. The next ACK grows cwnd to 4.25.

## Sender integration details

Only DATA uses the adaptive controller. START/FIN retain the configured fixed
timeout/retry budget so the receiver's documented completion linger contract
continues to apply. Control ACKs never provide DATA RTT samples or cwnd credit.
Control event `rto_ms` is the configured control timeout; DATA events use current
DATA RTO. Timer is restarted on new cumulative progress and after timeout;
duplicate replies and rwnd changes do not extend it.

Every cached packet stores its first send timestamp. At timeout, the entire
outstanding flight is marked ambiguous, even if only part is retransmitted.
An ACK range containing any such packet supplies no RTT sample (Karn rule).
For an entirely unambiguous newly acknowledged range, sample the last packet.
Ambiguous ACKs still provide cwnd credit and reset the consecutive retry budget,
but retain the backed-off RTO until a valid sample arrives.

New DATA and retries obey current effective window. Previously sent packets can
remain in flight after cwnd/rwnd shrinks; they are retained in the bounded ring,
not discarded. Retrying starts at base and is constrained by the reduced window.
When rwnd=0, no new DATA is sent; existing START/last-ACKed-DATA probes are used.
Persist timeout is not treated as network congestion.

DATA can now wait much longer than the fixed baseline. For N consecutive waits,
budget is at most the sum of the capped exponential RTOs, not (N*initial RTO).
With initial RTO=1000 and 2 extra retries the no-progress budget is at most
1+2+4=7 seconds (excluding processing). New progress resets this budget.
Receiver idle timeout must be chosen for the expected silence; do not use short
fixed-test idle settings for large backed-off RTOs. This PR does not claim VPS,
netem or performance validation.

## Parameter logs and validation

Schema 2 preserves all schema 1 column names and adds:

| Column | Unit / meaning |
| --- | --- |
| `rtt_sample_ms` | latest DATA ACK sample, -1 when ambiguous/no sample |
| `srtt_ms`, `rttvar_ms` | ms; 0 before the first sample |
| `cwnd_packets` | fractional DATA packets; floor used to send |
| `ssthresh_packets` | DATA packets |
| `effective_window_packets` | packets; capped by rwnd and allocation limit |

`elapsed_ms` uses CLOCK_MONOTONIC relative to process start. `rto_ms` is ms;
`receive_window` and `configured_window` are DATA packets. Event names remain
`send`, `ack`, `timeout`, `retransmit`, `probe`, `window_update`, control and
completion events. A timeout row records parameters after backoff/shrink.

`tests/test_congestion.c` checks the numeric example, ACK credit, threshold
crossing, persist and caps. `tests/test_adaptive.py` tests real sender events
against simulated peers, including Karn suppression, loss, zero window and
bounded failure. `tests/test_adaptive_transfer.py` uses the actual receiver and
existing fault relay to verify ten file/hash and fault scenarios.
Set `RUDP_ADAPTIVE_EVIDENCE=results/adaptive` to save actual CSVs and JSON logs.
The Transport validation workflow runs strict builds, the fixed regression
suite, and adaptive tests on Ubuntu and macOS; artifacts contain raw results.
Workflow results, not this document's existence, determine validation status.
