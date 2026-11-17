# The agents' sandboxed computer: a Linux desktop the agent drives with the
# Cognitrix driver, and that a person can watch and take over.
#
#   docker build -f sandbox/desktop/Dockerfile -t cognitrix-desktop .
#
# The engine starts one container per user (see cognitrix/computer/sandbox/
# client.py) and one desktop per agent inside it. Only cognitrix/computer is
# copied in: the engine, its keys and its database stay outside.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONPATH=/opt/cognitrix-sandbox \
    PYTHONDONTWRITEBYTECODE=1 \
    ACCESSIBILITY_ENABLED=1 \
    GNOME_ACCESSIBILITY=1 \
    QT_ACCESSIBILITY=1 \
    QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb xauth openbox x11vnc novnc websockify x11-xserver-utils \
        dbus dbus-x11 at-spi2-core libatspi2.0-0 libgtk-3-bin \
        libxtst6 libxss1 libxcomposite1 \
        chromium firefox-esr \
        pulseaudio pulseaudio-utils ffmpeg \
        python3 python3-pil python3-xlib \
        python3-venv python3-pip python3-dev sqlite3 git curl \
        fonts-dejavu-core fonts-liberation xdg-utils ca-certificates tini procps \
        xterm build-essential pkg-config ripgrep jq less zip unzip file openssh-client gnupg \
    && rm -rf /var/lib/apt/lists/*

# Coding work (cognitrix/computer/sandbox/workspace.py): Node 22 with pnpm and
# yarn, uv for Python, and the coding agent CLIs the Coding Agent tool runs
# here (each signs in with the person's API key, passed per run; Antigravity
# CLI uses the Gemini key, see AGY_SANDBOX_SETUP in cognitrix/common/sandbox_work.py). Other
# toolchains (Go, Rust, Java) install into the home folder without root and
# stay in the home volume.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && corepack enable \
    && npm install -g --no-fund --no-audit @anthropic-ai/claude-code @openai/codex @google/gemini-cli \
    && npm cache clean --force \
    && curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh \
    && curl -fsSL https://antigravity.google/cli/install.sh | bash -s -- --dir /usr/local/bin \
    && rm -rf /root/.cache/antigravity \
    && test -x /usr/local/bin/agy
ENV DISABLE_AUTOUPDATER=1 \
    NPM_CONFIG_PREFIX=/home/agent/.npm-global \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

# Browsers start with accessibility on (the driver reads pages through it) and
# with a profile per desktop, because one profile can't be open on two
# desktops at once. The container itself is the sandbox, so Chromium's own
# sandbox (which needs privileges the container doesn't get) is off.
COPY sandbox/desktop/bin/ /usr/local/bin/
COPY sandbox/desktop/applications/ /usr/local/share/applications/
# Firefox without first-run pages, telemetry prompts or captive-portal checks.
COPY sandbox/desktop/firefox/policies.json /usr/lib/firefox-esr/distribution/policies.json
# Chromium without the "unsupported command-line flag: --no-sandbox" bar
# across the top of every window (the container is the sandbox, see above).
COPY sandbox/desktop/chromium/policies.json /etc/chromium/policies/managed/cognitrix.json
# Every app window opens maximized, so the screen people watch is the app and
# not a small window in the corner of an empty desktop. The build fails if
# openbox's default config no longer has the spot this rule goes in.
RUN sed -i 's#^</applications>#  <application class="*" type="normal"><maximized>yes</maximized></application>\n</applications>#' \
        /etc/xdg/openbox/rc.xml \
    && grep -q '<maximized>yes</maximized></application>' /etc/xdg/openbox/rc.xml
COPY sandbox/desktop/entrypoint.sh /usr/local/bin/cognitrix-sandbox
COPY cognitrix/computer/ /opt/cognitrix-sandbox/cognitrix/computer/
RUN chmod 755 /usr/local/bin/* \
    && touch /opt/cognitrix-sandbox/cognitrix/__init__.py \
    && rm -rf /opt/cognitrix-sandbox/cognitrix/computer/__pycache__ \
       /opt/cognitrix-sandbox/cognitrix/computer/sandbox/__pycache__ \
    && useradd --create-home --shell /bin/bash agent

# A Linux user per agent (cognitrix/computer/sandbox/users.py): each in its
# own group, which 'agent' (the supervisor) is also in. Their home folders
# are set per agent at run time (~/work/<agent>).
RUN for i in $(seq -w 1 64); do \
        groupadd "cgx$i" \
        && useradd --no-create-home --home-dir /nonexistent --shell /bin/bash -g "cgx$i" "cgx$i" \
        && usermod -aG "cgx$i" agent || exit 1; \
    done

USER agent
WORKDIR /home/agent
# Files, downloads, browser profiles, Apps (~/.cognitrix-apps) and the agents'
# coding folders (~/work/<agent>) live here; mount a volume to keep them.
VOLUME /home/agent

EXPOSE 8790 6080
# Tells the engine to start this image as root with the capabilities for a
# Linux user per agent (cognitrix/computer/sandbox/client.py).
LABEL cognitrix.agent-users="1"
# Started as root (with only the capabilities it needs to give each agent
# its own user), it drops to 'agent' at once; see bin/cognitrix-sandbox-root.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/cognitrix-sandbox-root"]
