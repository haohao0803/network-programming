CC = cc
CFLAGS = -std=c11 -Wall -Wextra -Wpedantic -O2
PYTHON = python3

TEST_TMPDIR ?= $(if $(TMPDIR),$(TMPDIR),/tmp)
export TMPDIR := $(TEST_TMPDIR)
SENDER ?= $(CURDIR)/build/rudp-send

.PHONY: all check check-probe check-sender check-receiver check-integration check-all clean
all: build/udp-probe build/rudp-send build/rudp-recv

build/rudp-send: src/sender.c src/congestion.c src/congestion.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) src/sender.c src/congestion.c $(LDFLAGS) -o $@

build/rudp-recv: src/receiver.c
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDFLAGS) -o $@

build/udp-probe: src/udp_probe.c
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDFLAGS) -o $@

check: check-probe check-sender check-receiver check-congestion

.PHONY: check-congestion check-adaptive
build/test-congestion: tests/test_congestion.c src/congestion.c src/congestion.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/test_congestion.c src/congestion.c $(LDFLAGS) -o $@

check-congestion: build/test-congestion
	./build/test-congestion

check-adaptive: build/rudp-send build/rudp-recv check-congestion
	mkdir -p "$(TMPDIR)"
	$(PYTHON) tests/test_adaptive.py ./build/rudp-send
	$(PYTHON) tests/test_adaptive_sender_contract.py ./build/rudp-send
	$(PYTHON) tests/test_adaptive_transfer.py ./build/rudp-send ./build/rudp-recv

check-probe: build/udp-probe
	mkdir -p "$(TMPDIR)"
	$(PYTHON) scripts/check_probe.py ./build/udp-probe

check-sender: build/rudp-send
	mkdir -p "$(TMPDIR)"
	$(PYTHON) tests/test_sender.py ./build/rudp-send
	$(PYTHON) tests/test_sender_window.py ./build/rudp-send
	$(PYTHON) tests/test_sender_trace.py ./build/rudp-send

check-receiver: build/rudp-recv
	mkdir -p "$(TMPDIR)"
	$(PYTHON) tests/test_receiver.py ./build/rudp-recv

# Override SENDER to compare a separately built reference executable.
check-integration: build/rudp-send build/rudp-recv
	mkdir -p "$(TMPDIR)"
	$(PYTHON) tests/test_transfer.py "$(SENDER)" ./build/rudp-recv
	$(PYTHON) tests/test_trace_transfer.py "$(SENDER)" ./build/rudp-recv

check-all: check check-integration

clean:
	rm -f build/udp-probe build/rudp-send build/rudp-recv build/test-congestion
