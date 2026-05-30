# namer-helper

Sidecar-Helper für [Namer](https://github.com/ThePornDatabase/namer) — analysiert fehlgeschlagene Treffer, fragt StashApp ab und nutzt Ollama für lokale KI-Vorschläge.

**Namer selbst wird nicht verändert.** Alle Funktionen laufen außen herum.

---

## Überblick

```text
Dateien / Medienordner
        │
        ▼
  Namer Core                 ← unangetastet, läuft wie gewohnt
  (Rename, Watchdog, WebUI)
        │
        ▼ fehlgeschlagene Treffer
  namer-helper
        │
        ├── report           MVP 1 — Log auslesen, Report erzeugen
        ├── stash-search     MVP 3 — StashApp nach Szenen durchsuchen
        └── analyze          MVP 2 — Ollama: Suchvorschläge generieren
```

---

## Installation

### Voraussetzung

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (empfohlen) oder pip

### Lokal

```bash
git clone https://github.com/Nanja-at-web/namer-helper.git
cd namer-helper
uv venv .venv --python 3.11
uv pip install -e . --python .venv/bin/python
```

### Als Proxmox-LXC / Docker

Siehe [docs/ollama-setup.md](docs/ollama-setup.md) und [docs/stash-bridge.md](docs/stash-bridge.md).

---

## Befehle

### `report` — Fehlgeschlagene Namer-Treffer auswerten

Liest das `failed_dir` aus `namer.cfg` und erzeugt einen Markdown- und JSON-Report.

```bash
namer-helper report \
  --namer-config /etc/namer/namer.cfg \
  --output-dir /var/lib/namer-helper/reports \
  --format both
```

| Option | Standard | Bedeutung |
|---|---|---|
| `--namer-config` | `/etc/namer/namer.cfg` | Pfad zur Namer-Konfiguration |
| `--failed-dir` | — | Direkter Pfad (überschreibt namer.cfg) |
| `--output-dir` | `/var/lib/namer-helper/reports` | Ausgabeverzeichnis |
| `--format` | `both` | `markdown`, `json` oder `both` |

---

### `stash-search` — Szenen in StashApp suchen

Sucht Dateinamen in einer laufenden StashApp-Instanz. Zuerst per Pfad-Match, dann per Titel-Suche.

```bash
namer-helper stash-search \
  "Studio.Title.1080p.x264.mp4" \
  --stash-url http://192.168.1.123:9999
```

Alle fehlgeschlagenen Namer-Treffer auf einmal:

```bash
namer-helper stash-search \
  --failed-dir /var/lib/namer/failed \
  --stash-url http://192.168.1.123:9999
```

| Option | Standard | Bedeutung |
|---|---|---|
| `--stash-url` | `http://localhost:9999` | StashApp-URL |
| `--api-key` | — | API-Key falls in StashApp gesetzt |
| `--failed-dir` | — | Verzeichnis mit fehlgeschlagenen Treffern |
| `--namer-config` | `/etc/namer/namer.cfg` | Alternativ: Pfad aus namer.cfg lesen |

Confidence-Werte:

| Match-Typ | Confidence |
|---|---:|
| Pfad-Match | 0.90 |
| Titel-Suche | 0.65 |

---

### `analyze` — Dateinamen via Ollama analysieren

Bereinigt den Dateinamen und lässt Ollama Suchvorschläge generieren.
**Ollama trifft keine Entscheidungen — es schlägt nur vor.**

```bash
namer-helper analyze \
  "Studio.Title.1080p.x264.mp4" \
  --ollama-url http://192.168.1.50:11434 \
  --model llama3
```

Alle fehlgeschlagenen Treffer analysieren:

```bash
namer-helper analyze \
  --failed-dir /var/lib/namer/failed \
  --ollama-url http://192.168.1.50:11434
```

| Option | Standard | Bedeutung |
|---|---|---|
| `--ollama-url` | `http://localhost:11434` | Ollama-Server URL |
| `--model` | `llama3` | Ollama-Modell |
| `--timeout` | `30` | Timeout in Sekunden |

Confidence-Schwellenwerte:

| Confidence | Empfehlung |
|---|---|
| >= 0.85 | `auto_rename` |
| 0.50 – 0.84 | `manual_review` |
| < 0.50 | `skip` |

---

## Konfiguration

Alle Optionen in `config/helper.yaml`:

```yaml
namer:
  config_path: /etc/namer/namer.cfg

report:
  output_dir: /var/lib/namer-helper/reports
  format: both

stash:
  url: http://192.168.1.123:9999
  api_key: ""
  timeout: 15
  enabled: false

ollama:
  base_url: http://192.168.1.50:11434
  model: llama3
  timeout: 30
  enabled: false

privacy:
  mode: local-only   # local-only | query-external | contribute
```

---

## Homelab-Setup (Proxmox)

Empfohlene Aufteilung als separate LXC-Container:

```text
Proxmox Host
  ├── LXC: Namer          Port 6980   github.com/ThePornDatabase/namer
  ├── LXC: StashApp       Port 9999   github.com/stashapp/stash
  └── LXC: Ollama         Port 11434  community-scripts/ProxmoxVE ollama.sh
```

namer-helper läuft als Skript oder Dienst auf dem Namer-LXC oder einem eigenen LXC.

### Ollama-LXC installieren

```bash
# Auf dem Proxmox-Host:
bash -c "$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/ollama.sh)"

# Im Ollama-LXC:
ollama pull llama3
```

Weitere Details: [docs/ollama-setup.md](docs/ollama-setup.md)

### StashApp einbinden

Weitere Details: [docs/stash-bridge.md](docs/stash-bridge.md)

---

## Empfohlener Workflow

```text
1. Namer läuft normal (Watchdog)
        │
        ▼
2. namer-helper report
   → Welche Dateien wurden nicht erkannt?
        │
        ▼
3. namer-helper stash-search
   → Hat StashApp die Szene bereits?
        │
        ├── Treffer → Metadaten übernehmen
        │
        └── kein Treffer
                │
                ▼
            namer-helper analyze
            → Ollama: Suchvorschläge
                │
                ▼
            Manuelle Prüfung
```

---

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

50 Tests in 9 Dateien (log_parser, renderer, stash_matcher, ollama_analyzer,
precheck_identification, theporndb_context, scan_status, metadata_cache, file_filtering).

---

## Architektur

Dieses Projekt ist Teil einer modularen Erweiterungsstrategie für Namer.
Der Namer-Core bleibt vollständig unangetastet und updatebar.

Hintergründe: [Nanja-at-web/namer — docs/core/Core.md](https://github.com/Nanja-at-web/namer/blob/docs/core-strategy/docs/core/Core.md)

---

## Lizenz

MIT
