FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# cron is used by the optional scraper scheduler service.
RUN apt-get update \
    && apt-get install -y --no-install-recommends cron curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN pip install --upgrade pip setuptools wheel \
    && pip install .

# Install Chromium and its runtime dependencies for Playwright.
RUN playwright install --with-deps chromium

COPY docker /app/docker

RUN addgroup --system app && adduser --system --ingroup app app \
    && chmod +x /app/docker/*.sh \
    && mkdir -p /app/data \
    && chown -R app:app /app /ms-playwright

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "tenderscraper.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
