#!/bin/sh
set -eu
export PGHOST=database PGUSER=composer PGDATABASE=composer
export PGPASSWORD="$(cat /etc/app/password)"
until value=$(psql -Atqc 'SELECT value FROM acceptance WHERE id = 1' 2>/dev/null); do sleep 1; done
mkdir -p /tmp/www
{
    cat /etc/app/message.txt
    printf '\n'
    cat /app/version
    printf 'database: %s\n' "$value"
    hostname
} > /tmp/www/index.html
exec /usr/local/bin/busybox httpd -f -p 8080 -h /tmp/www
