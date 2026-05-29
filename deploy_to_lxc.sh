#!/usr/bin/env bash
# Deploy namer-helper source to LXC container via pct push.
# Run this script ON PROXBOX (not inside the container).
# Usage: bash deploy_to_lxc.sh [container-id]
set -euo pipefail

CT="${1:-103}"

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
push /tmp/nh_deploy/namer_bridge/log_parser.py     "$SITE/namer_bridge/log_parser.py"
push /tmp/nh_deploy/stash_bridge/stashdb.py        "$SITE/stash_bridge/stashdb.py"
push /tmp/nh_deploy/web/ai_config.py               "$SITE/web/ai_config.py"
push /tmp/nh_deploy/web/app.py                     "$SITE/web/app.py"

# ── Templates ─────────────────────────────────────────────────────────────────
push /tmp/nh_deploy/templates/base.html            "$TMPL/base.html"
push /tmp/nh_deploy/templates/dashboard.html       "$TMPL/dashboard.html"
push /tmp/nh_deploy/templates/failed.html          "$TMPL/failed.html"
push /tmp/nh_deploy/templates/mounts.html          "$TMPL/mounts.html"
push /tmp/nh_deploy/templates/proxmox.html         "$TMPL/proxmox.html"
push /tmp/nh_deploy/templates/settings.html        "$TMPL/settings.html"

# ── Restart service ───────────────────────────────────────────────────────────
echo "Restarting namer-helper…"
pct exec "$CT" -- systemctl restart namer-helper
echo "Done. http://$(pct exec "$CT" -- hostname -I | awk '{print $1}'):6981/"
