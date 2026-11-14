#!/bin/sh
# Select the project-local Linux tc without changing the global PATH or /usr.
set -eu
project_root=$(cd "$(dirname "$0")/.." && pwd)
local_tc_dir="$project_root/build/tools/bin"
if [ "$(uname -s)" != Linux ]; then
    echo 'Use this helper in Linux/WSL. See docs/LOCAL_TC.md.' >&2
    exit 2
fi
if [ ! -x "$local_tc_dir/tc" ]; then
    echo 'Project-local tc is not built. Follow docs/LOCAL_TC.md.' >&2
    exit 2
fi
if [ "$#" -eq 0 ]; then
    exec "$local_tc_dir/tc" -V
fi
exec env PATH="$local_tc_dir:$PATH" "$@"
