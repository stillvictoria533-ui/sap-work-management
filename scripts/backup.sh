#!/bin/sh
# Consistent database + attachment backup. Briefly pauses writes.
set -eu
cd "$(dirname "$0")/.."
umask 077
stamp=$(date -u +%Y%m%dT%H%M%SZ)
target="backups/$stamp.incomplete"
mkdir -p "$target"
compose() { docker compose --env-file .env.production "$@"; }
trap 'compose start api worker chat-worker >/dev/null' EXIT INT TERM
compose stop api worker chat-worker
compose exec -T db pg_dump -U postgres -d sapdesk -Fc > "$target/database.dump"
compose run --rm -T --no-deps --entrypoint tar api -C /app/private-media -czf - . > "$target/attachments.tar.gz"
test -s "$target/database.dump"
test -s "$target/attachments.tar.gz"
(cd "$target" && sha256sum database.dump attachments.tar.gz > SHA256SUMS)
mv "$target" "backups/$stamp"
printf 'Backup completed: %s\n' "backups/$stamp"
