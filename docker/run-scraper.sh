#!/bin/sh
set -eu

SOURCES="${SCRAPER_SOURCES:-tender_arena,poptavej}"
LIMIT="${SCRAPER_LIMIT:-200}"
DOWNLOAD_DOCS="${SCRAPER_DOWNLOAD_DOCS:-true}"
FAIL_FAST="${SCRAPER_FAIL_FAST:-false}"
FAILURES=0

OLD_IFS="$IFS"
IFS=","
for source in $SOURCES; do
  [ -z "$source" ] && continue
  if [ "$DOWNLOAD_DOCS" = "true" ]; then
    if ! tenderscraper ingest --source "$source" --limit "$LIMIT" --download-docs; then
      echo "Source '$source' failed (limit=$LIMIT)" >&2
      FAILURES=$((FAILURES + 1))
      if [ "$FAIL_FAST" = "true" ]; then
        exit 1
      fi
    fi
  else
    if ! tenderscraper ingest --source "$source" --limit "$LIMIT"; then
      echo "Source '$source' failed (limit=$LIMIT)" >&2
      FAILURES=$((FAILURES + 1))
      if [ "$FAIL_FAST" = "true" ]; then
        exit 1
      fi
    fi
  fi
done
IFS="$OLD_IFS"

if [ "$FAILURES" -gt 0 ]; then
  exit 1
fi
