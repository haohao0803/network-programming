"""Validate the Linux experiment environment in a temporary network namespace."""

import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


def main():
    if platform.system() != "Linux":
        print("FAIL Run this check on the Linux server.", flush=True)
        return 2
    if os.geteuid() != 0:
        print("Run: sudo python3 scripts/check_server.py", flush=True)
        return 2

    missing = [tool for tool in ("cc", "ip", "tc", "unshare", "sh")
               if shutil.which(tool) is None]
    if missing:
        print("FAIL Missing tools: " + ", ".join(missing), flush=True)
        print("Run: sudo sh scripts/server_setup.sh", flush=True)
        return 1

    repository = Path(__file__).resolve().parents[1]
    print("Kernel:", platform.release(), flush=True)
    print("Revision:", flush=True)
    if shutil.which("git"):
        subprocess.run(["git", "-c", "safe.directory=" + str(repository),
                        "-C", str(repository), "rev-parse", "HEAD"],
                       check=False, timeout=10)
    print("Make:", shutil.which("make") or "missing (run server_setup.sh)",
          flush=True)

    # The namespace is owned by this short-lived child. All network mutations
    # below happen after unshare succeeds, including the netem rule and veth pair.
    isolated_checks = '''
ip link set lo up
echo 'CHECK normal UDP round trip and error handling'
"$1" "$2" "$3"
echo 'CHECK netem delay on isolated loopback'
tc qdisc add dev lo root netem delay 5ms
"$1" "$2" "$3"
tc -s qdisc show dev lo
echo 'CHECK isolated veth creation'
ip link add rudp-check-a type veth peer name rudp-check-b
ip link show rudp-check-a
echo 'PASS isolated UDP, netem and veth checks'
'''
    try:
        with tempfile.TemporaryDirectory(prefix="rudp-server-check-") as directory:
            binary = Path(directory) / "udp-probe"
            subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Wpedantic", "-Werror",
                 "-O2", str(repository / "src/udp_probe.c"), "-o", str(binary)],
                check=True, timeout=60
            )
            print("PASS C11 compilation with warnings treated as errors", flush=True)
            subprocess.run(
                ["unshare", "--net", "sh", "-eu", "-c", isolated_checks,
                 "rudp-server-check", sys.executable,
                 str(repository / "scripts/check_probe.py"), str(binary)],
                check=True, timeout=40
            )
    except (subprocess.SubprocessError, OSError) as error:
        print("FAIL Server experiment check:", error, flush=True)
        print("Review the first error above. Namespace permissions and netem/veth "
              "kernel support are required.", flush=True)
        return 1

    missing_build_tools = [tool for tool in ("make", "git")
                           if shutil.which(tool) is None]
    if missing_build_tools:
        print("FAIL Install remaining build tools: " + ", ".join(missing_build_tools),
              flush=True)
        print("Run: sudo sh scripts/server_setup.sh", flush=True)
        return 1
    print("PASS Server prerequisites for the current UDP starter", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
