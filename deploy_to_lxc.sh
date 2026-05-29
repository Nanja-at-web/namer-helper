#!/usr/bin/env bash
# Deploy namer-helper source to LXC container via pct push.
# Run this script ON PROXBOX (not inside the container).
# Usage: bash deploy_to_lxc.sh [container-id]
set -euo pipefail

CT="${1:-103}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/src/namer_helper"

# Auto-detect install path inside the container
SITE=$(pct exec "$CT" -- find /opt /usr/local /root -name "app.py" -path "*/namer_helper/web/*" 2>/dev/null | head -1 | sed 's|/web/app.py||')
if [ -z "$SITE" ]; then
  echo "ERROR: Could not find namer_helper install path in container $CT"
  exit 1
fi
echo "Found install path: $SITE"
TMPL="$SITE/web/templates"

# Helper: push a file from this host into the container
push() {
  local src="$1" dst="$2"
  pct push "$CT" "$src" "$dst" --perms 644
}

# ── Python modules ────────────────────────────────────────────────────────────
# Keep the installed package in sync with the source tree. A fixed file list is
# easy to forget when adding helper modules.
while IFS= read -r -d '' py_file; do
  rel="${py_file#$SRC/}"
  push "$py_file" "$SITE/$rel"
done < <(find "$SRC" -type f -name "*.py" -not -path "*/__pycache__/*" -print0)

# ── Templates ─────────────────────────────────────────────────────────────────
push "$SRC/web/templates/base.html"             "$TMPL/base.html"
push "$SRC/web/templates/dashboard.html"        "$TMPL/dashboard.html"
push "$SRC/web/templates/failed.html"           "$TMPL/failed.html"
push "$SRC/web/templates/mounts.html"           "$TMPL/mounts.html"
push "$SRC/web/templates/pre-check.html"        "$TMPL/pre-check.html"
push "$SRC/web/templates/proxmox.html"          "$TMPL/proxmox.html"
push "$SRC/web/templates/settings.html"         "$TMPL/settings.html"

# ── Restart service ───────────────────────────────────────────────────────────
echo "Restarting namer-helper…"
pct exec "$CT" -- systemctl restart namer-helper
echo "Done. http://$(pct exec "$CT" -- hostname -I | awk '{print $1}'):6981/"
