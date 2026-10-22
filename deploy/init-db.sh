#!/bin/sh
set -eu
psql --username postgres --dbname postgres --set ON_ERROR_STOP=1 --set app_password="$POSTGRES_APP_PASSWORD" --file /opt/init-db.sql
