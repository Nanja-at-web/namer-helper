"""
AI service configuration (StashDB API key, Ollama URL/model).

Stored in /etc/namer-helper/ai_config.json — root-readable only.
Never committed to the repository.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


_CONFIG_FILE = "ai_config.json"


@dataclass
class AIConfig:
    stashdb_api_key: str = ""
    theporndb_api_key: str = ""
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    pre_check_dir: str = "/var/lib/namer/pre-check"
    # Optional: where aussortierte Dateien landen. Leer = neben pre-check/failed
    # (kleine LXC-Disk). Auf eine NAS zeigen lassen, damit der Container nicht
    # vollläuft, z.B. /mnt/nas/aussortiert.
    aussortiert_dir: str = ""
    # Lokale StashApp (für Wortschatz-Import "stash" und stash-search)
    stash_url: str = "http://localhost:9999"
    stash_api_key: str = ""


def load_ai_config(config_dir: Path) -> AIConfig:
    path = config_dir / _CONFIG_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AIConfig(
            stashdb_api_key=data.get("stashdb_api_key", ""),
            theporndb_api_key=data.get("theporndb_api_key", ""),
            ollama_url=data.get("ollama_url", "http://localhost:11434"),
            ollama_model=data.get("ollama_model", "llama3"),
            pre_check_dir=data.get("pre_check_dir", "/var/lib/namer/pre-check"),
            aussortiert_dir=data.get("aussortiert_dir", ""),
            stash_url=data.get("stash_url", "http://localhost:9999"),
            stash_api_key=data.get("stash_api_key", ""),
        )
    except Exception:
        return AIConfig()


def save_ai_config(config_dir: Path, cfg: AIConfig) -> None:
    path = config_dir / _CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    path.chmod(0o600)
