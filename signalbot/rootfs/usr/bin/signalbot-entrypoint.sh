#!/bin/sh
# signalbot-entrypoint.sh
#
# Flow:
#   1. Ensure the signal-cli config directory exists under /data (HA persistent storage).
#   2. Start supervisord so the signalbot-manager program is managed in all modes.
#      (The upstream bbernhard entrypoint only starts supervisord in json-rpc modes;
#       for MODE=normal we start it here instead.)
#   3. Ask supervisord to bring up signalbot-manager explicitly (|| true so a race
#      condition or already-started state does not abort the container).
#   4. Hand off to the original image entrypoint (/entrypoint.sh), which adjusts
#      the signal-api user UID/GID, chowns SIGNAL_CLI_CONFIG_DIR, and execs the
#      Go signal-cli-rest-api binary.
#
set -e

# 1. Ensure data directory exists
mkdir -p "${SIGNAL_CLI_CONFIG_DIR:-/data/signal-cli}"

# 2. Start supervisord (idempotent; safe to call even if already running)
service supervisor start

# 3. Bring up the manager (|| true: non-fatal if supervisord beat us to it)
supervisorctl start signalbot-manager || true

# 4. Hand off to the upstream entrypoint (sets up signal-api user and execs Go binary)
exec /entrypoint.sh "$@"
