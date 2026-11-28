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

# Sound: one PulseAudio server; each desktop gets its own speaker and
# microphone from the supervisor (cognitrix/computer/sandbox/audio.py), so an
# agent can sit in a call in the sandbox browser.
# Its socket takes every user, so agents with their own Linux user get sound
# on their own desktop's devices too.
if command -v pulseaudio >/dev/null 2>&1; then
    mkdir -p /tmp/cognitrix-pulse && chmod 755 /tmp/cognitrix-pulse
    export PULSE_SERVER=unix:/tmp/cognitrix-pulse/native
    # No pid file: after a restart of the container the one left behind
    # names the process id the new server gets again, so it took itself for
    # an already running server and quit, and no desktop had sound.
    rm -f /tmp/cognitrix-pulse/native
    pulseaudio --daemonize=yes --use-pid-file=no --exit-idle-time=-1 --disallow-exit -n \
        --load="module-native-protocol-unix auth-anonymous=1 socket=/tmp/cognitrix-pulse/native" \
        --load=module-always-sink >/dev/null 2>&1 || true
fi

# Live screens: one websocket endpoint, each desktop picked by ?token=<name>.
# On Fly the engine connects over the private IPv6 network ('[::]:6080').
websockify --web /usr/share/novnc --token-plugin TokenFile --token-source "$STATE/tokens" \
    "${COGNITRIX_SANDBOX_VIEWER_LISTEN:-6080}" \
    >/dev/null 2>&1 &

exec python3 -m cognitrix.computer.sandbox.supervisor
