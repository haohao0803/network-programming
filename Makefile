CC = cc
CFLAGS = -std=c11 -Wall -Wextra -Wpedantic -O2
PYTHON = python3
SENDER = ./build/rudp-send
RECEIVER = ./build/rudp-recv

.PHONY: all check check-probe check-sender check-receiver check-integration clean
all: build/udp-probe build/rudp-send build/rudp-recv

build/rudp-send: src/sender.c
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDFLAGS) -o $@

build/rudp-recv: src/receiver.c
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDFLAGS) -o $@

build/udp-probe: src/udp_probe.c
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDFLAGS) -o $@

check: check-probe check-sender check-receiver

check-probe: build/udp-probe
	$(PYTHON) scripts/check_probe.py ./build/udp-probe

check-sender: build/rudp-send
	$(PYTHON) tests/test_sender.py $(SENDER)
	$(PYTHON) tests/test_sender_window.py $(SENDER)

check-receiver: build/rudp-recv
	$(PYTHON) tests/test_receiver.py $(RECEIVER)

check-integration: all
	$(PYTHON) tests/test_transfer.py $(SENDER) $(RECEIVER)

clean:
	rm -f build/udp-probe build/rudp-send build/rudp-recv
