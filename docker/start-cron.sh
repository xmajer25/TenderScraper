#!/bin/sh
set -eu

CRON_EXPR="${SCRAPER_CRON:-0 6,18 * * *}"

cat >/etc/cron.d/tenderscraper <<EOF
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
$CRON_EXPR root /app/docker/run-scraper.sh >> /var/log/cron.log 2>&1
EOF

chmod 0644 /etc/cron.d/tenderscraper
touch /var/log/cron.log

echo "Configured SCRAPER_CRON=$CRON_EXPR"

cron
tail -f /var/log/cron.log
