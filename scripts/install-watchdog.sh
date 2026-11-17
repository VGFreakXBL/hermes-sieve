#!/usr/bin/env bash
# Render the watchdog plist for THIS machine.
#
# The shipped plist is a template: launchd needs absolute paths, and a plugin
# repo must not carry one user's home directory. This substitutes
# __HERMES_HOME__ and writes the result where launchd looks.
#
# This does NOT load the job. Loading is one command, run by the user:
#   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vgfreak.sieve-watch.plist
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SRC="$PLUGIN_DIR/scripts/com.vgfreak.sieve-watch.plist.template"
DEST="$HOME/Library/LaunchAgents/com.vgfreak.sieve-watch.plist"
PY="$HERMES_HOME/hermes-agent/venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "error: no hermes python at $PY (set HERMES_HOME if your root differs)" >&2
  exit 1
fi
if [ ! -f "$SRC" ]; then
  echo "error: template missing at $SRC" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HERMES_HOME/logs"

python3 - "$SRC" "$DEST" "$HERMES_HOME" <<'PY'
import pathlib
import sys

src, dest, home = sys.argv[1], sys.argv[2], sys.argv[3]
text = pathlib.Path(src).read_text(encoding="utf-8").replace("__HERMES_HOME__", home)
pathlib.Path(dest).write_text(text, encoding="utf-8")
print(f"rendered {dest} (HERMES_HOME={home})")
PY

echo "now load it once: launchctl bootstrap gui/$(id -u) $DEST"
