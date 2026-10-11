#!/bin/sh
set -eu

mkdir -p /tmp/www
{ cat /etc/app/message.txt; printf '\n'; hostname; } > /tmp/www/index.html
exec httpd -f -p 8080 -h /tmp/www
