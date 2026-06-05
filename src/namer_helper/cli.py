"""
namer-helper CLI entry point.
"""

from __future__ import annotations

from pathlib import Path

import click
from loguru import logger

from namer_helper.namer_bridge.config_reader import read_namer_paths
from namer_helper.namer_bridge.log_parser import collect_failed_matches
from namer_helper.ollama_bridge.analyzer import analyze_filenames
from namer_helper.ollama_bridge.client import OllamaClient
from namer_helper.reports.renderer import render_report
from namer_helper.stash_bridge.client import StashClient
from namer_helper.stash_bridge.matcher import search_scenes


@click.group()
@click.version_option()
def main() -> None:
    """Namer Helper — sidecar tools for Namer."""


@main.command("serve")
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind-Adresse")
@click.option("--port", default=6981, show_default=True, help="Port")
@click.option(
    "--namer-config",
    default="/etc/namer/namer.cfg",
    show_default=True,
    help="Pfad zur namer.cfg",
)
@click.option(
    "--report-dir",
    default="/var/lib/namer-helper/reports",
    show_default=True,
    help="Report-Ausgabeverzeichnis",
)
def serve_cmd(host: str, port: int, namer_config: str, report_dir: str) -> None:
    """Web-Dashboard starten (Port 6981)."""
    import uvicorn
    from namer_helper.web.app import create_app

    app = create_app(Path(namer_config), Path(report_dir))
    logger.info(f"Dashboard: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


@main.command("report")
@click.option(
    "--namer-config",
    default="/etc/namer/namer.cfg",
    show_default=True,
    help="Pfad zur namer.cfg",
)
@click.option(
    "--failed-dir",
    default=None,
    help="Pfad zum failed-Verzeichnis (überschreibt namer.cfg)",
)
@click.option(
    "--output-dir",
    default="/var/lib/namer-helper/reports",
    show_default=True,
    help="Ausgabeverzeichnis für Reports",
)
@click.option(
    "--format",
    "fmt",
    default="both",
    type=click.Choice(["markdown", "json", "both"]),
    show_default=True,
    help="Ausgabeformat",
)
@click.option(
    "--anonymize",
    is_flag=True,
    default=False,
    help="Dateinamen durch Pseudonyme ersetzen (für Debugging/Weitergabe)",
)
def report_cmd(namer_config: str, failed_dir: str | None, output_dir: str, fmt: str, anonymize: bool) -> None:
    """Fehlgeschlagene Namer-Treffer sammeln und Report erzeugen."""
    config_path = Path(namer_config)

    if failed_dir:
        resolved_failed_dir = Path(failed_dir)
    elif config_path.exists():
        paths = read_namer_paths(config_path)
        resolved_failed_dir = paths["failed_dir"]
        logger.info(f"failed_dir aus namer.cfg: {resolved_failed_dir}")
    else:
        logger.error(f"namer.cfg nicht gefunden: {config_path}")
        raise click.Abort()

    if not resolved_failed_dir.exists():
        logger.warning(f"failed_dir existiert nicht: {resolved_failed_dir}")
        resolved_failed_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Scanne: {resolved_failed_dir}")
    matches = collect_failed_matches(resolved_failed_dir)
    logger.info(f"Gefunden: {len(matches)} fehlgeschlagene Treffer")

    written = render_report(matches, Path(output_dir), fmt=fmt, anonymize=anonymize)
    for p in written:
        label = " (anonymisiert)" if anonymize else ""
        logger.success(f"Report geschrieben{label}: {p}")


@main.command("analyze")
@click.argument("filenames", nargs=-1, required=False)
@click.option(
    "--failed-dir",
    default=None,
    help="failed-Verzeichnis — analysiert alle Dateinamen dort",
)
@click.option(
    "--namer-config",
    default="/etc/namer/namer.cfg",
    show_default=True,
    help="Pfad zur namer.cfg (für failed_dir)",
)
@click.option(
    "--ollama-url",
    default="http://localhost:11434",
    show_default=True,
    help="Ollama-Server URL",
)
@click.option(
    "--model",
    default="llama3",
    show_default=True,
    help="Ollama-Modell",
)
@click.option(
    "--timeout",
    default=30,
    show_default=True,
    help="Timeout in Sekunden",
)
def analyze_cmd(
    filenames: tuple[str, ...],
    failed_dir: str | None,
    namer_config: str,
    ollama_url: str,
    model: str,
    timeout: int,
) -> None:
    """Dateinamen via Ollama analysieren und Suchvorschläge erhalten."""
    client = OllamaClient(base_url=ollama_url, timeout=timeout)

    if not client.is_available():
        logger.error(f"Ollama nicht erreichbar unter {ollama_url}")
        raise click.Abort()

    names: list[str] = list(filenames)

    if not names and failed_dir:
        from namer_helper.namer_bridge.log_parser import collect_failed_matches
        matches = collect_failed_matches(Path(failed_dir))
        names = [m.file_path.name for m in matches]
    elif not names:
        config_path = Path(namer_config)
        if config_path.exists():
            paths = read_namer_paths(config_path)
            matches = collect_failed_matches(paths["failed_dir"])
            names = [m.file_path.name for m in matches]

    if not names:
        logger.warning("Keine Dateinamen zum Analysieren gefunden.")
        return

    logger.info(f"Analysiere {len(names)} Dateinamen mit {model} via {ollama_url}")
    results = analyze_filenames(names, client, model=model)

    for r in results:
        if r.error:
            logger.error(f"{r.filename}: {r.error}")
            continue
        logger.info(f"\n{'─' * 60}")
        logger.info(f"Datei:    {r.filename}")
        logger.info(f"Titel:    {r.cleaned_name}")
        logger.info(f"Score:    {r.confidence:.2f}  →  {r.recommended_action}")
        logger.info(f"Grund:    {r.reason}")
        for i, q in enumerate(r.search_queries, 1):
            logger.info(f"Suche {i}:  {q}")


@main.command("index-tpdb")
@click.argument(
    "source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--ollama-url",
    default="http://localhost:11434",
    show_default=True,
    help="Ollama-Server URL",
)
@click.option(
    "--model",
    default="nomic-embed-text",
    show_default=True,
    help="Bevorzugtes Embedding-Modell",
)
@click.option(
    "--persist-dir",
    default="/etc/namer-helper/embeddings",
    show_default=True,
    help="ChromaDB-Verzeichnis",
)
def index_tpdb_cmd(source: Path, ollama_url: str, model: str, persist_dir: str) -> None:
    """TPDB JSON/JSONL-Szenen in den lokalen Embedding-Index schreiben."""
    from namer_helper.embedding import ChromaSceneIndex, OllamaEmbedder, load_scene_documents

    docs = load_scene_documents(source)
    if not docs:
        logger.error(f"Keine indexierbaren TPDB-Dokumente in {source}")
        raise click.Abort()

    embedder = OllamaEmbedder(base_url=ollama_url, model=model)
    resolved_model = embedder.resolve_model()
    if not resolved_model:
        logger.error(f"Kein Embedding-Modell in Ollama verfügbar unter {ollama_url}")
        raise click.Abort()

    index = ChromaSceneIndex(Path(persist_dir))
    result = index.upsert(docs, embedder)
    if result.error:
        logger.error(result.error)
        raise click.Abort()

    logger.success(f"Indexiert: {len(docs)} TPDB-Dokument(e) mit {resolved_model} -> {persist_dir}")


@main.command("index-tpdb-cache")
@click.option(
    "--cache-dir",
    default="/opt/namer-helper/lookup-cache",
    show_default=True,
    help="Pre-Check Lookup-Cache-Verzeichnis",
)
@click.option(
    "--ollama-url",
    default="http://localhost:11434",
    show_default=True,
    help="Ollama-Server URL",
)
@click.option(
    "--model",
    default="nomic-embed-text",
    show_default=True,
    help="Bevorzugtes Embedding-Modell",
)
@click.option(
    "--persist-dir",
    default="/etc/namer-helper/embeddings",
    show_default=True,
    help="ChromaDB-Verzeichnis",
)
def index_tpdb_cache_cmd(cache_dir: str, ollama_url: str, model: str, persist_dir: str) -> None:
    """TPDB-Treffer aus dem lokalen Pre-Check-Cache indexieren."""
    from namer_helper.embedding import ChromaSceneIndex, OllamaEmbedder, load_lookup_cache_documents

    docs = load_lookup_cache_documents(Path(cache_dir))
    if not docs:
        logger.warning(f"Keine TPDB-Dokumente im Lookup-Cache gefunden: {cache_dir}")
        return

    embedder = OllamaEmbedder(base_url=ollama_url, model=model)
    resolved_model = embedder.resolve_model()
    if not resolved_model:
        logger.error(f"Kein Embedding-Modell in Ollama verfügbar unter {ollama_url}")
        raise click.Abort()

    index = ChromaSceneIndex(Path(persist_dir))
    result = index.upsert(docs, embedder)
    if result.error:
        logger.error(result.error)
        raise click.Abort()

    logger.success(f"Indexiert: {len(docs)} Cache-TPDB-Dokument(e) mit {resolved_model} -> {persist_dir}")


@main.command("search-embedding")
@click.argument("query")
@click.option(
    "--ollama-url",
    default="http://localhost:11434",
    show_default=True,
    help="Ollama-Server URL",
)
@click.option(
    "--model",
    default="nomic-embed-text",
    show_default=True,
    help="Bevorzugtes Embedding-Modell",
)
@click.option(
    "--persist-dir",
    default="/etc/namer-helper/embeddings",
    show_default=True,
    help="ChromaDB-Verzeichnis",
)
@click.option(
    "--limit",
    default=3,
    show_default=True,
    help="Maximale Trefferanzahl",
)
@click.option(
    "--min-score",
    default=0.25,
    show_default=True,
    help="Mindestscore für angezeigte Treffer",
)
def search_embedding_cmd(query: str, ollama_url: str, model: str, persist_dir: str, limit: int, min_score: float) -> None:
    """Lokalen Embedding-Index direkt durchsuchen."""
    from namer_helper.embedding import search_scene_index

    result = search_scene_index(
        query,
        ollama_url=ollama_url,
        persist_dir=Path(persist_dir),
        model=model,
        limit=limit,
        min_score=min_score,
    )
    if result.error:
        logger.error(result.error)
        raise click.Abort()
    if not result.found:
        logger.warning("Keine Embedding-Treffer")
        return

    for hit in result.hits:
        logger.info(f"{hit.score:.3f}  {hit.id}  {hit.title}")
        metadata = hit.metadata
        details = []
        for key in ("site", "date", "duration", "sku", "performers", "url"):
            value = metadata.get(key)
            if value not in (None, "", []):
                details.append(f"{key}: {value}")
        if details:
            logger.info(f"  {' | '.join(details)}")


@main.command("generate-training")
@click.option(
    "--rules",
    "rules_path",
    default="/etc/namer-helper/rules.yaml",
    show_default=True,
    help="Rule-Learning YAML mit bestätigten Renames",
)
@click.option(
    "--output",
    "output_path",
    default="/etc/namer-helper/training/rules.jsonl",
    show_default=True,
    help="Ausgabe-Datei im JSONL-Format",
)
@click.option(
    "--variants",
    default=12,
    show_default=True,
    help="Varianten pro bestätigter Regel",
)
@click.option(
    "--seed",
    default=0,
    show_default=True,
    help="Seed für deterministische Varianten",
)
def generate_training_cmd(rules_path: str, output_path: str, variants: int, seed: int) -> None:
    """C2: Trainingsdaten aus bestätigten Rule-Learning-Renames erzeugen."""
    from namer_helper.training.generator import generate_from_rules_file

    count = generate_from_rules_file(
        Path(rules_path),
        Path(output_path),
        variants_per_rule=variants,
        seed=seed,
    )
    logger.success(f"Trainingsbeispiele geschrieben: {count} -> {output_path}")


@main.command("import-vocabulary")
@click.option(
    "--from", "source",
    type=click.Choice(["stash", "stashdb", "tpdb"]),
    required=True,
    help="Quelle: lokale StashApp / StashDB cloud / ThePornDB",
)
@click.option("--config-dir", default="/etc/namer-helper", show_default=True,
              help="Verzeichnis für known_*.json + ai_config.json")
@click.option("--stash-url", default="http://localhost:9999", show_default=True,
              help="StashApp-URL (nur bei --from stash)")
@click.option("--stash-api-key", default="", help="StashApp API-Key (falls gesetzt)")
@click.option("--stashdb-key", default="", help="StashDB API-Key (sonst aus ai_config.json)")
@click.option("--tpdb-key", default="", help="ThePornDB API-Key (sonst aus ai_config.json)")
def import_vocabulary_cmd(source, config_dir, stash_url, stash_api_key, stashdb_key, tpdb_key):
    """Studio-/Performer-Wortschatz vorab aus einer Quelle befüllen (Option 2)."""
    from namer_helper import vocab_import
    from namer_helper.web.ai_config import load_ai_config

    cd = Path(config_dir)
    ai = load_ai_config(cd)
    stashdb_key = stashdb_key or ai.stashdb_api_key
    tpdb_key = tpdb_key or ai.theporndb_api_key

    def prog(msg: str) -> None:
        logger.info(msg)

    logger.info(f"Import aus '{source}' → {cd}")
    if source == "stash":
        res = vocab_import.import_from_stash(cd, url=stash_url, api_key=stash_api_key, progress=prog)
    elif source == "stashdb":
        res = vocab_import.import_from_stashdb(cd, api_key=stashdb_key, progress=prog)
    else:
        res = vocab_import.import_from_tpdb(cd, api_key=tpdb_key, progress=prog)

    if res["error"]:
        logger.error(
            f"Abbruch: {res['error']} "
            f"(bis dahin +{res['studios']} Studios, +{res['performers']} Performer gelernt)"
        )
        raise click.Abort()
    logger.success(
        f"Fertig: +{res['studios']} Studios, +{res['performers']} Performer in den Wortschatz."
    )


@main.command("stash-search")
@click.argument("filenames", nargs=-1, required=False)
@click.option(
    "--failed-dir",
    default=None,
    help="failed-Verzeichnis — sucht alle Dateinamen dort in StashApp",
)
@click.option(
    "--namer-config",
    default="/etc/namer/namer.cfg",
    show_default=True,
    help="Pfad zur namer.cfg (für failed_dir)",
)
@click.option(
    "--stash-url",
    default="http://localhost:9999",
    show_default=True,
    help="StashApp-URL",
)
@click.option(
    "--api-key",
    default="",
    help="StashApp API-Key (falls gesetzt)",
)
@click.option(
    "--timeout",
    default=15,
    show_default=True,
    help="Timeout in Sekunden",
)
def stash_search_cmd(
    filenames: tuple[str, ...],
    failed_dir: str | None,
    namer_config: str,
    stash_url: str,
    api_key: str,
    timeout: int,
) -> None:
    """Dateinamen in StashApp suchen und vorhandene Metadaten anzeigen."""
    client = StashClient(url=stash_url, api_key=api_key, timeout=timeout)

    if not client.is_available():
        logger.error(f"StashApp nicht erreichbar unter {stash_url}")
        raise click.Abort()

    names: list[str] = list(filenames)

    if not names and failed_dir:
        matches = collect_failed_matches(Path(failed_dir))
        names = [m.file_path.name for m in matches]
    elif not names:
        config_path = Path(namer_config)
        if config_path.exists():
            paths = read_namer_paths(config_path)
            matches = collect_failed_matches(paths["failed_dir"])
            names = [m.file_path.name for m in matches]

    if not names:
        logger.warning("Keine Dateinamen zum Suchen gefunden.")
        return

    logger.info(f"Suche {len(names)} Dateinamen in StashApp ({stash_url})")
    results = search_scenes(names, client)

    for r in results:
        logger.info(f"\n{'─' * 60}")
        logger.info(f"Datei: {r.filename}")
        if r.error:
            logger.error(f"Fehler: {r.error}")
            continue
        if not r.found:
            logger.warning("Kein Treffer in StashApp")
            continue
        s = r.best
        logger.success(f"Treffer ({s.matched_by}, confidence {s.confidence:.2f})")
        logger.info(f"  Titel:     {s.title}")
        logger.info(f"  Datum:     {s.date or '—'}")
        logger.info(f"  Studio:    {s.studio or '—'}")
        if s.performers:
            logger.info(f"  Performer: {', '.join(s.performers)}")
