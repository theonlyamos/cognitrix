# syntax=docker/dockerfile:1

# --- Frontend build (Node) ---------------------------------------------------
FROM node:22-slim AS frontend
# Pin pnpm to the 10 line (which owns lockfileVersion 9.0, matching
# pnpm-lock.yaml). Node 22 LTS is required because corepack's unpinned "latest"
# pnpm now needs Node >= 22.13; pinning also stops a future pnpm bump from
# breaking this build again.
RUN corepack enable && corepack prepare pnpm@latest-10 --activate
WORKDIR /frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
# vite.config reads the app version from the repo-root pyproject.toml, which
# sits at ../ of this /frontend workdir — make it available in this stage.
COPY pyproject.toml /pyproject.toml
RUN pnpm run build          # outputs /frontend/dist

# --- Runtime (Python) --------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

# The app runs headless: the pyautogui-backed screen tools import their GUI deps
# lazily (only when such a tool is actually invoked), so no X server is needed to
# start the web app or the worker. libgl1/libglib2.0-0 cover shared-lib loads
# from the pillow/vision deps; curl is used by the compose/fly health check.
# Supervisor keeps the web process and solo Celery worker in one Fly machine.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 curl ca-certificates ffmpeg \
        supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
# The API serves the SPA from <repo>/frontend/dist (FRONTEND_BUILD_DIR); drop
# the freshly built assets there.
COPY --from=frontend /frontend/dist /app/frontend/dist

# Editable install keeps the package at /app/cognitrix, so its relative path to
# ../frontend/dist resolves at runtime (a site-packages install would not).
RUN pip install -e .

COPY deploy/supervisord.conf /etc/supervisor/cognitrix.conf

# Run the app as an unprivileged user. The entry script starts as root just
# to hand the data volume to that user, then drops to it. Under supervisord
# the web and worker programs run as that user (see deploy/supervisord.conf).
RUN groupadd --system --gid 10001 cognitrix \
    && useradd --system --uid 10001 --gid cognitrix --create-home --home-dir /home/cognitrix cognitrix \
    && mkdir -p /home/cognitrix/.cognitrix \
    && chown -R cognitrix:cognitrix /home/cognitrix \
    && chmod +x /app/deploy/entrypoint.sh
ENV HOME=/home/cognitrix
ENTRYPOINT ["/app/deploy/entrypoint.sh"]

EXPOSE 8000

# Fly supplies the external Redis URLs through secrets. Compose overrides CMD
# for its web/worker services and supplies its own Redis container.
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/cognitrix.conf"]
