"""
Proxmox SSH integration: run host-level commands from within the LXC container.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

_KEY_PATH = Path("/etc/namer-helper/.ssh/id_ed25519")


@dataclass
class ProxmoxConfig:
    host: str = ""
    user: str = "root"
    port: int = 22
    lxc_id: str = "103"


def _config_file(config_dir: Path) -> Path:
    return config_dir / "proxmox.json"


def load_proxmox_config(config_dir: Path) -> ProxmoxConfig:
    f = _config_file(config_dir)
    if not f.exists():
        return ProxmoxConfig()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        fields = ProxmoxConfig.__dataclass_fields__
        return ProxmoxConfig(**{k: v for k, v in data.items() if k in fields})
    except Exception:
        return ProxmoxConfig()


def save_proxmox_config(config_dir: Path, cfg: ProxmoxConfig) -> None:
    f = _config_file(config_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    f.chmod(0o600)


def ensure_ssh_key() -> str:
    """Generate ed25519 key pair if not present; return the public key."""
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _KEY_PATH.exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(_KEY_PATH)],
            capture_output=True,
        )
    pub = _KEY_PATH.with_suffix(".pub")
    return pub.read_text().strip() if pub.exists() else ""


def _ssh_args(cfg: ProxmoxConfig) -> list[str]:
    return [
        "ssh",
        "-i", str(_KEY_PATH),
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-p", str(cfg.port),
        f"{cfg.user}@{cfg.host}",
    ]


def run_remote(cfg: ProxmoxConfig, command: str, timeout: int = 60) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            _ssh_args(cfg) + [command],
            capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


def _next_mp_index(cfg: ProxmoxConfig) -> tuple[bool, int]:
    ok, out = run_remote(cfg, f"pct config {cfg.lxc_id}")
    if not ok:
        return False, 0
    used: set[int] = set()
    for line in out.splitlines():
        if line.startswith("mp"):
            try:
                used.add(int(line.split(":")[0][2:]))
            except (ValueError, IndexError):
                pass
    for i in range(256):
        if i not in used:
            return True, i
    return False, 0


def _host_mountpoint(nfs_host: str, share: str) -> str:
    safe_share = share.strip("/").replace("/", "_")
    safe_host = nfs_host.replace(".", "_")
    return f"/mnt/namer-helper/{safe_host}_{safe_share}"


def setup_host_mount(
    cfg: ProxmoxConfig,
    nfs_host: str,
    share: str,
    target: str,
) -> list[tuple[str, bool, str]]:
    """
    Mount NFS on the Proxmox host and bind-mount into the LXC container.
    Returns list of (description, ok, output) for each step.
    """
    host_mp = _host_mountpoint(nfs_host, share)
    steps: list[tuple[str, bool, str]] = []

    ok, out = run_remote(cfg, "apt-get install -y nfs-common 2>&1 | tail -5")
    steps.append(("nfs-common installieren", ok, out))

    ok, out = run_remote(cfg, f"mkdir -p {host_mp}")
    steps.append((f"Mountpunkt erstellen ({host_mp})", ok, out))

    # Skip if already mounted
    _, mount_check = run_remote(cfg, f"mountpoint -q {host_mp} && echo mounted")
    if "mounted" in mount_check:
        steps.append((f"NFS mounten ({nfs_host}:{share})", True, "Bereits gemountet"))
    else:
        ok, out = run_remote(cfg, f"mount -t nfs {nfs_host}:{share} {host_mp}")
        steps.append((f"NFS mounten ({nfs_host}:{share})", ok, out))
        if not ok:
            return steps

    mp_ok, mp_idx = _next_mp_index(cfg)
    if not mp_ok:
        steps.append(("LXC-Konfiguration lesen", False, "mp-Index nicht ermittelbar"))
        return steps

    ok, out = run_remote(cfg, f"pct set {cfg.lxc_id} -mp{mp_idx} {host_mp},mp={target}")
    steps.append((f"LXC Bind-Mount eintragen (mp{mp_idx})", ok, out))

    fstab_line = f"{nfs_host}:{share} {host_mp} nfs defaults,_netdev 0 0"
    ok, out = run_remote(
        cfg,
        f"grep -qxF '{fstab_line}' /etc/fstab || echo '{fstab_line}' >> /etc/fstab",
    )
    steps.append(("fstab-Eintrag hinzufügen", ok, out))

    return steps


def teardown_host_mount(
    cfg: ProxmoxConfig,
    nfs_host: str,
    share: str,
    target: str,
) -> list[tuple[str, bool, str]]:
    """Remove bind mount from LXC config, unmount NFS on host, clean fstab."""
    host_mp = _host_mountpoint(nfs_host, share)
    steps: list[tuple[str, bool, str]] = []

    # Find and remove the mp entry from LXC config
    ok, out = run_remote(
        cfg,
        f"pct config {cfg.lxc_id} | grep -n 'mp=' | grep '{host_mp}' | head -1",
    )
    if ok and out:
        mp_key = out.strip().split(":")[1].split(":")[0].strip() if ":" in out else ""
        if mp_key.startswith("mp"):
            ok2, out2 = run_remote(cfg, f"pct set {cfg.lxc_id} --delete {mp_key}")
            steps.append((f"LXC Bind-Mount entfernen ({mp_key})", ok2, out2))
        else:
            steps.append(("LXC Bind-Mount entfernen", False, "mp-Eintrag nicht gefunden"))
    else:
        steps.append(("LXC Bind-Mount entfernen", True, "Kein Eintrag gefunden (bereits entfernt)"))

    ok, out = run_remote(cfg, f"umount {host_mp} 2>&1 || true")
    steps.append((f"NFS unmounten ({host_mp})", True, out))

    fstab_line = f"{nfs_host}:{share} {host_mp} nfs defaults,_netdev 0 0"
    ok, out = run_remote(
        cfg,
        f"sed -i '\\|{fstab_line}|d' /etc/fstab",
    )
    steps.append(("fstab-Eintrag entfernen", ok, out))

    return steps
