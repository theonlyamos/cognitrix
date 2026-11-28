# The agents' sandboxed computer: a Linux desktop the agent drives with the
# Cognitrix driver, and that a person can watch and take over.
#
#   docker build -f sandbox/desktop/Dockerfile -t cognitrix-desktop .
#
# The engine starts one container per user (see cognitrix/computer/sandbox/
# client.py) and one desktop per agent inside it. Only cognitrix/computer is
# copied in: the engine, its keys and its database stay outside.
#
# Layer order matters: people's computers download only the layers that
# changed (see .github/workflows/sandbox-image.yml, which keeps a build
# cache). Big, rarely changing layers (system packages, coding CLIs, the CLI's
# dependencies) come first; anything that changes with every commit or
# version bump (the app's own code, COGNITRIX_VERSION) comes last, so a new
# version ships a few small layers instead of the whole image.
FROM debian:bookworm-slim AS base

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONPATH=/opt/cognitrix-sandbox \
    PYTHONDONTWRITEBYTECODE=1 \
    ACCESSIBILITY_ENABLED=1 \
    GNOME_ACCESSIBILITY=1 \
    QT_ACCESSIBILITY=1 \
    QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1

# Node 22 comes from NodeSource, and its repo is added before everything else
# is installed: noVNC's package depends on "nodejs", and without the repo apt
# satisfies that with Debian's Node 18 (about 50 MB, never used).
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends \
        xvfb xauth openbox x11vnc novnc websockify x11-xserver-utils \
        dbus dbus-x11 at-spi2-core libatspi2.0-0 libgtk-3-bin \
        libxtst6 libxss1 libxcomposite1 \
        chromium firefox-esr \
        pulseaudio pulseaudio-utils ffmpeg \
        python3 python3-pil python3-xlib \
        python3-venv python3-pip python3-dev sqlite3 git \
        fonts-dejavu-core fonts-liberation xdg-utils tini procps \
        xterm build-essential pkg-config ripgrep jq less zip unzip file openssh-client \
        iptables \
        nodejs \
    && test "$(dpkg-query -W -f='${Version}' nodejs | cut -d. -f1)" -ge 22 \
    && ! dpkg -s libnode108 >/dev/null 2>&1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Tailscale, for a computer on Fly that joins its owner's tailnet (Settings >
# Tailscale; cognitrix/tailscale/computers.py). Started by the root start
# script only when the engine asks for it; never in local Docker. iptables
# above is what it sets its own network rules with.
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscaled /usr/local/bin/tailscaled
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscale /usr/local/bin/tailscale

# The CLI's dependency list (see install.sh), worked out in a throwaway stage:
# only the list is copied into the image, so its install layer below is
# reused until a dependency changes, not on every version bump.
FROM base AS cli-requirements
RUN --mount=type=bind,source=pyproject.toml,target=/tmp/cognitrix-src/pyproject.toml,rw \
    --mount=type=bind,source=README.md,target=/tmp/cognitrix-src/README.md,rw \
    --mount=type=bind,source=cognitrix,target=/tmp/cognitrix-src/cognitrix,rw \
    --mount=type=bind,source=sandbox/desktop/cognitrix-cli/install.sh,target=/tmp/install.sh \
    sh /tmp/install.sh requirements /tmp/cognitrix-src /cognitrix-requirements.txt

FROM base
# Coding work (cognitrix/computer/sandbox/workspace.py): Node 22 with pnpm and
# yarn, uv for Python, and the coding agent CLIs the Coding Agent tool runs
# here (each signs in with the person's API key, passed per run; Antigravity
# CLI uses the Gemini key, see AGY_SANDBOX_SETUP in cognitrix/common/sandbox_work.py). Other
# toolchains (Go, Rust, Java) install into the home folder without root and
# stay in the home volume. Left out to keep the image small: npm's and
# node-gyp's download caches, and Codex's voice-mode helper (the sandbox has
# no microphone).
RUN corepack enable \
    && npm install -g --no-fund --no-audit @anthropic-ai/claude-code @openai/codex @google/gemini-cli \
    && rm -rf /usr/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-*/vendor/*/codex-resources/voice \
    && codex --version \
    && curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh \
    && curl -fsSL https://antigravity.google/cli/install.sh | bash -s -- --dir /usr/local/bin \
    && test -x /usr/local/bin/agy \
    && npm cache clean --force \
    && rm -rf /root/.cache /root/.npm /tmp/* /tmp/.[!.]*
ENV DISABLE_AUTOUPDATER=1 \
    NPM_CONFIG_PREFIX=/home/agent/.npm-global \
    PIP_BREAK_SYSTEM_PACKAGES=1 \
    PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

# Every app window opens maximized, so the screen people watch is the app and
# not a small window in the corner of an empty desktop. The build fails if
# openbox's default config no longer has the spot this rule goes in.
RUN sed -i 's#^</applications>#  <application class="*" type="normal"><maximized>yes</maximized></application>\n</applications>#' \
        /etc/xdg/openbox/rc.xml \
    && grep -q '<maximized>yes</maximized></application>' /etc/xdg/openbox/rc.xml

# 'agent' runs the desktops. A Linux user per agent
# (cognitrix/computer/sandbox/users.py): each in its own group, which 'agent'
# (the supervisor) is also in. Their home folders are set per agent at run
# time (~/work/<agent>).
RUN useradd --create-home --shell /bin/bash agent \
    && for i in $(seq -w 1 64); do \
        groupadd "cgx$i" \
        && useradd --no-create-home --home-dir /nonexistent --shell /bin/bash -g "cgx$i" "cgx$i" \
        && usermod -aG "cgx$i" agent || exit 1; \
    done

# Browsers start with accessibility on (the driver reads pages through it) and
# with a profile per desktop, because one profile can't be open on two
# desktops at once. The container itself is the sandbox, so Chromium's own
# sandbox (which needs privileges the container doesn't get) is off.
# (--chmod, not a later chmod over /usr/local/bin, which would copy uv and agy
# into another layer.)
COPY --chmod=755 sandbox/desktop/bin/ /usr/local/bin/
COPY --chmod=755 sandbox/desktop/entrypoint.sh /usr/local/bin/cognitrix-sandbox
COPY sandbox/desktop/applications/ /usr/local/share/applications/
# Firefox without first-run pages, telemetry prompts or captive-portal checks.
COPY sandbox/desktop/firefox/policies.json /usr/lib/firefox-esr/distribution/policies.json
# Chromium without the "unsupported command-line flag: --no-sandbox" bar
# across the top of every window (the container is the sandbox, see above).
COPY sandbox/desktop/chromium/policies.json /etc/chromium/policies/managed/cognitrix.json

# The app's own CLI (`cognitrix`, and `cognitrix-python` for scripts that
# import the package), built from this same source so it matches the engine
# of this commit or release. Its own venv; long-term vector memory (chromadb)
# is left out to keep the image small. No keys or sign-ins are in the image: a
# command that runs it gets the person's model keys at run time, as
# placeholders the key proxy fills in (cognitrix_cli_env in
# cognitrix/common/sandbox_work.py).
# Its dependencies first (big, and cached while the list above is unchanged),
# then the package itself (small, rebuilt on every change). The source is
# mounted for that step only (not copied), so it isn't kept in a layer.
COPY --from=cli-requirements /cognitrix-requirements.txt /tmp/cognitrix-requirements.txt
RUN --mount=type=bind,source=sandbox/desktop/cognitrix-cli/install.sh,target=/tmp/install.sh \
    sh /tmp/install.sh deps /tmp/cognitrix-requirements.txt /opt/cognitrix-cli \
    && rm -rf /root/.cache /tmp/cognitrix-requirements.txt
RUN --mount=type=bind,source=pyproject.toml,target=/tmp/cognitrix-src/pyproject.toml,rw \
    --mount=type=bind,source=README.md,target=/tmp/cognitrix-src/README.md,rw \
    --mount=type=bind,source=cognitrix,target=/tmp/cognitrix-src/cognitrix,rw \
    --mount=type=bind,source=sandbox/desktop/cognitrix-cli,target=/tmp/cognitrix-cli,rw \
    sh /tmp/cognitrix-cli/install.sh package /tmp/cognitrix-src /opt/cognitrix-cli \
    && install -m 755 /tmp/cognitrix-cli/cognitrix /tmp/cognitrix-cli/cognitrix-python /usr/local/bin/ \
    && rm -rf /root/.cache

# The desktop supervisor (changes with most commits, so it comes last).
COPY cognitrix/computer/ /opt/cognitrix-sandbox/cognitrix/computer/
RUN touch /opt/cognitrix-sandbox/cognitrix/__init__.py \
    && rm -rf /opt/cognitrix-sandbox/cognitrix/computer/__pycache__ \
       /opt/cognitrix-sandbox/cognitrix/computer/sandbox/__pycache__
ARG COGNITRIX_VERSION=dev
LABEL cognitrix.cli-version="${COGNITRIX_VERSION}"

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
