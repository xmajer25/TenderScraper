#!/bin/sh
set -eu

SOURCES="${SCRAPER_SOURCES:-tender_arena,poptavej}"
LIMIT="${SCRAPER_LIMIT:-50}"
DOWNLOAD_DOCS="${SCRAPER_DOWNLOAD_DOCS:-true}"

OLD_IFS="$IFS"
IFS=","
for source in $SOURCES; do
  [ -z "$source" ] && continue
  if [ "$DOWNLOAD_DOCS" = "true" ]; then
    tenderscraper ingest --source "$source" --limit "$LIMIT" --download-docs
  else
    tenderscraper ingest --source "$source" --limit "$LIMIT"
  fi
done
IFS="$OLD_IFS"
