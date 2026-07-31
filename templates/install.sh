#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$SCRIPT_DIR/lib/vapor_installer.py" install --bundle-root "$SCRIPT_DIR" "$@"
