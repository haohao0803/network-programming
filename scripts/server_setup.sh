#!/bin/sh
set -eu

if [ "$(uname -s)" != Linux ]; then
    echo 'Run this setup on the Ubuntu/Debian server.' >&2
    exit 2
fi
if [ "$(id -u)" != 0 ]; then
    echo 'Run: sudo sh scripts/server_setup.sh' >&2
    exit 2
fi

. /etc/os-release
case "$ID" in
    ubuntu|debian) ;;
    *) echo 'This installer supports Ubuntu and Debian.' >&2; exit 2 ;;
esac

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential python3 git iproute2 util-linux

echo 'Dependencies installed. Run make as the repository owner.'
echo 'Then run: sudo python3 scripts/check_server.py'
