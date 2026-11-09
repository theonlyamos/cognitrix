#!/bin/sh
# Start Cognitrix as the unprivileged "cognitrix" user.
#
# The container starts as root only long enough to make the data folder
# belong to that user (a volume created by an older image is owned by root),
# then drops to it for good (under supervisord, each program is started as that user). Started with --user, it skips straight to the
# command.
set -e

DATA="$HOME/.cognitrix"
LEGACY=/root/.cognitrix
# Where agents' file and shell tools work. The app's own folder (/app) is
# read-only for the unprivileged user, so they get a folder on the data volume.
export COGNITRIX_TOOLS_ROOT="${COGNITRIX_TOOLS_ROOT:-$DATA/workspace}"

if [ "$(id -u)" = "0" ]; then
    # Older setups mount the data volume at /root/.cognitrix. Keep using it
    # from there, so nothing has to be moved or re-mounted.
    if [ ! -L "$DATA" ] && [ -n "$(ls -A "$LEGACY" 2>/dev/null)" ] && [ -z "$(ls -A "$DATA" 2>/dev/null)" ]; then
        rmdir "$DATA" 2>/dev/null || true
        chmod 711 /root
        ln -s "$LEGACY" "$DATA"
    fi
    mkdir -p "$DATA/workspace"
    TARGET="$(readlink -f "$DATA")"
    # Only walk the folder when something in it isn't ours yet.
    if [ -n "$(find "$TARGET" ! -user cognitrix -print -quit 2>/dev/null)" ]; then
        chown -R cognitrix:cognitrix "$TARGET"
    fi
    # supervisord keeps root for itself (it has to, to write its programs'
    # output to the container log) and starts each program as "cognitrix".
    case "$1" in
        *supervisord) exec "$@" ;;
    esac
    exec setpriv --reuid=cognitrix --regid=cognitrix --init-groups "$@"
fi

mkdir -p "$COGNITRIX_TOOLS_ROOT" 2>/dev/null || true
exec "$@"
