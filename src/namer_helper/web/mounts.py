"""
Mount management: save/load configs, mount/unmount network shares.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

NAMER_DIRS = {
    "watch":  "/var/lib/namer/watch",
    "work":   "/var/lib/namer/work",
    "failed": "/var/lib/namer/failed",
    "dest":   "/var/lib/namer/dest",
}


@dataclass
class MountConfig:
    id: str
    protocol: str       # "smb" | "nfs"
    host: str
    share: str          # SMB: share name  /  NFS: export path
    target: str         # local mount point
    username: str = ""
    password: str = field(default="", repr=False)
    label: str = ""     # optional human label


def _config_file(config_dir: Path) -> Path:
    return config_dir / "mounts.json"


def load_mounts(config_dir: Path) -> list[MountConfig]:
    f = _config_file(config_dir)
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return [MountConfig(**m) for m in data]
    except Exception:
        return []


def save_mounts(config_dir: Path, mounts: list[MountConfig]) -> None:
    f = _config_file(config_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps([asdict(m) for m in mounts], indent=2), encoding="utf-8")
    f.chmod(0o600)  # credentials — only root readable


def is_mounted(target: str) -> bool:
    try:
        mounts = Path("/proc/mounts").read_text()
        return any(
            line.split()[1] == target
            for line in mounts.splitlines()
            if len(line.split()) >= 2
        )
    except Exception:
        return False


def do_mount(config: MountConfig) -> tuple[bool, str]:
    Path(config.target).mkdir(parents=True, exist_ok=True)

    if config.protocol == "nfs":
        cmd = [
            "mount", "-t", "nfs",
            f"{config.host}:{config.share}",
            config.target,
        ]
    else:
        opts = (
            f"username={config.username},"
            f"password={config.password},"
            "uid=0,gid=0,file_mode=0755,dir_mode=0755"
        )
        cmd = [
            "mount", "-t", "cifs",
            f"//{config.host}/{config.share}",
            config.target,
            "-o", opts,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return True, ""
    return False, (result.stderr or result.stdout).strip()


def do_unmount(target: str) -> tuple[bool, str]:
    result = subprocess.run(["umount", target], capture_output=True, text=True)
    if result.returncode == 0:
        return True, ""
    return False, (result.stderr or result.stdout).strip()
