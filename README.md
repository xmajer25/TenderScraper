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

Use `SCRAPER_LIMIT=0` for an unlimited run, meaning "fetch everything the connector can currently enumerate".

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

Preferred deploy path:
- Create the stack from the repo as a Blueprint. Render will read [render.yaml](c:/Users/jakub/OneDrive/Desktop/MetaIT/TenderScraper/render.yaml) and create all 3 resources together: `tenderscraper-db`, `tenderscraper-api`, and `tenderscraper-scraper`.
- If you create only a single Web Service manually in the Render Dashboard, `render.yaml` is not applied to that service automatically.

Manual Render setup if you do not use Blueprint:
1. Create a Postgres database named `tenderscraper-db`.
2. Create a Web Service from this repo using the `Dockerfile`.
3. Create a Cron Job from this repo using the same `Dockerfile`.

Manual service settings:
- Web Service start command: `sh -c "uvicorn tenderscraper.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"`
- Cron Job start command: `/app/docker/run-scraper.sh`
- Web Service `DATABASE_URL`: set it to the Postgres Internal Database URL from `tenderscraper-db`
- Cron Job `DATABASE_URL`: set it to the same Postgres Internal Database URL
- Web Service and Cron Job both need the same S3 settings and any source credentials such as `POPTAVEJ_USERNAME` and `POPTAVEJ_PASSWORD`
- Cron Job also needs its scraper settings such as `SCRAPER_SOURCES`, `SCRAPER_LIMIT`, and `SCRAPER_DOWNLOAD_DOCS`

Important:
- Do not set `DATABASE_URL` on Render to `postgresql+psycopg://postgres:postgres@postgres:5432/tenderscraper`. That hostname only works inside local Docker Compose.
- The API and cron job are separate Render runtimes. They do not share durable local files, so attachments must go to S3-compatible storage.

For file storage, point the S3 settings at a provider such as Cloudflare R2, AWS S3, or Backblaze B2 S3-compatible API.

Recommended Render settings:
- `STORAGE_BACKEND=s3`
- `SCRATCH_DIR=/tmp/tenderscraper`

## Verify Postgres

From the app container:

```bash
docker compose exec api tenderscraper db-stats
```

Reset app tables in the configured database:

```bash
tenderscraper db-reset --yes
```

Unlimited ingest examples:

```bash
tenderscraper ingest --source poptavej --limit 0 --download-docs
tenderscraper ingest --source tender_arena --limit 0 --download-docs
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
