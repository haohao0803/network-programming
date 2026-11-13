CC = cc
CFLAGS = -std=c11 -Wall -Wextra -Wpedantic -O2
PYTHON = python3

.PHONY: all check clean
all: build/udp-probe build/rudp-send

build/rudp-send: src/sender.c
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDFLAGS) -o $@

build/udp-probe: src/udp_probe.c
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDFLAGS) -o $@

check: all
	$(PYTHON) scripts/check_probe.py ./build/udp-probe
	$(PYTHON) tests/test_sender.py ./build/rudp-send

clean:
	rm -f build/udp-probe build/rudp-send
