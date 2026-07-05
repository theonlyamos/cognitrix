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

# xvfb: the pyautogui-backed tools import Xlib against $DISPLAY at load time and
# crash headless without a virtual display (xvfb-run wraps the commands below).
# libgl1/libglib2.0-0 satisfy shared-lib loads from the pillow/opencv-family
# deps; curl is used by the compose health check.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb libgl1 libglib2.0-0 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
# The API serves the SPA from <repo>/frontend/dist (FRONTEND_BUILD_DIR); drop
# the freshly built assets there.
COPY --from=frontend /frontend/dist /app/frontend/dist

# Editable install keeps the package at /app/cognitrix, so its relative path to
# ../frontend/dist resolves at runtime (a site-packages install would not).
RUN pip install -e .

EXPOSE 8000

# Web server by default; the worker service overrides this command in compose.
# xvfb-run -a provides the virtual display the tool stack needs.
CMD ["xvfb-run", "-a", "cognitrix", "--ui", "web"]
