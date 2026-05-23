#!/usr/bin/env bash
set -euo pipefail

FAILED_DIR="${NAMER_FAILED_DIR:-/var/lib/namer/failed}"
OUTPUT_DIR="${REPORT_OUTPUT_DIR:-/var/lib/namer-helper/reports}"
INTERVAL="${REPORT_INTERVAL:-3600}"

# Wenn Argumente übergeben: direkter CLI-Aufruf (z.B. docker exec ... analyze)
if [[ $# -gt 0 ]]; then
  exec namer-helper "$@"
fi

# Daemon-Modus: Report periodisch ausführen
echo "namer-helper daemon — Report alle ${INTERVAL}s (failed: ${FAILED_DIR})"
while true; do
  echo "[$(date -Iseconds)] Starte report..."
  namer-helper report \
    --failed-dir "${FAILED_DIR}" \
    --output-dir "${OUTPUT_DIR}" \
    || echo "[$(date -Iseconds)] report fehlgeschlagen (weiter)"
  sleep "${INTERVAL}"
done
