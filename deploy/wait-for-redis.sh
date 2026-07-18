#!/bin/sh
set -eu

attempt=0
max_attempts="${REDIS_STARTUP_ATTEMPTS:-30}"

until [ "$(redis-cli -h 127.0.0.1 ping 2>/dev/null || true)" = "PONG" ]; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge "$max_attempts" ]; then
        echo "Redis did not become ready after ${max_attempts} attempts" >&2
        exit 1
    fi
    sleep 1
done

exec "$@"
