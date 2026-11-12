#!/bin/sh
# Shared services for every desktop in this container, then the supervisor.
set -e
STATE="${COGNITRIX_SANDBOX_STATE:-/tmp/cognitrix-sandbox}"
mkdir -p "$STATE/tokens" "$HOME/.profiles"

# One session bus and one accessibility bus, shared by all desktops: the
# driver matches windows to their accessible apps by process id.
eval "$(dbus-launch --sh-syntax)"
export DBUS_SESSION_BUS_ADDRESS
/usr/libexec/at-spi-bus-launcher --launch-immediately >/dev/null 2>&1 &
sleep 1
dbus-send --session --print-reply --dest=org.a11y.Bus /org/a11y/bus \
    org.freedesktop.DBus.Properties.Set string:org.a11y.Status string:IsEnabled variant:boolean:true \
    >/dev/null 2>&1 || true

# Live screens: one websocket endpoint, each desktop picked by ?token=<name>.
websockify --web /usr/share/novnc --token-plugin TokenFile --token-source "$STATE/tokens" 6080 \
    >/dev/null 2>&1 &

exec python3 -m cognitrix.computer.sandbox.supervisor
