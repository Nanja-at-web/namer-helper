#!/usr/bin/env bash
set -euo pipefail

CONFIG="${HELPER_CONFIG:-/etc/namer-helper/helper.yaml}"
INTERVAL="${REPORT_INTERVAL:-3600}"

# Wenn Argumente übergeben: direkter CLI-Aufruf (z.B. docker exec ... analyze)
if [[ $# -gt 0 ]]; then
  exec namer-helper "$@"
fi

# Daemon-Modus: Report periodisch ausführen
echo "namer-helper daemon — Report alle ${INTERVAL}s (config: ${CONFIG})"
while true; do
  echo "[$(date -Iseconds)] Starte report..."
  namer-helper --config "${CONFIG}" report || echo "[$(date -Iseconds)] report fehlgeschlagen (weiter)"
  sleep "${INTERVAL}"
done
