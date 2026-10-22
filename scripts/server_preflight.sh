#!/bin/sh
# Read-only checks. Does not install packages, stop services or expose secrets.
printf '=== System ===\n'
cat /etc/os-release
uname -m
id
printf '\n=== Capacity ===\n'
getconf _NPROCESSORS_ONLN
free -h
df -h / /opt 2>/dev/null
printf '\n=== Docker ===\n'
if command -v docker >/dev/null 2>&1; then
    docker --version
    docker compose version
    docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
else
    printf 'Docker is not installed.\n'
fi
printf '\n=== TLS tools ===\n'
python3 --version
openssl version
printf '\n=== Port 80 / 443 ownership ===\n'
ss -lntp '( sport = :80 or sport = :443 )'
printf '\n=== Existing target ===\n'
if [ -d /opt/sapdesk ]; then
    printf '/opt/sapdesk already exists. Inspect before extracting or changing it.\n'
else
    printf '/opt/sapdesk does not exist.\n'
fi
