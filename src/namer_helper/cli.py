"""
namer-helper CLI entry point.
"""

from __future__ import annotations

from pathlib import Path

import click
from loguru import logger

from namer_helper.namer_bridge.config_reader import read_namer_paths
from namer_helper.namer_bridge.log_parser import collect_failed_matches
from namer_helper.reports.renderer import render_report


@click.group()
@click.version_option()
def main() -> None:
    """Namer Helper — sidecar tools for Namer."""


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
def report_cmd(namer_config: str, failed_dir: str | None, output_dir: str, fmt: str) -> None:
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

    written = render_report(matches, Path(output_dir), fmt=fmt)
    for p in written:
        logger.success(f"Report geschrieben: {p}")
