from dotenv import load_dotenv
load_dotenv()

import typer
from tenderscraper.config import settings
from tenderscraper.connectors.registry import get_connector
from tenderscraper.db import create_db_and_tables
from tenderscraper.ingestion.orchestrator import ingest_all
from tenderscraper.repository import get_db_stats

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """TenderScraper CLI."""
    # This runs before any command. Keep it empty.
    pass

@app.command()
def ingest(
    source: str = typer.Option(...),
    limit: int = typer.Option(10, min=1, max=200),
    download_docs: bool = typer.Option(False, "--download-docs", help="Download documents and update Postgres metadata"),
) -> None:
    settings.ensure_dirs()
    create_db_and_tables()
    connector = get_connector(source)
    tenders = connector.fetch(limit=limit)

    tender_refs = ingest_all(tenders=tenders)
    typer.echo(f"Ingested {len(tender_refs)} tenders")

    if download_docs:
        from tenderscraper.ingestion.orchestrator import download_docs_for_ingested_tenders

        download_docs_for_ingested_tenders(tender_refs)
        typer.echo("Downloaded documents and updated database metadata")

@app.command()
def mock_ingest(source: str = typer.Option(..., help="Connector source key, e.g. ted")) -> None:
    """Ingest mock tenders into data/ folder."""
    settings.ensure_dirs()
    create_db_and_tables()
    connector = get_connector(source)
    tenders = connector.fetch(query=None, limit=10)

    tender_refs = ingest_all(tenders=tenders)
    typer.echo(f"Ingested {len(tender_refs)} tenders")
    for source, tender_id in tender_refs:
        typer.echo(f" - {source}/{tender_id}")


@app.command()
def list_sources() -> None:
    """List available connector sources."""
    from tenderscraper.connectors.registry import CONNECTORS

    for k in sorted(CONNECTORS):
        typer.echo(k)


@app.command()
def hello() -> None:
    """Sanity check."""
    typer.echo("tenderscraper OK")

@app.command()
def init() -> None:
    """Create required local folders and initialize storage."""
    settings.ensure_dirs()
    create_db_and_tables()
    typer.echo(f"scratch_dir: {settings.scratch_dir.resolve()}")
    typer.echo(f"database_url: {settings.normalized_database_url}")
    typer.echo(f"storage_backend: {settings.storage_backend}")


@app.command("db-stats")
def db_stats() -> None:
    """Show simple database stats for verification."""
    create_db_and_tables()
    stats = get_db_stats()
    typer.echo(f"database_url: {settings.normalized_database_url}")
    typer.echo(f"total_tenders: {stats['total_tenders']}")
    typer.echo(f"tenders_with_documents: {stats['tenders_with_documents']}")
    typer.echo(f"documents_total: {stats['documents_total']}")
    for source, count in stats["by_source"].items():
        typer.echo(f"source[{source}]: {count}")
