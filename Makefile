CC = cc
CFLAGS = -std=c11 -Wall -Wextra -Wpedantic -O2
PYTHON = python3

.PHONY: all check check-probe check-receiver check-integration clean
all: build/udp-probe build/rudp-recv

build/rudp-recv: src/receiver.c
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDFLAGS) -o $@

build/udp-probe: src/udp_probe.c
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDFLAGS) -o $@

check: check-probe check-receiver

check-probe: build/udp-probe
	mkdir -p work/tmp
	TMPDIR="$(CURDIR)/work/tmp" $(PYTHON) scripts/check_probe.py ./build/udp-probe

check-receiver: build/rudp-recv
	mkdir -p work/tmp
	TMPDIR="$(CURDIR)/work/tmp" $(PYTHON) tests/test_receiver.py ./build/rudp-recv

# SENDER points to the independently built sender executable.
check-integration: build/rudp-recv
	@test -n "$(SENDER)" || { echo 'Set SENDER=/path/to/rudp-send'; exit 2; }
	mkdir -p work/tmp
	TMPDIR="$(CURDIR)/work/tmp" $(PYTHON) tests/test_transfer.py "$(SENDER)" ./build/rudp-recv

clean:
	rm -f build/udp-probe build/rudp-recv
