"""Check the public CLI on Linux and macOS using the Python standard library."""

from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


def main():
    executable = str(Path(sys.argv[1]).resolve())
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as port_picker:
        port_picker.bind(("127.0.0.1", 0))
        port = str(port_picker.getsockname()[1])

    with tempfile.TemporaryFile(mode="w+t") as output:
        server = subprocess.Popen(
            [executable, "listen", port], stdout=output, stderr=subprocess.STDOUT
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                output.seek(0)
                if "READY " in output.read():
                    break
                if server.poll() is not None:
                    raise RuntimeError("Listener exited before it was ready")
                time.sleep(0.02)
            else:
                raise RuntimeError("Listener did not become ready")

            subprocess.run(
                [executable, "send", "127.0.0.1", port], check=True, timeout=5
            )
            if server.wait(timeout=5) != 0:
                raise RuntimeError("Listener failed")
        finally:
            if server.poll() is None:
                server.terminate()
                server.wait(timeout=5)
            output.seek(0)
            print(output.read(), end="")

    invalid = subprocess.run(
        [executable, "listen", "70000"], capture_output=True, text=True, timeout=5
    )
    if invalid.returncode != 2 or "Port must" not in invalid.stderr:
        raise RuntimeError("Invalid port was not rejected")

    # Keep a port open without replying to exercise the bounded client timeout.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as silent_peer:
        silent_peer.bind(("127.0.0.1", 0))
        port = str(silent_peer.getsockname()[1])
        failed = subprocess.run(
            [executable, "send", "127.0.0.1", port],
            capture_output=True, text=True, timeout=5
        )
    if failed.returncode != 1 or "Timed out" not in failed.stderr:
        raise RuntimeError("Silent peer did not produce a bounded failure")
    print("PASS invalid port and silent-peer timeout")


if __name__ == "__main__":
    main()
