# Stash Bridge

Die Stash Bridge verbindet namer-helper mit einer laufenden StashApp-Instanz.
StashApp dient als lokale Metadaten-Quelle — Szenen die dort bereits gescrapt
wurden, müssen nicht neu gesucht werden.

---

## Voraussetzung: StashApp läuft

Die Stash Bridge funktioniert mit jeder StashApp-Instanz — lokal, im LXC oder
auf einem anderen System im Netzwerk.

Beispiel-Instanz:

```
http://192.168.1.123:9999
```

GraphQL-Endpoint (wird automatisch ergänzt):

```
http://192.168.1.123:9999/graphql
```

---

## Konfiguration

In `config/helper.yaml`:

```yaml
stash:
  url: http://192.168.1.123:9999
  api_key: ""       # leer lassen wenn keine Auth aktiv
  timeout: 15
  enabled: true
```

### API-Key (optional)

Falls in StashApp ein API-Key gesetzt ist:
`Settings → Security → API Key`

Den Key in `helper.yaml` unter `api_key` eintragen.

---

## Verwendung

### Einzelne Dateinamen suchen

```bash
namer-helper stash-search \
  "Studio.Title.1080p.x264.mp4" \
  "Unknown.Scene.2024.mkv" \
  --stash-url http://192.168.1.123:9999
```

### Alle fehlgeschlagenen Namer-Treffer durchsuchen

```bash
namer-helper stash-search \
  --failed-dir /var/lib/namer/failed \
  --stash-url http://192.168.1.123:9999
```

### Mit namer.cfg (liest failed_dir automatisch)

```bash
namer-helper stash-search \
  --namer-config /etc/namer/namer.cfg \
  --stash-url http://192.168.1.123:9999
```

---

## Wie die Suche funktioniert

```text
Dateiname
   │
   ▼
1. Pfad-Suche in StashApp          confidence: 0.90
   (INCLUDES-Match auf Dateiname)
   │
   ├── Treffer → Ergebnis zurückgeben
   │
   └── kein Treffer
           │
           ▼
       2. Titel-Suche                confidence: 0.65
          (bereinigter Dateiname als Suchbegriff)
          │
          ├── Treffer → Ergebnis zurückgeben
          └── kein Treffer → leer
```

---

## Confidence-Werte

| Match-Typ | Confidence | Bedeutung |
|---|---:|---|
| Pfad-Match (`path`) | 0.90 | StashApp kennt die Datei direkt |
| Titel-Suche (`title_search`) | 0.65 | Szene gefunden, aber Pfad unbekannt |

---

## Mehrere StashApp-Instanzen

Falls StashApp auf mehreren Systemen läuft, einfach den `--stash-url`-Parameter
wechseln:

```bash
# Proxmox-Instanz
namer-helper stash-search "file.mp4" --stash-url http://192.168.1.123:9999

# andere Instanz
namer-helper stash-search "file.mp4" --stash-url http://192.168.1.200:9999
```

---

## Zusammenspiel mit anderen MVPs

```text
Failed Match (MVP 1 Report)
        │
        ▼
Stash Bridge suchen (MVP 3)
        │
        ├── Treffer (confidence >= 0.65)
        │       → Metadaten übernehmen
        │
        └── kein Treffer
                │
                ▼
            Ollama Assist (MVP 2)
                → Suchvorschläge generieren
```
