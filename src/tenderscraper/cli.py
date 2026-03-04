from dotenv import load_dotenv
load_dotenv()

import typer
from tenderscraper.config import settings
from tenderscraper.connectors.registry import get_connector
from tenderscraper.ingestion.orchestrator import ingest_all

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
    download_docs: bool = typer.Option(False, "--download-docs", help="Download documents into raw/ and update meta.json"),
) -> None:
    settings.ensure_dirs()
    connector = get_connector(source)
    tenders = connector.fetch(limit=limit)

    paths = ingest_all(tenders_dir=settings.tenders_dir, tenders=tenders)
    typer.echo(f"Ingested {len(paths)} tenders")

    if download_docs:
        from tenderscraper.ingestion.orchestrator import download_docs_for_ingested_tenders

        download_docs_for_ingested_tenders(paths)
        typer.echo("Downloaded documents and updated meta.json")

@app.command()
def mock_ingest(source: str = typer.Option(..., help="Connector source key, e.g. ted")) -> None:
    """Ingest mock tenders into data/ folder."""
    settings.ensure_dirs()
    connector = get_connector(source)
    tenders = connector.fetch(query=None, limit=10)

    paths = ingest_all(tenders_dir=settings.tenders_dir, tenders=tenders)
    typer.echo(f"Ingested {len(paths)} tenders into: {settings.tenders_dir.resolve()}")
    for p in paths:
        typer.echo(f" - {p}")


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
    typer.echo(f"data_dir: {settings.data_dir.resolve()}")
    typer.echo(f"tenders_dir: {settings.tenders_dir.resolve()}")
    typer.echo(f"sqlite_path: {settings.sqlite_path.resolve()}")
