#!/bin/sh
set -eu

# Fly mounts the volume after the image is built, so prepare Redis' persistent
# directory at container start and keep it private to the redis service user.
install -d -o redis -g redis -m 700 /root/.cognitrix/redis

exec "$@"
