# Ollama Setup

Ollama ist in namer-helper **optional** und dient ausschließlich als Vorschlags-Engine.
Es trifft keine automatischen Entscheidungen und benennt keine Dateien um.

---

## Empfohlene Variante: Ollama als Proxmox-LXC

Die einfachste und wartungsärmste Variante für Homelab-Setups ist ein dedizierter
Ollama-LXC auf dem Proxmox-Host — bereitgestellt vom community-scripts-Projekt.

### LXC installieren

Im Proxmox-Host-Terminal:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/ollama.sh)"
```

Ressourcen des LXC (Standardwerte):

```text
CPU:   4 Kerne
RAM:   4 GB
Disk:  40 GB
OS:    Ubuntu 24.04
Port:  11434
GPU:   optional (wird automatisch erkannt)
```

### Modell laden

Nach der Installation im Ollama-LXC:

```bash
ollama pull llama3       # ~4 GB, empfohlen
ollama pull mistral      # ~4 GB, schnell
ollama pull gemma2       # ~5 GB, kompakt
```

### namer-helper konfigurieren

In `config/helper.yaml` die IP des Ollama-LXC eintragen:

```yaml
ollama:
  base_url: http://192.168.1.50:11434   # ← IP des Ollama-LXC
  model: llama3
  enabled: true
```

---

## Alternativen

### Docker

```yaml
# docker-compose.yml
services:
  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ./ollama:/root/.ollama
```

```yaml
# config/helper.yaml
ollama:
  base_url: http://ollama:11434
  model: llama3
  enabled: true
```

### Lokal (direkt installiert)

```bash
# Installation: https://ollama.com/download
ollama serve   # läuft auf localhost:11434
```

```yaml
ollama:
  base_url: http://localhost:11434
  enabled: true
```

---

## Erreichbarkeit prüfen

```bash
curl http://<ollama-ip>:11434/api/tags
```

Oder über namer-helper:

```bash
namer-helper analyze "Studio.Title.1080p.x264.mp4" \
  --ollama-url http://192.168.1.50:11434 \
  --model llama3
```

---

## Proxmox-Netzwerk-Hinweise

- Ollama-LXC und Namer-LXC sollten im selben Proxmox-Bridge-Netzwerk liegen
- Ollama lauscht standardmäßig auf `0.0.0.0:11434` — nicht ins Internet freigeben
- Für GPU-Unterstützung muss der Proxmox-Host eine kompatible GPU haben
  und der LXC als privilegierter Container laufen (oder GPU-Passthrough konfiguriert sein)

---

## Wie Ollama in namer-helper verwendet wird

```text
Dateiname
   │
   ▼
_clean_filename()          ← technische Tags entfernen (1080p, x264, WEBDL …)
   │
   ▼
Prompt aufbauen            ← strukturierter JSON-Prompt
   │
   ▼
Ollama /api/generate       ← lokaler LXC, kein externer Dienst
   │
   ▼
OllamaResult
   ├── cleaned_name
   ├── search_queries       ← 2-3 Suchvarianten
   ├── confidence           ← 0.0 – 1.0
   ├── recommended_action   ← auto_rename | manual_review | skip
   └── reason
```

**Ollama schlägt nur vor. namer-helper handelt nicht automatisch.**

---

## Confidence-Schwellenwerte

| Confidence | Empfehlung | Bedeutung |
|---|---|---|
| >= 0.85 | `auto_rename` | Titel eindeutig erkannt |
| 0.50 – 0.84 | `manual_review` | unsicher, manuelle Prüfung |
| < 0.50 | `skip` | Dateiname zu unklar |

Diese Schwellenwerte werden vom Modell selbst vorgeschlagen und können
in zukünftigen Versionen über `config/rules.yaml` überschrieben werden.
