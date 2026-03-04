# TenderScraper

## Docker quick start

1. Copy env template:
   ```bash
   cp .env.example .env
   ```
2. Fill `.env` (at least `POPTAVEJ_USERNAME` and `POPTAVEJ_PASSWORD` if you use `poptavej` source).
3. Start API:
   ```bash
   docker compose up --build
   ```
4. Open API docs:
   - http://localhost:8000/docs
   - healthcheck: http://localhost:8000/health

## Run scraper manually (one run, exits)

Build image once, then:

```bash
docker compose --profile tools run --rm scraper
```

This runs `/app/docker/run-scraper.sh`, which executes:
- `tenderscraper ingest --source <source> --limit <n> [--download-docs]`

Sources and behavior are controlled with env vars:
- `SCRAPER_SOURCES` (comma-separated, default `tender_arena,poptavej`)
- `SCRAPER_LIMIT` (default `50`)
- `SCRAPER_DOWNLOAD_DOCS` (`true`/`false`, default `true`)

## Optional cron scheduler container

Enable scheduler profile:

```bash
docker compose --profile cron up -d scraper-cron
```

Default schedule (twice daily):
- `SCRAPER_CRON=0 6,18 * * *`

Other examples:
- `0 */12 * * *` (every 12 hours)
- `30 2,14 * * *` (02:30 and 14:30 daily)

Run a single source ad hoc:

```bash
docker compose --profile tools run --rm -e SCRAPER_SOURCES=ted -e SCRAPER_LIMIT=1 -e SCRAPER_DOWNLOAD_DOCS=false scraper
```

## Data persistence

In local compose (`docker-compose.yml`):
- host `./data` is mounted to container `/app/data`
- metadata JSON and downloaded attachments are both persisted there

Structure:
- `./data/tenders/source=<source>/tender=<id>/meta.json`
- `./data/tenders/source=<source>/tender=<id>/raw/*`
- `./data/auth/poptavej_state.json`

## Prod-like compose override

Use named volume (no bind mount):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

This stores data in Docker volume `tender_data`.

## Required env vars

- `POPTAVEJ_USERNAME` (required for authenticated poptavej downloads)
- `POPTAVEJ_PASSWORD` (required for authenticated poptavej downloads)

Optional:
- `POPTAVEJ_STORAGE_STATE` (default `./data/auth/poptavej_state.json`)
- `SCRAPER_SOURCES`, `SCRAPER_LIMIT`, `SCRAPER_DOWNLOAD_DOCS`, `SCRAPER_CRON`
- `DATA_DIR`, `TENDERS_DIR`, `SQLITE_PATH`

## VPS runbook (Docker Compose)

1. Install Docker Engine + Docker Compose plugin on the VPS.
2. Copy project to server and create `.env` from `.env.example`.
3. Set real credentials in `.env` and keep it out of git.
4. Start API: `docker compose up -d --build api`.
5. Start scheduler: `docker compose --profile cron up -d scraper-cron`.
6. Verify API health: `curl http://localhost:8000/health`.
7. Verify scraper logs: `docker compose logs -f scraper-cron`.
8. Optional: switch to named volume with `-f docker-compose.prod.yml`.
