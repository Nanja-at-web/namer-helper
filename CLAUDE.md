# namer-helper – PROJECT MEMORY

<!-- Für Claude Code, ChatGPT Codex und andere KI-Assistenten -->

<!-- Format: [E]=Etabliert/Implementiert [I]=Geplant | [H]=Hoch [M]=Mittel [L]=Niedrig | [C]=Claude -->

-----

## Projekt-Kontext [E][H]

**Repository:** <https://github.com/Nanja-at-web/namer-helper>
**Version:** 0.1.0
**Zweck:** Sidecar-Helper für [Namer (ThePornDatabase)](https://github.com/ThePornDatabase/namer)

- Analysiert fehlgeschlagene Namer-Treffer (`failed/` Ordner)
- Fragt lokale StashApp-Instanz ab
- Nutzt Ollama für lokale KI-Vorschläge
- Bietet Web-Dashboard zur Verwaltung
- **Namer selbst wird NICHT verändert** – alles läuft außen herum

**Stack:** Python 3.11+, uv/hatchling, Click, FastAPI, Uvicorn, Loguru, Jinja2, Requests
**Deployment:** Proxmox LXC oder Docker (community-scripts.org Muster)
**Lizenz:** AGPL-3.0
**Default Port:** 6981 (Web-Dashboard)

-----

## Tatsächliche Dateistruktur (vollständig verifiziert) [E][H]

```
namer-helper/
├── src/namer_helper/
│   ├── __init__.py                    # version = "0.1.0"
│   ├── cli.py                         # CLI: serve, report, analyze, stash-search
│   ├── namer_bridge/
│   │   ├── __init__.py
│   │   ├── config_reader.py           # liest namer.cfg [watchdog]-Sektion
│   │   └── log_parser.py              # parst .namer_failed.log → FailedMatch
│   ├── ollama_bridge/
│   │   ├── __init__.py
│   │   ├── client.py                  # HTTP-Client /api/generate, stream=False
│   │   └── analyzer.py                # _STRIP_RE + Prompt → OllamaResult
│   ├── stash_bridge/
│   │   ├── __init__.py
│   │   ├── client.py                  # GraphQL-Client StashApp
│   │   └── matcher.py                 # Pfad-Match (0.90) + Titel-Suche (0.65)
│   ├── reports/
│   │   ├── __init__.py
│   │   └── renderer.py                # Markdown + JSON, Anonymisierung (sha256)
│   └── web/
│       ├── __init__.py
│       ├── app.py                     # FastAPI Factory, Port 6981
│       ├── mounts.py                  # SMB/NFS: MountConfig, /proc/mounts
│       └── templates/
│           ├── base.html              # Tailwind CDN, Nav: Dashboard/Watch/Failed/Dest/Mounts/LiveLog
│           ├── dashboard.html         # Service-Status, Ordner-Stats, Reports
│           ├── failed.html            # Tabelle: Retry → watch/, Delete, Log-Link
│           ├── files.html             # Generischer Datei-Browser
│           ├── log.html               # Live-Log SSE-Stream (MAX 500 Zeilen)
│           ├── logfile.html           # .namer_failed.log Inhalt anzeigen
│           └── mounts.html            # SMB/NFS konfigurieren, mounten/unmounten
├── tests/
│   └── test_log_parser.py             # 5 Unit-Tests (parse_score, no_score, date, empty, sorted)
├── docs/
│   ├── ollama-setup.md                # Proxmox-LXC Ollama, community-scripts
│   └── stash-bridge.md                # StashApp GraphQL, Confidence-Werte
├── config/
│   ├── helper.yaml                    # ✓ VORHANDEN – Haupt-Konfiguration
│   └── namer/
│       └── namer.cfg.example          # Namer-Beispielkonfig (porndb_token, Pfade)
├── docker/
│   └── entrypoint.sh                  # Daemon-Modus + WebUI-Modus
├── docker-compose.yml                 # ✓ VORHANDEN – Full-Stack (namer+helper+ollama)
├── Dockerfile                         # python:3.11-slim, pip install .
└── pyproject.toml                     # hatchling, click/fastapi/loguru/requests/jinja2
```

**Bestätigte FEHLENDE Dateien:**

```
config/rules.yaml           # MVP 4: Rule Learning – noch nicht erstellt
tests/test_analyzer.py      # fehlt (README behauptet "33 Tests" – aspirational)
tests/test_stash_client.py  # fehlt
docker/docker-compose.yml   # docker-compose.yml ist im ROOT, nicht in docker/
```

-----

## Implementierte Befehle (aktueller Stand) [E][H]

```bash
namer-helper serve          # FastAPI Dashboard auf Port 6981
namer-helper report         # fehlgeschlagene Treffer → Markdown/JSON Report
namer-helper analyze        # Dateinamen via Ollama analysieren
namer-helper stash-search   # Dateinamen in lokaler StashApp suchen
```

### serve

- FastAPI + Uvicorn, Port 6981
- Dashboard: Namer-Service-Status, Ordner-Statistiken, Reports
- Service-Control: `systemctl start/stop/restart namer-watchdog`
- Datei-Browser: watch/, work/, failed/, dest/
- Failed-Management: Retry (→ watch/), Delete, Log anzeigen
- Live-Log: SSE-Stream von `journalctl -u namer-watchdog -f`
- Mount-Manager: SMB/NFS Shares konfigurieren, mounten/unmounten

### report

- Liest `failed_dir` aus `namer.cfg` (Sektion `[watchdog]`)
- Parst alle `.namer_failed.log` Dateien → FailedMatch (score, site_hint, date_hint)
- Ausgabe: Markdown-Tabelle + JSON, optional anonymisiert (`--anonymize`)

### analyze

- Bereinigt Dateiname mit `_STRIP_RE` (entfernt 720p, x264, bluray etc.)
- Baut strukturierten JSON-Prompt
- Ruft Ollama `/api/generate` auf (Default-Modell: **llama3**)
- Gibt OllamaResult zurück: cleaned_name, search_queries, confidence, recommended_action, reason
- **Kein automatisches Umbenennen** – nur Vorschläge

### stash-search

- Zwei-Stufen-Suche in StashApp:
1. Pfad-Match via `INCLUDES` (confidence: 0.90)
1. Titel-Suche mit bereinigtem Dateinamen (confidence: 0.65)
- Gibt StashScene zurück: id, title, date, studio, performers

-----

## Konfiguration (aktueller Stand) [E][H]

### config/helper.yaml (✓ VORHANDEN)

```yaml
namer:
  config_path: /etc/namer/namer.cfg
  failed_dir: ""       # leer = aus namer.cfg lesen
  log_dir: ""

report:
  output_dir: /var/lib/namer-helper/reports
  format: both

stash:
  url: http://localhost:9999
  api_key: ""
  timeout: 15
  enabled: false        # ← explizit aktivieren nötig

ollama:
  base_url: http://localhost:11434
  model: llama3         # aktuell llama3, empfohlen: qwen2.5:3b
  timeout: 30
  enabled: false        # ← explizit aktivieren nötig

privacy:
  mode: local-only      # local-only | query-external | contribute
```

**Lokale Overrides:** `config/helper/helper.local.yaml` (gitignored) für echte API-Keys.

### Docker-Betrieb [E][H]

**Zwei Modi via `HELPER_MODE` Env-Variable:**

```bash
# Modus 1: report-daemon (Standard)
# → Erstellt Report alle REPORT_INTERVAL Sekunden (Default: 3600)
docker compose up -d

# Modus 2: webui
# → Startet FastAPI Dashboard auf Port 6981
HELPER_MODE=webui docker compose up -d

# Direkter CLI-Aufruf im laufenden Container:
docker exec namer-helper namer-helper analyze "Studio.File.1080p.mp4"
```

**Services in docker-compose.yml:**

```
namer          Port 6980   ghcr.io/theporndatabase/namer:latest
namer-helper   Port 6981   lokal gebaut (Dockerfile)
ollama         Port 11434  ollama/ollama:latest  → opt-in via --profile ollama
```

**Shared Volumes:**

```
namer_failed   ← von namer geschrieben, von namer-helper gelesen
namer_watch    ← Retry: namer-helper verschiebt Dateien zurück hierhin
```

### namer.cfg (gelesen, nicht geschrieben)

Liest aus `[watchdog]`-Sektion:

```ini
[watchdog]
failed_dir = /failed
work_dir   = /work
watch_dir  = /watch
dest_dir   = /dest
```

Defaults wenn nicht vorhanden: `/var/lib/namer/{failed,work,watch,dest}`

-----

## Was aktuell FEHLT (Lücken für Phase 1+) [E][H]

```
TPDB-Integration fehlt komplett
  → kein tpdb_bridge/ Modul, kein /scenes /movies /jav Endpoint

Normalisierungs-Modul fehlt
  → analyzer.py hat _STRIP_RE (Basis), aber kein Leet/Noise/Unicode-Handling

Alias-Wörterbuch fehlt
  → EA/BRZ etc. nicht auflösbar, kein data/aliases.json

Log-Fehlerklassen fehlen
  → log_parser.py parst Score/Site/Datum, klassifiziert NICHT (kein FailureReason)

JAV-Erkennung fehlt
  → ABC-123 Codes werden nicht erkannt

StashDB-Fingerprint fehlt
  → nur lokale StashApp, kein stashdb.org Hash-Matching

Performer-Resolve fehlt
  → kein TPDB /performers Lookup

Kandidaten-Ranking fehlt
  → kein LLM-basiertes Sortieren von API-Ergebnissen

Pre-Check fehlt (MVP 5)
  → keine Vorprüfung vor Namer-Aufruf

Nachprüfung fehlt (MVP 6)
  → keine Validierung nach Namer-Aufruf

Review-Queue fehlt (MVP 7)
  → keine manuelle Entscheidungsschnittstelle

Rule Learning fehlt (MVP 4)
  → config/rules.yaml nicht vorhanden

Tests unvollständig
  → test_log_parser.py vorhanden (5 Tests), alle anderen fehlen
  → README erwähnt "33 Tests" – aspirational, nicht aktuell
```

-----

## Externe APIs [E][H]

### TPDB (ThePornDatabase) – NOCH NICHT INTEGRIERT

- **Base URL:** `https://api.theporndb.net`
- **Auth:** `Authorization: Bearer {API_KEY}`
- **Endpunkte:**
  - `GET /scenes`     → Einzelszenen
  - `GET /movies`     → Vollfilme
  - `GET /jav`        → JAV (Code-basiert, z.B. ABP-123)
  - `GET /performers` → Performer-Profile inkl. Aliases
  - `GET /sites`      → Studio/Site-Informationen
- **Felder je Szene:** `id`, `title`, `date`, `site.name`, `performers[].name`, `tags[].name`, `description`, `duration`
- **Matching:** Text-Suche + pHash-Fingerprint

### StashDB (cloud) – NOCH NICHT INTEGRIERT

- **URL:** `https://stashdb.org/graphql`
- **Auth:** Header `ApiKey: {API_KEY}`
- **API-Typ:** GraphQL
- **Primäres Matching:** Fingerprints (OSHASH, pHash, MD5)
- **Entitäten:** Scenes, Performers, Studios, Tags, Galleries

### StashApp (lokal) – IMPLEMENTIERT

- **URL:** `http://localhost:9999/graphql` (konfigurierbar)
- **Auth:** optional API Key
- **Matching:** Pfad-INCLUDES + Titel-Suche
- **Confidence:** 0.90 (Pfad), 0.65 (Titel)

### Ollama (lokal) – IMPLEMENTIERT

- **URL:** `http://localhost:11434`
- **Aktuelles Modell:** `llama3`
- **Empfohlene Modelle:**
  - `qwen2.5:3b` – besser für JSON-Output, domänenspezifischer
  - `qwen2.5:1.5b` – Batch-Verarbeitung (schneller)
  - `nomic-embed-text` – für spätere Embedding-Suche

-----

## Architektur-Grundprinzip (aus Core.md) [E][H]

> Quelle: `Nanja-at-web/namer` Branch `docs/core-strategy/docs/core/Core.md`

**Kernregel:**

```
Core bleibt unangetastet.
Eigene Features werden außen herum gebaut.
Wenn ein Feature auch außerhalb von Namer gebaut werden kann,
dann wird es außerhalb gebaut.
```

**Entscheidung 2026-05-23:** Option C – Core bleibt vollständig unangetastet.
Kein eigener Commit auf `namer/main`. Alle Erweiterungen leben im Helper.

### 14 Leitregeln (unveränderlich) [E][H]

```
1.  Core bleibt unangetastet.
2.  Eigene Features werden außen herum gebaut.
3.  Keine direkten Core-Patches.
4.  Deterministische Quellen vor KI.
5.  Hash/Fingerprint schlägt Dateiname.
6.  Ollama schlägt nur vor.
7.  Externe Quellen sind optional.
8.  Datenschutz-Modi müssen klar sein.
9.  Jede automatische Aktion braucht Confidence-Regeln.
10. Updates von Upstream müssen einfach bleiben.
```

-----

## Branch: backup/precheck-ai-proxmox-20260529 [E][H]

> **Stand: 2026-05-30** – Aktiver Entwicklungsbranch, noch nicht in `main` gemergt.
> Enthält die vollständige Pre-Check-Implementierung + TPDB + StashDB (cloud) + Proxmox-Integration.

-----

## Neue Dateistruktur im precheck-Branch [E][H]

```
namer-helper/ (branch: backup/precheck-ai-proxmox-20260529)
├── src/namer_helper/
│   ├── namer_bridge/
│   │   ├── config_reader.py       # + read_namer_porndb_token() NEU
│   │   ├── hasher.py              # NEU: oshash, phash, video_info, OCR, logo
│   │   └── filename_parser.py     # NEU: deterministischer Parser (FilenameInfo)
│   ├── stash_bridge/
│   │   ├── stashdb.py             # NEU: StashDB cloud (fingerprint+context+submit)
│   │   └── theporndb.py           # NEU: TPDB REST+GraphQL (Scenes + Movies)
│   └── web/
│       ├── app.py                 # massiv erweitert
│       ├── ai_config.py           # NEU: AIConfig, /etc/namer-helper/ai_config.json
│       ├── identification.py      # NEU: Multi-Signal Confidence-Aggregation
│       ├── proxmox.py             # NEU: SSH-Integration, LXC-Management
│       ├── metadata_cache.py      # NEU: Video-Metadaten-Cache
│       ├── lookup_cache.py        # NEU: Lookup-Cache (by oshash)
│       ├── scan_status.py         # NEU: Persistenter Scan-Status
│       └── templates/
│           ├── base.html          # + Pre-Check, Proxmox, → Einstellungen in Nav
│           ├── dashboard.html     # + pre-check/ Tile, work/-Alert
│           ├── pre-check.html     # NEU
│           ├── settings.html      # NEU
│           └── proxmox.html       # NEU
```

-----

## Vorprüfung & Nachprüfung (Pre-Check) – IMPLEMENTIERT [E][H]

> Pre-Check existiert im Branch `backup/precheck-ai-proxmox-20260529`.
> Noch nicht in `main` gemergt.

### Was Pre-Check tut

```
Dateien landen in /var/lib/namer/pre-check/  (BEVOR sie zu Namer gehen)
    ↓
    ↓
Dashboard: Dateiliste mit Größe + Dauer (lazy via /pre-check/duration)
    ↓
    ↓ Benutzer klickt "Analyse" oder "Alle scannen"
    ↓
    ↓
POST /pre-check/lookup → 4-Phasen async Pipeline
    ↓
    ↓
build_identification() → status + confidence + suggested_name + action
    ↓
    ├── action="rename" → /pre-check/rename → Datei bleibt in pre-check/
    ├── action="review" → User prüft Kandidaten manuell
    └── /pre-check/send → Datei geht nach watch/ → Namer verarbeitet sie
```

### 4-Phasen Lookup-Pipeline (/pre-check/lookup)

```python
# Phase 1 (parallel):
oshash    → compute_oshash(video)     # OpenSubtitles-Hash (64KB-Chunks)
vinfo     → get_video_info(video)     # FFProbe: Dauer, Auflösung, MP4-Tags
                                      # (meta_title, meta_studio, meta_date,
                                      #  meta_performers, meta_copyright)

# Phase 2 (parallel):
phash           → compute_phash(video)
ocr_text        → extract_frame_text(video)     # OCR auf Frames
ollama_result   → analyze_filename(cleaned)     # LLM
dest_duplicate  → find_dest_duplicate(oshash)   # Duplikat in dest/?
logo_studio     → detect_studio_logo(video,     # Vision: moondream
                      ollama_url, model="moondream")

# Phase 3 (parallel):
StashDB.query_by_fingerprints(oshash, phash)
StashDB.search_by_performer(performers, studio, date, duration)
StashDB.search_by_context(title, performers, studio, date, duration)
TPDB.query_by_fingerprints(oshash, phash)
TPDB.query_movies_by_hashes(oshash, phash)

# Phase 4 (sequential):
TPDB.search_by_performer(...)           → alle bekannten Performer
TPDB.search_by_context(StashDB-Titel)  → StashDB-Treffer als Query
TPDB.search_by_context(Ollama-Query)   → LLM-Vorschläge
TPDB.search_by_context(OCR-Zeile)      → letzter Ausweg
Cross-Check: StashDB ↔ TPDB (Titel, Performer, Datum, Dauer)
→ build_identification() → finales Ergebnis
```

### Confidence-Modell (identification.py)

```
Status        Confidence  Quelle                       Aktion
──────────────────────────────────────────────────────────────
duplicate     1.00        oshash in dest/              skip
identified    0.97        StashDB Fingerprint          rename
identified    0.96        TPDB Fingerprint             rename
identified    0.96        TPDB Movie Fingerprint       rename
identified    0.90        StashDB + TPDB bestätigt     rename
identified    0.88-0.90   TPDB Score >= 80             rename
likely        0.76-0.78   TPDB Score 50-79             review
possible      0.35-0.55   TPDB Score < 50              review
possible      0.55        Nur Ollama conf >= 0.85      review
unknown       0.00        Kein Treffer                 review
──────────────────────────────────────────────────────────────
Dauer-Konflikt: > 35% Abweichung → confidence 0.32-0.35 unabhängig von Quelle
```

-----

## Neue Module (precheck-Branch) [E][H]

### namer_bridge/filename_parser.py

```python
parse_filename(name) → FilenameInfo
# FilenameInfo.cleaned, performers[], studio, date, resolution, tech_tags[], confidence
# Erkennt @studio #performer, " - " Segmente, Datum, technische Tags
# Confidence: 0.15 + 0.25(Datum) + 0.20(Studio) + 0.20(Performer) + 0.10(Tech)
```

### namer_bridge/hasher.py

```python
compute_oshash(path)                              → str | None
compute_phash(path, duration)                     → str | None
get_video_info(path)                              → dict  # FFProbe + MP4-Tags
extract_frame_text(path, duration)                → str   # OCR
detect_studio_logo(path, ollama_url, model)       → str | None  # moondream
```

### stash_bridge/stashdb.py (StashDB cloud)

```python
StashDBClient(api_key)
  .query_by_fingerprints(oshash, phash)    → StashDBResult
  .search_by_context(title, ...)           → StashDBResult
  .search_by_performer(performers, ...)    → StashDBResult
  .submit_fingerprint(scene_id, ...)       → dict  # Fingerprints zurückmelden
```

### stash_bridge/theporndb.py

```python
ThePornDBClient(api_key)
  .query_by_fingerprints(oshash, phash)   → ThePornDBResult    # Scenes
  .search_by_context(title, ...)          → ThePornDBResult
  .search_by_performer(performers, ...)   → ThePornDBResult
  .query_movies_by_hashes(oshash, phash)  → ThePornDBMovieResult
  .search_movies_by_context(title, ...)   → ThePornDBMovieResult
# Score-Breakdown: performer_match, title_overlap, date_match, duration_match
```

### web/ai_config.py

```python
@dataclass AIConfig:
    stashdb_api_key, theporndb_api_key, ollama_url, ollama_model, pre_check_dir
# Gespeichert: /etc/namer-helper/ai_config.json (chmod 0o600)
# NICHT in config/helper.yaml
```

### web/scan_status.py

```python
# Persistenter Status: /opt/namer-helper/scan-status/pre-check.json
# Stati: idle | running | pause_requested | paused | stopped | finished | error
# Batch-Scan: start(), mark_running(), mark_done(), finish(), pause(), resume(), stop()
```

### web/proxmox.py

```python
ProxmoxConfig(host, user="root", port=22, lxc_id="103")
ensure_ssh_key()              → str   # ~/.ssh/namer_helper_rsa (auto-erzeugt)
run_remote(cfg, cmd)          → tuple[bool, str]
setup_host_mount(cfg, ...)    → list[tuple[str, bool, str]]  # Schritte mit Status
# Config: /etc/namer-helper/proxmox_config.json
```

-----

## Neue Web-Routen (precheck-Branch) [E][H]

```
GET/POST /pre-check              → Dateiliste + UI
GET      /pre-check/duration     → Video-Dauer (lazy, gecacht)
POST     /pre-check/lookup       → 4-Phasen-Analyse (Haupt-Endpoint)
POST     /pre-check/scan/start   → Batch-Scan (Liste → asyncio.create_task)
POST     /pre-check/scan/pause   → Pausieren
POST     /pre-check/scan/resume  → Fortsetzen
POST     /pre-check/scan/stop    → Abbrechen
GET      /pre-check/scan/status  → Polling
GET      /pre-check/video        → Video servieren (für Player)
POST     /pre-check/stashdb-submit → Fingerprints an StashDB
POST     /pre-check/cache/invalidate → Lookup-Cache löschen
POST     /pre-check/rename       → Umbenennen in pre-check/
POST     /pre-check/send         → → watch/
POST     /pre-check/send-all     → Alle → watch/
POST     /pre-check/delete       → Löschen

GET/POST /settings               → AIConfig (Keys, Ollama, pre-check-dir)
GET/POST /proxmox                → SSH-Config + NFS-Mounts + LXC-Restart
POST     /proxmox/test           → SSH-Verbindungstest
POST     /proxmox/host-mount     → NFS-Mount auf Proxmox-Host
POST     /proxmox/restart        → LXC neu starten

POST     /failed/lookup          → AI-Lookup für failed/ (oshash + Ollama)
POST     /failed/rename          → Datei in failed/ umbenennen
POST     /failed/retry-all       → Alle failed/ → watch/
POST     /namer/retry-time       → retry_time in namer.cfg schreiben
POST     /work/clear             → work/ leeren (→ watch/)
```

-----

## MVP-Roadmap (aktualisiert) [E][H]

```
MVP 1: Failed-Match-Review     ✓ main + branch
MVP 2: Ollama Assist           ✓ main + branch (branch: erweitert mit Kontext)
MVP 3: Stash Bridge (lokal)    ✓ main + branch
MVP 4: Rule Learning           ✗ nicht implementiert (config/rules.yaml fehlt)
MVP 5: Pre-Check               ✓ branch (backup/precheck-ai-proxmox-20260529)
MVP 6: Post-Check              ✓ branch (als Teil der Pre-Check-Pipeline)
MVP 7: Review-Queue            ⚠️  branch (Pre-Check UI = manuelle Review, keine Queue)
MVP 8: StashDB cloud           ✓ branch (fingerprint + context + submit)
MVP 9: TPDB cloud              ✓ branch (Scenes + Movies, Score-Breakdown)
MVP 10: Proxmox-Integration    ✓ branch (SSH, LXC-Restart, Host-Mounts)
```

-----

## Namer-Interna (wichtig für Integration) [E][H]

- **Log-Suffix:** `.namer_failed.log` (neben der Videodatei)
- **Log-Format:** enthält `match score: X.XX`, `site: Name`, `date: YYYY-MM-DD`
- **Matching-Schwelle:** 95% RapidFuzz-Ähnlichkeit (Powerset)
- **Hash-Typen:** pHash (perceptual), OSHash (OpenSubtitles-kompatibel)
- **Ordner-Workflow:** `watch/` → `work/` → `dest/` oder `failed/`
- **Retry:** failed/-Dateien werden alle 24h erneut versucht
- **Service-Name:** `namer-watchdog` (systemctl)
- **Metadaten:** MP4-Tags (Plex/Apple TV), NFO-Dateien, Poster

-----

## ALLE GEPLANTEN MODULE – PRIORISIERT [I][H]

> Bewertung: Aufwand 1 (Stunden) – 5 (Wochen) | Gewinn 1 (marginal) – 5 (transformativ)

-----

### GRUPPE A – Sofort: Maximaler Gewinn, minimaler Aufwand [I][H]

|# |Modul                                               |Aufwand|Gewinn|Priorität|
|--|----------------------------------------------------|-------|------|---------|
|A1|Normalisierungs-Pipeline (Leet, #, Noise, Unicode)  |1      |5     |⭐⭐⭐⭐⭐    |
|A2|Studio-Alias-Wörterbuch (selbstlernend, JSON)       |1      |4     |⭐⭐⭐⭐⭐    |
|A3|Log-Analyser (Fehlerklassen aus `_namer.log`)       |1      |4     |⭐⭐⭐⭐⭐    |
|A4|Query-Normalisierung (LLM baut saubere Suchanfragen)|2      |5     |⭐⭐⭐⭐⭐    |
|A5|Batch-Verarbeitung (20 Dateien pro LLM-Call)        |1      |3     |⭐⭐⭐⭐     |

**Was Gruppe A löst:**

```
Dateiname mit Leet/Noise  → A1 normalisiert vor jedem API-Call
"EA" / "BRZ" unbekannt    → A2 löst sofort auf
Namer meldet Fehler       → A3 klassifiziert + schlägt Fix vor
TPDB liefert 0 Ergebnisse → A4 generiert bessere Queries
50 failed-Dateien         → A5 verarbeitet in 3 LLM-Calls statt 50
```

-----

### GRUPPE B – Kurzfristig: Hoher Gewinn, überschaubarer Aufwand [I][H]

|# |Modul                                                        |Aufwand|Gewinn|Priorität|
|--|-------------------------------------------------------------|-------|------|---------|
|B1|Performer-Resolve (Performer → Studio-Shortlist → Tag-Filter)|2      |5     |⭐⭐⭐⭐⭐    |
|B2|Kandidaten-Ranking (LLM bewertet TPDB-Ergebnisliste)         |2      |5     |⭐⭐⭐⭐⭐    |
|B3|Confidence-Schwellen (Auto / Vorschlag / Manual / Skip)      |1      |4     |⭐⭐⭐⭐     |
|B4|Beschreibungs-Embedding (nomic-embed-text + ChromaDB)        |2      |4     |⭐⭐⭐⭐     |
|B5|JAV-Code-Erkennung (Regex ABC-123 → TPDB /jav)               |1      |4     |⭐⭐⭐⭐     |
|B6|config/helper.yaml vervollständigen (TPDB, alle Schwellen)   |1      |3     |⭐⭐⭐⭐     |
|B7|Interaktiver Bestätigungsmodus (Web UI / CLI)                |2      |3     |⭐⭐⭐      |

**Was Gruppe B löst:**

```
Nur Performer im Dateinamen   → B1 findet Studio-Kontext
TPDB gibt 20 Treffer          → B2 wählt den richtigen
Falscher Auto-Rename          → B3 verhindert unsichere Matches
Beschreibung passt zum Namen  → B4 findet über Semantik
ABP-123 im Dateinamen         → B5 direkt zu TPDB /jav
Kein TPDB API-Key konfiguriert → B6 config/helper.yaml erweitern
Unsicherer Treffer            → B7 zeigt Vorschlag zur Bestätigung
```

-----

### GRUPPE C – Mittelfristig: Hoher dauerhafter Gewinn [I][M]

|# |Modul                                                          |Aufwand|Gewinn|Priorität|
|--|---------------------------------------------------------------|-------|------|---------|
|C1|Fine-Tuned Text-Modell (qwen2.5:1.5b auf TPDB-Daten)           |3      |5     |⭐⭐⭐⭐     |
|C2|Synthetischer Trainingsdaten-Generator (Obfuskations-Varianten)|2      |5     |⭐⭐⭐⭐     |
|C3|StashDB Fingerprint-Integration (OSHash + pHash)               |2      |5     |⭐⭐⭐⭐     |
|C4|TPDB Thumbnail-Datenbank (lokaler Index für CLIP)              |3      |3     |⭐⭐⭐      |
|C5|Performer-Alias-Datenbank (JD→Jane Doe, lokal + TPDB)          |2      |4     |⭐⭐⭐⭐     |
|C6|Multi-Source-Suche (TPDB Scenes + Movies + JAV parallel)       |2      |4     |⭐⭐⭐⭐     |

**Was Gruppe C löst:**

```
Abkürzungen / Leet dauerhaft   → C1 kennt alle Muster aus Training
Wenig Echtdaten für Training   → C2 generiert 500.000 Varianten
Datei ohne Namen, nur Hash     → C3 exakter Match ohne Text
Frames als Suchbasis           → C4 Vorbereitung für Frame-Analyse
"JD" oder "J.Doe" unbekannt    → C5 löst alle Schreibweisen auf
Film? Szene? JAV?              → C6 sucht alle Kategorien parallel
```

-----

### GRUPPE D – Langfristig: Komplex, aber transformativ [I][L]

|# |Modul                                                  |Aufwand|Gewinn|Priorität|
|--|-------------------------------------------------------|-------|------|---------|
|D1|Frame-Analyse via CLIP (Thumbnail-Matching)            |4      |4     |⭐⭐⭐      |
|D2|Studio-Watermark-Erkennung (MobileNetV3 fine-tuned)    |4      |4     |⭐⭐⭐      |
|D3|Vollautomatische TPDB-Trainingsdaten-Pipeline          |3      |5     |⭐⭐⭐⭐     |
|D4|Lokale Metadaten-Datenbank (SQLite FTS5, offline-fähig)|3      |4     |⭐⭐⭐      |
|D5|Whisper-Integration (Audiotranskription → Titel-Suche) |4      |3     |⭐⭐       |
|D6|VR-Content-Erkennung (360°-Marker, SBS-Auflösungen)    |3      |3     |⭐⭐       |

**Was Gruppe D löst:**

```
Datei ohne Namen + kein Hash    → D1 erkennt über Bildinhalt
Wasserzeichen im Frame          → D2 identifiziert Studio direkt
Training automatisch aktuell    → D3 TPDB-Änderungen fließen ein
Offline / kein API-Zugriff      → D4 vollständig lokal arbeitsfähig
Gesprochenes im Video           → D5 Titel aus Dialog erschließen
VR-Dateien falsch kategorisiert → D6 erkennt VR-spezifische Muster
```

-----

## Kritische Bewertung des Projekts [E][H]

### Was gut ist

- **Code-Qualität solide:** Keine unbehandelten Exceptions, sauberes async/await, Factory-Pattern, Fehler in Result-Objekten statt Exceptions. Professionell.
- **4-Phasen-Pipeline im Branch:** Konzept ist richtig – parallel, priorisiert, deterministisch vor KI.
- **identification.py:** Klares Confidence-Modell mit nachvollziehbaren Stufen. Dauer-Konflikt-Erkennung ist wertvoll.
- **filename_parser.py:** Deterministisch, keine externen Deps, gutes Signal für LLM-Input.
- **Keine automatischen Datei-Aktionen:** Leitprinzip wird eingehalten.

-----

### Was problematisch ist

**P1 – Branch-Name ist ein Warnsignal** `[KRITISCH]`

```
backup/precheck-ai-proxmox-20260529
```

`backup/` ist kein Feature-Branch. Kein PR, kein Merge-Kandidat. Die wertvollsten Features
– StashDB, TPDB, Pre-Check – sind in einem Snapshot eingefroren. main ist nahezu wertlos.

**P2 – main liefert kaum Mehrwert** `[KRITISCH]`

```
main heute: Regex-Bereinigung → llama3 → StashApp lokal → Report
```

Das macht Namer selbst teilweise schon. Der Mehrwert gegenüber Namer pur ist marginal.
Alles Wertvolle steckt im Branch der `backup/` heißt.

**P3 – README lügt** `[HOCH]`

```
"33 Tests, alle Komponenten abgedeckt."
Tatsächlich: 1 Testdatei, 5 Tests, nur log_parser.
```

Kein einziger Test für Analyzer, StashDB-Client, TPDB-Client, Pre-Check-Pipeline.

**P4 – Zwei Config-Systeme für dieselben Werte** `[HOCH]`

```
config/helper.yaml          → ollama.base_url, stash.url
/etc/namer-helper/ai_config.json → ollama_url, stashdb_api_key, theporndb_api_key
```

`ollama_url` steht in beiden. Wer gewinnt ist unklar. Jemand der das einrichtet
sucht an beiden Stellen und trägt Werte doppelt ein.

**P5 – pyproject.toml kennt seine eigenen Abhängigkeiten nicht** `[HOCH]`

```toml
# Was drin steht: loguru, pyyaml, click, requests, fastapi, uvicorn, jinja2
# Was der Branch tatsächlich braucht:
ffmpeg (binary)       → compute_phash, extract_frame_text, get_video_info
tesseract (binary)    → extract_frame_text (OCR)
ollama pull moondream → detect_studio_logo
```

Auf einem frischen System nicht lauffähig ohne Anleitung.

**P6 – Keine Normalisierung für Leet-Speak** `[HOCH]`
`filename_parser.py` entfernt technische Tags aber kein Leet-Speak, kein #-Handling.
`analyze_filename` bekommt `3v1l4ng3l` statt `evil angel`.
LLM-Qualität leidet direkt darunter – das ist das Kernproblem dieser Session.

**P7 – moondream für Logo-Erkennung fragwürdig** `[MITTEL]`
moondream (1.7B) ist für einfache Bildbeschreibungen ausgelegt, nicht für
Studio-Wasserzeichen in dunklen Video-Frames. Ein falscher Logo-Treffer
fließt als `logo_studio` in die TPDB-Suche ein und kann die Identifikation
in die falsche Richtung ziehen. Kein Konfidenz-Schwellenwert vorhanden.

**P8 – Single-Lookup ohne Timeout** `[MITTEL]`

```python
# Batch-Scan: Timeout vorhanden (600s)
result = await asyncio.wait_for(pre_check_lookup(name), timeout=600)
# Einzelner /pre-check/lookup: KEIN Timeout
# → Phase 1-4 + moondream + OCR + 5 DB-Queries können 2-5 Min dauern
# → Browser-Timeout möglich
```

**P9 – Proxmox-Integration gehört nicht in dieses Tool** `[MITTEL]`

```python
# proxmox.py: SSH-Keys, pct restart, NFS-Mount-Setup auf Proxmox-Host
```

Vollständiges Homelab-Management in einem Datei-Umbenenner. Jede Zeile
ist Wartungsaufwand der nichts zur eigentlichen Aufgabe beiträgt.

**P10 – Kein Fallback wenn ffmpeg/tesseract fehlt** `[MITTEL]`
Wenn `ffmpeg` nicht installiert ist, schlägt `compute_phash` still fehl.
Wenn `tesseract` fehlt, gibt `extract_frame_text` leer zurück.
Kein Warning, kein UI-Hinweis. Benutzer sieht einfach schlechtere Ergebnisse.

-----

### Problemranking

|#  |Problem            |Auswirkung           |Aufwand Fix|
|---|-------------------|---------------------|-----------|
|P1 |Branch nie gemergt |Nichts nutzbar       |Niedrig    |
|P2 |main zu schwach    |Kein Mehrwert        |→ P1 lösen |
|P3 |README lügt        |Vertrauensverlust    |Trivial    |
|P6 |Kein Leet/Noise    |Schlechte LLM-Eingabe|Niedrig    |
|P4 |Zwei Config-Systeme|Wartungsproblem      |Mittel     |
|P5 |Fehlende Deps      |Nicht lauffähig      |Niedrig    |
|P7 |moondream          |Falsche Signale      |Mittel     |
|P8 |Kein Timeout       |Browser-Timeout      |Trivial    |
|P9 |Proxmox-Scope      |Wartungsaufwand      |Mittel     |
|P10|Kein Fallback      |Stille Fehler        |Niedrig    |

-----

## Revidierte Umsetzungsreihenfolge [I][H]

> **Begründung der Revision:** Die ursprüngliche Reihenfolge war für einen
> Zustand gedacht wo fast nichts existiert. Der precheck-Branch hat StashDB,
> TPDB, Pre-Check-Pipeline und Proxmox bereits implementiert.
> Priorität ist jetzt: Bestehende Arbeit nutzbar machen, dann Lücken füllen.

```
Phase 0 – Sofort: Branch nutzbar machen (Woche 1)
────────────────────────────────────────────────────────────────
  0.1  precheck-Branch in main mergen (oder als stable Branch taggen)
       → Ohne das ist alles andere wertlos
  0.2  README korrigieren ("33 Tests" → tatsächlichen Stand)
  0.3  pyproject.toml: ffmpeg + tesseract als optionale Hinweise
       + Startup-Check: warnen wenn nicht vorhanden
  0.4  Config zusammenführen: ai_config.json als einzige Quelle für API-Keys
       helper.yaml nur noch für Pfade/Namer-Config
  0.5  moondream: als optional markieren, deaktivieren wenn nicht vorhanden
  →
  Ergebnis: Branch ist produktionsreif und installierbar

Phase 1 – Fehlende Basis ergänzen (Woche 2)
────────────────────────────────────────────────────────────────
  A1  normalize.py in filename_parser.py integrieren
      → Leet-Speak (3→e, 0→o, 4→a ...), #/* Noise, Unicode
      → analyzer.py: _STRIP_RE ersetzen durch normalize()
  A2  aliases.py + data/aliases.json
      → EA→Evil Angel, BRZ→Brazzers etc.
      → in filename_parser.py einbinden
  B5  jav.py: ABC-123 Codes → direkt zu TPDB /jav
      → in Pre-Check-Pipeline einbinden (Phase 1 des Lookups)
  A3  log_parser.py: FailureReason Enum
      → Klassifizierung warum Namer gescheitert ist
  →
  Ergebnis: LLM bekommt saubere Eingaben → Trefferrate steigt messbar

Phase 2 – Qualität & Tests (Woche 3)
────────────────────────────────────────────────────────────────
  T1  tests/test_filename_parser.py (incl. Leet-Fälle)
  T2  tests/test_identification.py (alle Confidence-Pfade)
  T3  tests/test_hasher.py (mock ffmpeg)
  T4  tests/test_stashdb.py (mock HTTP)
  T5  tests/test_theporndb.py (mock HTTP)
  FIX Single-Lookup Timeout (120s default)
  FIX UI-Feedback wenn ffmpeg/tesseract fehlt
  FIX Proxmox aus Core isolieren → eigenes optionales Modul
  →
  Ergebnis: Testabdeckung entspricht README-Aussage

Phase 3 – Erweiterungen (Monat 2)
────────────────────────────────────────────────────────────────
  C5  Performer-Alias-Datenbank (data/performer_aliases.json)
      → ergänzt aliases.py für Performer-Abkürzungen
  B4  Beschreibungs-Embedding (nomic-embed-text + ChromaDB)
      → TPDB-Beschreibungen semantisch durchsuchbar
  MVP4 Rule Learning (config/rules.yaml)
      → bestätigte Entscheidungen persistieren
      → Einziger MVP der nach Phase 0 noch komplett fehlt
  →
  Ergebnis: Lernfähiges System, weniger Wiederholung

Phase 4 – Fine-Tuned Modell (Monat 3)
────────────────────────────────────────────────────────────────
  C2  Trainingsdaten-Generator (aus TPDB-Daten + Leet-Varianten)
  C1  Fine-Tuned Modell: qwen2.5:1.5b, Unsloth, RunPod (~1€)
      → GGUF → ollama create namer-v1
      → ERST wenn normalize.py + aliases.py stabil sind
      → Training auf bereinigten Daten, nicht auf Rohdaten
  →
  Ergebnis: Dauerhaft spezialisiertes Modell (~94-96%)

Phase 5 – Vision (Monat 4+, optional)
────────────────────────────────────────────────────────────────
  D1  CLIP statt moondream für Frame-Analyse
      → deterministischer, keine LLM-Halluzinationen
  D2  Studio-Watermark: MobileNetV3 fine-tuned
      → erst wenn CLIP-Basis vorhanden
  D3  Auto-Training-Pipeline (TPDB-Updates)
  D4  Lokale SQLite-Datenbank (offline-fähig)
  D5  Whisper Audio → erst ganz am Ende
  →
  Ergebnis: Vollautonomes System (~97-98%)
```

### Was sich gegenüber der ursprünglichen Reihenfolge geändert hat

|Ursprünglich                  |Revidiert                       |Grund                                          |
|------------------------------|--------------------------------|-----------------------------------------------|
|Phase 1: config/helper.yaml   |→ Phase 0: Config zusammenführen|Branch hat bereits 2 Config-Systeme            |
|Phase 1: TPDB-Client bauen    |→ entfällt                      |Branch hat theporndb.py bereits                |
|Phase 1: StashDB-Fingerprint  |→ entfällt                      |Branch hat stashdb.py bereits                  |
|Phase 2: B1 Performer-Resolve |→ entfällt                      |Branch hat search_by_performer()               |
|Phase 2: B2 Kandidaten-Ranking|→ Phase 3                       |identification.py macht das bereits            |
|Phase 2: B7 Bestätigungsmodus |→ Phase 0                       |Pre-Check-UI im Branch vorhanden               |
|Phase 3: Multi-Source-Suche   |→ entfällt                      |Branch hat 4-Phasen-Pipeline                   |
|Phase 4: Fine-Tuned Modell    |→ Phase 4                       |bleibt, aber erst nach normalize.py            |
|Phase 5: CLIP                 |→ Phase 5, ersetzt moondream    |moondream unzuverlässig                        |
|–                             |Phase 0: Branch mergen          |war nicht bekannt, jetzt kritischster Schritt  |
|–                             |Phase 0.3: pyproject.toml       |fehlt komplett                                 |
|–                             |Phase 1: normalize.py           |war geplant, jetzt noch dringlicher            |
|–                             |Phase 3: Rule Learning          |MVP 4 ist nach wie vor der einzige fehlende MVP|

### Abhängigkeitsgraph (revidiert)

```
Phase 0: Branch mergen
    ↓
    ├──► Config zusammenführen (ai_config.json als Quelle)
    ↓
    ├──► A1 normalize.py
         ├──► A2 aliases.py
         ├──► B5 jav.py
         └──► analyzer.py refactoren (_STRIP_RE → normalize)
              └──► C2 Trainingsdaten (normalize muss stabil sein)
                   └──► C1 Fine-Tuning

Phase 2 Tests laufen parallel zu allem
MVP4 Rule Learning hat keine Abhängigkeit → kann jederzeit starten
CLIP/D1 ersetzt moondream → erst nach Phase 3 sinnvoll
```

### Kritischer Pfad (revidiert)

```
BLOCKIERT ALLES: Branch mergen (Phase 0.1)

DRINGEND DANACH: normalize.py (A1)
  → LLM-Input wird sofort besser
  → Trainingsdaten können erst danach generiert werden

KANN PARALLEL: aliases.py → jav.py → Tests → Config-Merge

ERST WENN NORMALIZE STABIL: Fine-Tuning (C1, C2)

OPTIONAL / ISOLIERBAR: Proxmox-Modul, moondream → CLIP
```

-----

## Detailspezifikation Phase 1 Module [I][H]

### B6 · config/helper.yaml erweitern (ERSTE AUFGABE)

**Datei:** `config/helper.yaml` (fehlt noch, Defaults in docs referenziert)
**Aufgabe:** TPDB, Confidence-Schwellen, Alias-Pfade ergänzen

```yaml
namer:
  config_path: /etc/namer/namer.cfg

tpdb:
  api_key: ""
  base_url: https://api.theporndb.net
  enabled: false                        # erst aktivieren wenn Key gesetzt

stashdb:
  api_key: ""
  base_url: https://stashdb.org/graphql
  enabled: false

stash:
  url: http://localhost:9999
  api_key: ""
  timeout: 15
  enabled: true

ollama:
  base_url: http://localhost:11434
  model: qwen2.5:3b                     # besser als llama3 für diesen Zweck
  model_batch: qwen2.5:1.5b
  model_embed: nomic-embed-text
  timeout: 30
  enabled: true

matching:
  confidence_auto: 0.85
  confidence_suggest: 0.60
  confidence_skip: 0.40
  max_candidates: 5

paths:
  aliases_file: data/aliases.json
  performer_aliases_file: data/performer_aliases.json
  chroma_dir: data/chroma
  reports_dir: /var/lib/namer-helper/reports
```

-----

### A1 · normalize.py – Normalisierungs-Pipeline

**Datei:** `src/namer_helper/normalize.py`
**Achtung:** `analyzer.py` hat bereits `_STRIP_RE` – A1 ERWEITERT das, ersetzt es nicht

**Leet-Mapping (kontextabhängig – nur zwischen Buchstaben ersetzen):**

```python
LEET_MAP = {
    '0': 'o', '1': 'i', '3': 'e', '4': 'a',
    '5': 's', '7': 't', '8': 'b', '9': 'g',
    '@': 'a', '$': 's', '!': 'i', '+': 't',
}
```

**Wichtige Regel:** `1080p`, `2023`, `x264` – NICHT anfassen
(Leet NUR ersetzen wenn benachbarte Buchstaben vorhanden)

**Noise-Zeichen:** `#`, `*`, `[`, `]`, `(`, `)` – entfernen

**Sonderfälle:**

- `cu#t` → `cut`
- `p*rn`, `pr0n` → `porn`
- `Film - Kopie (3)` → `Film`
- `%C3%A9` → URL-Decode → ASCII-Normalize

**Output-Format:**

```python
@dataclass
class NormalizeResult:
    original: str
    normalized: str
    ext: str
    leet_detected: bool
    noise_detected: bool
    steps: dict[str, str]   # für Debugging/Logging
```

-----

### A2 · aliases.py – Studio-Alias-Wörterbuch

**Dateien:** `src/namer_helper/aliases.py`, `data/aliases.json`

**Initiale aliases.json:**

```json
{
  "_meta": {"version": 1, "source": "manual+auto"},
  "studios": {
    "EA": "Evil Angel", "BRZ": "Brazzers", "NF": "NubileFilms",
    "DDF": "DDFNetwork", "21S": "21Sextury", "ZTOD": "ZeroTolerance",
    "MFX": "MetFX", "RK": "Reality Kings", "WF": "WoodmanCastingX",
    "NXG": "Naughty America", "LES": "LesbianX", "GIO": "GirlsInOrgasms"
  },
  "performers": {}
}
```

**Selbstlern-Mechanismus:** bei erfolgreichem TPDB-Match neue Abkürzung speichern

-----

### A3 · log_analyser – log_parser.py erweitern

**Aufgabe:** `FailedMatch` um `failure_reason: FailureReason` erweitern

```python
class FailureReason(Enum):
    NO_DATE          = "A"   # Kein Datum erkannt
    UNKNOWN_STUDIO   = "B"   # Studio nicht in aliases
    PERFORMER_ABBREV = "C"   # Performer-Abkürzung nicht aufgelöst
    LOW_FUZZY_SCORE  = "D"   # Score < 95% (im Log sichtbar)
    ZERO_API_RESULTS = "E"   # 0 Ergebnisse (im Log sichtbar)
    MULTIPLE_MATCHES = "F"   # Mehrere Treffer, kein Gewinner
    LEET_NOT_PARSED  = "H"   # Leet-Zeichen im Namen
    NOISE_CHARS      = "I"   # #/*/ [ ] im Namen
    UNKNOWN          = "Z"   # nicht klassifizierbar
```

-----

### B5 · jav.py – JAV-Code-Erkennung

**Datei:** `src/namer_helper/jav.py`

**Regex:** `\b([A-Z]{2,6}-\d{2,5})\b`
**Sonderfall FC2:** `FC2-PPV-\d{6,8}` – eigene Behandlung

-----

### A4 · analyzer.py refactoren – Query-Normalisierung

**Aufgabe:** bestehenden `_STRIP_RE` durch A1 `normalize()` ersetzen
**Neues LLM-Output-Format:**

```json
{
  "candidates": [
    {"query": "Evil Angel Jane Doe Hot Scene 2023", "confidence": 0.90},
    {"query": "Evil Angel Jane Doe Hot Scene",      "confidence": 0.75},
    {"query": "Jane Doe Hot Scene 2023",            "confidence": 0.60}
  ],
  "removed_noise": ["final", "CUT", "v2", "1080p"],
  "detected_studio": "Evil Angel",
  "detected_performers": ["Jane Doe"],
  "detected_year": "2023",
  "detected_tags": [],
  "jav_code": null
}
```

**Modell-Empfehlung:** `qwen2.5:3b` statt `llama3` (besser für JSON)

-----

## Trefferrate-Erwartung je Phase [I][M]

```
Nur Namer (aktuell):           ~60-70% Auto-Match
+ Phase 1 (Normalisierung):    ~72-78%
+ Phase 2 (Performer/Rank):    ~82-87%
+ Phase 3 (Embedding/Hash):    ~90-93%
+ Phase 4 (Fine-Tuned LLM):    ~94-96%
+ Phase 5 (Frame/Vision):      ~97-98%

Verbleibende 2-3%: Unikate, nie in TPDB erfasst – manuell (Top-5-Vorschlag)
```

-----

## Ollama Modelfile (für qwen2.5:3b spezialisieren) [I][M]

```dockerfile
# modelfiles/scene-parser.Modelfile
FROM qwen2.5:3b

SYSTEM """
Du bist ein Parser für Video-Dateinamen aus der Erwachsenenunterhaltung.
Extrahiere alle erkennbaren Informationen. Gib NUR valides JSON zurück.

Erkennbare Felder:
- studio: Studioname (normalisiert, z.B. "Evil Angel" nicht "EA")
- performers: Array von Vor- und Nachnamen
- title: Szenenname wenn erkennbar
- year: Jahreszahl (4-stellig)
- resolution: 480p / 720p / 1080p / 2160p / 4K
- tags: Array erkannter Genre/Tags
- jav_code: Studio-Code (z.B. "ABP-123")
- confidence: 0.0-1.0

Regeln:
- Leet-Speak bereits normalisiert (Eingabe ist bereinigt)
- Wenn Feld unklar: weglassen statt raten
- Kein Text außerhalb des JSON
"""

PARAMETER temperature 0.05
PARAMETER num_ctx 2048
```

```bash
ollama create scene-parser -f modelfiles/scene-parser.Modelfile
```

-----

## Entwicklungsregeln [E][H]

- **Kein Raten:** LLM darf keine Metadaten erfinden – nur normalisieren + ranken
- **Namer unangetastet:** Kein Eingriff in Namer-Core
- **Determinismus first:** Regelbasiert wo möglich, LLM nur als Fallback
- **Commit-Disziplin:** `py_compile` → `unittest` → `git diff --check` vor jedem Commit
- **dry_run:** Jede umbenennende Aktion hat `dry_run=True` als Standard
- **Offline-Degradierung:** Alle Module müssen ohne externe APIs funktionieren
- **Config-Format:** YAML (`config/helper.yaml`), nicht .cfg
- **Fehler nie werfen:** Wie StashClient/OllamaClient – Fehler in Result-Objekt

-----

## Nächste konkrete Schritte (revidiert) [I][H]

```
Schritt 0 – Branch nutzbar machen (heute):
  git checkout backup/precheck-ai-proxmox-20260529
  git checkout -b stable/precheck   # oder direkt in main mergen

Schritt 1 – System-Check:
  which ffmpeg tesseract             # sind die binaries da?
  ollama list                        # ist moondream vorhanden?
  python -c "import namer_helper"    # ist das Paket installierbar?

Schritt 2 – README korrigieren:
  "33 Tests" entfernen → aktuellen Stand dokumentieren

Schritt 3 – Config zusammenführen:
  ai_config.json = API-Keys + Ollama-URL + pre_check_dir
  helper.yaml = nur Pfade + Namer-Config (ollama_url entfernen)

Schritt 4 – normalize.py schreiben (A1):
  src/namer_helper/normalize.py
  → Leet (3→e, 0→o, 4→a, 1→i, 5→s, 7→t)
  → NUR zwischen Buchstaben (1080p bleibt 1080p)
  → Noise (#, *, [, ])
  → Unicode → ASCII
  tests/test_normalize.py mit Leet-Fällen

Schritt 5 – aliases.py + data/aliases.json (A2):
  EA→Evil Angel, BRZ→Brazzers etc.
  in filename_parser.py einbinden

Schritt 6 – jav.py (B5):
  Regex: \b([A-Z]{2,6}-\d{2,5})\b
  Sonderfall FC2-PPV
  in Pre-Check-Lookup Phase 1 einbinden

Schritt 7 – analyzer.py refactoren:
  _STRIP_RE ersetzen durch normalize()
  Eingabe an LLM ist jetzt bereinigt

Schritt 8 – Tests schreiben:
  test_filename_parser.py
  test_identification.py (alle Confidence-Pfade)
  test_hasher.py (mock ffmpeg)
  test_stashdb.py (mock HTTP)
```

-----

## Repository: Nanja-at-web/ProxmoxVED [E][H]

> **Fork von:** `community-scripts/ProxmoxVED` (ursprünglich von tteck/ProxmoxVE)
> **Zweck:** Proxmox LXC-Installationsskripte für Namer (und andere Apps)
> **Beziehung zu namer-helper:** Deployment-Layer – installiert Namer als LXC auf Proxmox

### Branches

|Branch                             |Stand      |Inhalt                                              |
|-----------------------------------|-----------|----------------------------------------------------|
|`main`                             |aktiv      |Namer LXC-Skript, produktionsreif                   |
|`codex/add-namer`                  |Entwicklung|Fast identisch mit main, ohne COMMUNITY_SCRIPTS_URL |
|`backup/codex-add-namer-2026-05-22`|Snapshot   |Ältere Version, Source zeigt auf Fork statt upstream|

### Dateistruktur (namer-relevant)

```
ProxmoxVED/
├── ct/
│   └── namer.sh               # Launcher-Skript (LXC erstellen + Namer installieren)
├── install/
│   └── namer-install.sh       # Eigentliches Installationsskript (läuft im LXC)
└── json/
    └── namer.json             # Metadaten für community-scripts Web-UI
```

**Fehlend (noch nicht erstellt):**

```
ct/namer-helper.sh             # namer-helper LXC-Skript
install/namer-helper-install.sh
json/namer-helper.json
ct/stash.sh                    # StashApp LXC
```

-----

### ct/namer.sh – Was es tut

```bash
# Bezieht build.func von community-scripts/ProxmoxVED (upstream, nicht Fork!)
source <(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVED/main/misc/build.func)

# LXC-Ressourcen:
var_cpu=2, var_ram=2048, var_disk=8
var_os=debian, var_version=13
var_unprivileged=1   # unprivilegierter Container

# Update-Kanäle:
NAMER_UPDATE_CHANNEL=package  # PyPI (aktiv)
NAMER_UPDATE_CHANNEL=github   # reserviert, noch nicht implementiert

# Zugriff nach Installation:
http://{IP}:6980  # Namer WebUI
```

**Besonderheit in main:** Definiert `COMMUNITY_SCRIPTS_URL` auf den Fork, aber nutzt es (noch) nicht aktiv.

-----

### install/namer-install.sh – Was installiert wird

```bash
# System-Abhängigkeiten:
apt install -y ffmpeg nfs-common
# → ffmpeg IS installiert! (löst P5 aus namer-helper-Kritik teilweise)
# → tesseract fehlt noch (OCR für Pre-Check)

# Python-Setup:
uv venv --clear --python 3.11 /opt/namer/.venv
uv pip install namer   # von PyPI

# Verzeichnisse:
/opt/namer/             # Anwendung + venv
/etc/namer/namer.cfg    # Konfiguration (chmod 0640)
/var/lib/namer/watch/   # Eingangsordner
/var/lib/namer/work/    # Verarbeitungsordner
/var/lib/namer/failed/  # Fehlgeschlagene Dateien
/var/lib/namer/dest/    # Erfolgreich umbenannte Dateien
/var/lib/namer/database/
/mnt/nas/               # NFS-Mountpunkt

# Bootstrap-Config via configupdater (erhält Kommentare):
porndb_token = ""
database_path = /var/lib/namer/database
watch_dir / work_dir / failed_dir / dest_dir gesetzt
web = True aktiviert

# Systemd-Service:
namer-watchdog.service  → python -m namer watchdog
# RestartSec=5, Restart=on-failure
# After=network-online.target remote-fs.target

# Umgebungsdatei:
/etc/default/namer:
  NAMER_CONFIG=/etc/namer/namer.cfg
  NAMER_UPDATE_CHANNEL=package
```

-----

### Unterschiede zwischen den Branches

|                     |main                             |codex/add-namer          |backup                          |
|---------------------|---------------------------------|-------------------------|--------------------------------|
|COMMUNITY_SCRIPTS_URL|✓ definiert                      |✗ fehlt                  |✗ fehlt                         |
|Source-Verweis       |ThePornDatabase/namer            |ThePornDatabase/namer    |Nanja-at-web/namer              |
|var_arm64            |`no`                             |`no`                     |fehlt                           |
|Abschluss-Meldung    |`Completed successfully!` (klein)|`Completed successfully!`|`Completed Successfully!` (groß)|

Inhaltlich sind main und codex/add-namer fast identisch – main hat minimale Bereinigungen.

-----

### Was für namer-helper noch fehlt

Damit namer-helper als vollständige LXC-Installation verfügbar ist:

```
1. ct/namer-helper.sh
   → LXC erstellen, namer-helper-install.sh aufrufen
   → Port 6981, var_ram=1024, var_disk=4
   → Abhängigkeit: namer-LXC muss laufen (shared volume namer_failed)

2. install/namer-helper-install.sh
   → apt install ffmpeg tesseract-ocr nfs-common    ← tesseract NEU
   → uv pip install namer-helper
   → /etc/namer-helper/ Verzeichnis
   → /var/lib/namer/pre-check/ erstellen
   → /opt/namer-helper/scan-status/ erstellen
   → systemd service: namer-helper-web.service (Port 6981)

3. json/namer-helper.json
   → interface_port: 6981
   → Hinweis: namer-LXC muss konfiguriert sein
   → Hinweis: StashDB API Key optional

4. Netzwerk-Verbindung zwischen LXCs:
   → namer-helper braucht Zugriff auf namer failed-Verzeichnis
   → Empfehlung: NFS oder shared bind-mount
   → NICHT in-container NFS (wie namer.json warnt)
```

-----

### Beziehung der Repos zueinander

```
community-scripts/ProxmoxVED  ← upstream (wird nicht direkt genutzt)
        ↓
Nanja-at-web/ProxmoxVED       ← Fork, baut LXC-Skripte für Namer
        ↓
        ↓ installiert
        ↓
Proxmox Host
  ├── LXC: Namer (Port 6980)          ← ct/namer.sh
  │         /var/lib/namer/
  │
  ├── LXC: namer-helper (Port 6981)   ← ct/namer-helper.sh (fehlt noch)
  │         liest /var/lib/namer/failed/
  │
  ├── LXC: StashApp (Port 9999)       ← ct/stash.sh (fehlt noch)
  │
  └── LXC: Ollama (Port 11434)        ← community-scripts/ProxmoxVE/ct/ollama.sh
```

**Wichtige Erkenntnis:** `ffmpeg` wird vom Namer-LXC-Skript bereits installiert.
namer-helper bräuchte ein eigenes LXC mit `ffmpeg` + `tesseract-ocr`.
Wenn namer-helper im gleichen LXC wie Namer läuft, ist ffmpeg bereits da.

-----

### Kritik an ProxmoxVED

**Positiv:**

- Saubere Struktur, folgt community-scripts Konventionen
- `configupdater` statt sed für Config – erhält Kommentare, kein Datenverlust
- ffmpeg wird korrekt als Dependency installiert
- Update-Mechanismus mit Versions-Check ist durchdacht
- nfs-common vorinstalliert – NAS-Integration vorbereitet

**Problematisch:**

- `COMMUNITY_SCRIPTS_URL` in main definiert aber nie genutzt – totes Holz oder halbfertiger Plan
- GitHub update channel reserviert aber wirft nur Error – irreführend
- `Co-Author: OpenAI Codex` in Copyright – unüblich, möglicherweise problematisch bei Upstream-Merge
- Kein namer-helper Skript – die wichtigste Ergänzung fehlt
- Kein tesseract – OCR für Pre-Check funktioniert nicht ohne es
- pre-check Verzeichnis `/var/lib/namer/pre-check/` wird nicht angelegt
- Kein StashApp-Skript obwohl es in der Architektur zentral ist

-----

*Exportiert aus Claude-Session | Projekt: namer-helper | Stand: 2026-05-30*
*Branches: main + backup/precheck-ai-proxmox-20260529 (vollständig analysiert)*
*Kritik + revidierte Umsetzungsreihenfolge: 2026-05-30*
*Tags: [C] Claude Sonnet 4.6 | [E]=Implementiert [I]=Geplant | [H/M/L]=Priorität*

-----

## Implementierungsstand stable/precheck (laufend aktualisiert) [E][H]

**Branch:** `stable/precheck` | **Tests:** 118 | **Letzter Commit:** 2026-05-30

### Abgeschlossene Schritte

| Schritt | Modul | Status |
|---------|-------|--------|
| 0 | Branch `stable/precheck` erstellt, CLAUDE.md committed | ✓ |
| 1a | README: Testanzahl korrigiert (50 → 118, wächst) | ✓ |
| 1b | `helper.yaml`: Hinweis dass ollama/API-Keys aus `ai_config.json` kommen | ✓ |
| 1c | `pyproject.toml`: ffmpeg/tesseract/moondream als System-Dep dokumentiert | ✓ |
| 1d | `app.py`: Startup-Check für Binaries, moondream-Verfügbarkeitscheck | ✓ |
| 4 | `normalize.py`: Leet, Noise, URL-decode, Unicode (93→118 Tests) | ✓ |
| 5 | `aliases.py` + `data/aliases.json`: Studio-Alias-Auflösung | ✓ |

### Noch ausstehend

| Schritt | Modul | Priorität |
|---------|-------|-----------|
| 6 | `jav.py`: ABC-123 Code-Erkennung | Hoch |
| 7 | `analyzer.py`: `_STRIP_RE` → `normalize()` refactoren | Hoch |
| T | Tests: `test_filename_parser.py` mit normalize-Integration | Mittel |

### Wichtige Architekturentscheidungen

- **normalize.py** läuft VOR `filename_parser.py` und `analyzer.py`
  - Gibt bereinigten Stem zurück (Separatoren erhalten, für downstream)
  - Schutz über `_TECH_TOKEN_RE` (1080p, x264, S01E05, …)
  - Chain-Propagation: `chars[i]` statt `token[i]` → D03 → Doe
- **aliases.py** läuft NACH `filename_parser.py` (optionaler Parameter)
  - Großbuchstaben-Abkürzungen unangetastet von normalize.py
  - EA → Evil Angel erst durch aliases.py, nicht durch Leet-Logik
  - `learn()` ist best-effort: silent fail bei I/O-Fehler
- **ai_config.json** ist einzige Quelle für API-Keys und ollama_url (Web)
  - `helper.yaml` ist nur Referenz/Defaults für Pfade
