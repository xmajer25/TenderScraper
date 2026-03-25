from dotenv import load_dotenv
load_dotenv()

import typer
from tenderscraper.config import settings
from tenderscraper.connectors.registry import get_connector
from tenderscraper.db import create_db_and_tables, reset_db
from tenderscraper.ingestion.orchestrator import download_docs_for_ingested_tenders, ingest_all
from tenderscraper.repository import get_db_stats, get_tender_meta, list_tender_refs, upsert_tender_meta

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """TenderScraper CLI."""
    # This runs before any command. Keep it empty.
    pass

@app.command()
def ingest(
    source: str = typer.Option(...),
    limit: int = typer.Option(10, min=0),
    download_docs: bool = typer.Option(False, "--download-docs", help="Download documents and update Postgres metadata"),
) -> None:
    settings.ensure_dirs()
    create_db_and_tables()
    connector = get_connector(source)
    tenders = connector.fetch(limit=None if limit == 0 else limit)

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


@app.command("db-reset")
def db_reset(
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Drop and recreate all application tables in DATABASE_URL.",
    ),
) -> None:
    """Drop and recreate application tables in the configured database."""
    if not yes:
        raise typer.BadParameter("Refusing to reset the database without --yes.")

    reset_db()
    typer.echo(f"database_url: {settings.normalized_database_url}")
    typer.echo("Application tables were dropped and recreated.")


@app.command("backfill-poptavej-deadlines")
def backfill_poptavej_deadlines(
    limit: int = typer.Option(
        0,
        min=0,
        help="How many listing items to scan. Use 0 for all currently available listing pages.",
    ),
) -> None:
    """Refresh only submission_deadline_at for existing poptavej tenders from listing pages."""
    from tenderscraper.scraping.sources.poptavej import PoptavejScraper

    settings.ensure_dirs()
    create_db_and_tables()

    scraper = PoptavejScraper()
    items = scraper.fetch_listing(limit=None if limit == 0 else limit, headless=True, timeout_ms=30_000)

    scanned = 0
    updated = 0
    missing = 0
    detail_fallback_updates = 0

    for item in items:
        scanned += 1
        meta = get_tender_meta("poptavej", item.source_tender_id)
        if not meta:
            continue
        deadline = item.closing_at
        if deadline is None:
            try:
                detail = scraper.fetch_detail(
                    notice_url=item.notice_url,
                    storage_state_path=None,
                    headless=True,
                    timeout_ms=30_000,
                )
                deadline = detail.submission_deadline_at
                if deadline is not None:
                    detail_fallback_updates += 1
            except Exception:
                deadline = None

        if deadline is None:
            missing += 1
            continue

        meta["submission_deadline_at"] = deadline.isoformat()
        upsert_tender_meta(meta)
        updated += 1

    typer.echo(f"scanned: {scanned}")
    typer.echo(f"updated: {updated}")
    typer.echo(f"detail_fallback_updates: {detail_fallback_updates}")
    typer.echo(f"missing_deadline_in_listing: {missing}")


@app.command("backfill-document-storage")
def backfill_document_storage(
    source: str = typer.Option(..., help="Connector source key, e.g. tender_arena or poptavej"),
    limit: int = typer.Option(
        0,
        min=0,
        help="How many existing tenders to process. Use 0 for all tenders of the source.",
    ),
) -> None:
    """Backfill storage_key/storage_url/download_url for existing tenders by downloading missing documents."""
    settings.ensure_dirs()
    create_db_and_tables()

    tender_refs = list_tender_refs(source=source, limit=None if limit == 0 else limit)
    if not tender_refs:
        typer.echo("No matching tenders found.")
        raise typer.Exit(code=0)

    download_docs_for_ingested_tenders(tender_refs)
    typer.echo(f"processed_tenders: {len(tender_refs)}")
    typer.echo(f"source: {source}")


@app.command("backfill-storage-urls")
def backfill_storage_urls(
    source: str = typer.Option(..., help="Connector source key, e.g. tender_arena or poptavej"),
    limit: int = typer.Option(
        0,
        min=0,
        help="How many existing tenders to process. Use 0 for all tenders of the source.",
    ),
) -> None:
    """Recompute storage_url/download_url from existing storage_key values without scraping or downloading."""
    settings.ensure_dirs()
    create_db_and_tables()

    tender_refs = list_tender_refs(source=source, limit=None if limit == 0 else limit)
    if not tender_refs:
        typer.echo("No matching tenders found.")
        raise typer.Exit(code=0)

    updated_tenders = 0
    updated_documents = 0

    for ref_source, tender_id in tender_refs:
        meta = get_tender_meta(ref_source, tender_id)
        if not meta:
            continue

        changed = False
        for document in meta.get("documents") or []:
            storage_key = (document.get("storage_key") or "").strip()
            if not storage_key:
                continue

            public_url = settings.public_object_url(storage_key)
            if not public_url:
                continue

            if not document.get("source_url") and document.get("url"):
                document["source_url"] = document.get("url")
                changed = True

            if document.get("storage_url") != public_url:
                document["storage_url"] = public_url
                changed = True
                updated_documents += 1

            if document.get("download_url") != public_url:
                document["download_url"] = public_url
                changed = True

        if changed:
            upsert_tender_meta(meta)
            updated_tenders += 1

    typer.echo(f"source: {source}")
    typer.echo(f"processed_tenders: {len(tender_refs)}")
    typer.echo(f"updated_tenders: {updated_tenders}")
    typer.echo(f"updated_documents: {updated_documents}")
