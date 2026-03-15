# TenderScraper

Tender metadata is stored in Postgres. Attachment files are stored in S3-compatible object storage. The runtime does not depend on `data/tenders`.

Postgres can technically store binary files, but that is the wrong default here. `.pdf`, `.docx`, `.xlsx`, and similar raw tender files should live in object storage, while Postgres keeps searchable metadata and document references.

## Local Docker

Create env file:

```bash
cp .env.example .env
```

Start API + Postgres:

```bash
docker compose up --build
```

Services:
- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- Health: http://localhost:8000/health
- Postgres: `localhost:5432`

Local defaults:
- metadata DB: Postgres in Docker
- files: uploaded to object storage
- any local files are temporary scratch files under `SCRATCH_DIR`

## Run scraper once

```bash
docker compose --profile tools run --rm scraper
```

Ad hoc example:

```bash
docker compose --profile tools run --rm -e SCRAPER_SOURCES=ted -e SCRAPER_LIMIT=1 -e SCRAPER_DOWNLOAD_DOCS=false scraper
```

## Optional local cron container

```bash
docker compose --profile cron up -d scraper-cron
```

Default schedule:

```text
0 6,18 * * *
```

Examples:
- `0 */12 * * *`
- `30 2,14 * * *`

## Environment

Core:
- `DATABASE_URL`
- `SCRATCH_DIR`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POPTAVEJ_USERNAME`
- `POPTAVEJ_PASSWORD`

Storage:
- `STORAGE_BACKEND=s3`
- `S3_BUCKET`
- `S3_REGION`
- `S3_ENDPOINT_URL`
- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`
- `S3_PUBLIC_BASE_URL`
- `S3_PRESIGN_EXPIRY_S`

Scraper:
- `SCRAPER_SOURCES`
- `SCRAPER_LIMIT`
- `SCRAPER_DOWNLOAD_DOCS`
- `SCRAPER_CRON`
- `SCRAPER_FAIL_FAST`

## Render

Use:
- Render Postgres for metadata
- Render Web Service for the API
- Render Cron Job for the scraper
- External S3-compatible object storage for files

Do not rely on local disk on Render for attachments. The API service and cron scraper are separate runtimes, so shared durable file access is not the right boundary.

This repo includes [render.yaml](c:/Users/jakub/OneDrive/Desktop/MetaIT/TenderScraper/render.yaml) with:
- one web service
- one cron service
- one Postgres database

For file storage, point the S3 settings at a provider such as Cloudflare R2, AWS S3, or Backblaze B2 S3-compatible API.

Recommended Render settings:
- `STORAGE_BACKEND=s3`
- `SCRATCH_DIR=/tmp/tenderscraper`

## Verify Postgres

From the app container:

```bash
docker compose exec api tenderscraper db-stats
```

From Postgres directly:

```bash
docker compose exec postgres psql -U postgres -d tenderscraper -c "select source, count(*) from tenderrecord group by source order by source;"
```

If you want to verify that the runtime is using only Postgres + object storage, run:

```bash
docker compose --profile tools run --rm scraper
```

## Validation

Validated in this workspace:
- `docker compose config`
- `docker compose build` completed successfully before the later Docker daemon failure
- API health and `/docs` responded successfully against the built image

Not fully re-validated after the final Postgres/S3 code changes:
- end-to-end scraper run against the updated image
- Render deployment itself
