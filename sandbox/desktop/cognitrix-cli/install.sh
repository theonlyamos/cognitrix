#!/bin/sh
# Installs the app's own CLI (`cognitrix`) into its own venv in the sandbox
# image, from the same source the image is built from, so it matches the
# engine of that commit/release. Run by sandbox/desktop/Dockerfile in three
# steps, so a version bump only rebuilds the small last one:
#
#   install.sh requirements <source folder> <requirements file>
#       Writes the package's dependencies (not its version) to a file. Run in
#       a throwaway build stage; the image copies only the file in, so the
#       next step's layer stays cached while the dependencies don't change.
#   install.sh deps <requirements file> <venv folder>
#       Creates the venv with the dependencies (the big layer, ~430 MB).
#   install.sh package <source folder> <venv folder>
#       Adds the package itself, without dependencies, and checks it starts.
#
# chromadb (long-term vector memory: ~280 MB with onnxruntime, kubernetes and
# grpc) is left out; the package turns that memory off when chromadb is
# missing (cognitrix/memory/hybrid_context.py). Every other dependency comes
# from the package's own metadata, so pyproject.toml stays the one list.
set -eu
step="$1"
skip="chromadb"

case "$step" in
requirements)
    src="$2"
    out="$3"
    tmp=/tmp/cognitrix-reqs-venv
    python3 -m venv "$tmp"
    "$tmp/bin/pip" install --no-cache-dir --disable-pip-version-check --no-deps "$src"
    "$tmp/bin/python" - "$skip" > "$out" <<'PY'
import importlib.metadata as md
import re
import sys

skip = set(sys.argv[1].split(','))
for req in md.requires('cognitrix') or []:
    name = re.match(r'[A-Za-z0-9._-]+', req).group(0).lower().replace('_', '-')
    if name not in skip:
        print(req)
PY
    rm -rf "$tmp"
    ;;
deps)
    reqs="$2"
    venv="$3"
    python3 -m venv "$venv"
    "$venv/bin/pip" install --no-cache-dir --disable-pip-version-check -r "$reqs"
    ;;
package)
    src="$2"
    venv="$3"
    "$venv/bin/pip" install --no-cache-dir --disable-pip-version-check --no-deps "$src"
    mkdir -p /tmp/cognitrix-check
    # Fails the build if the CLI can't start (a dependency that now needs
    # chromadb at import time, or one missing from the deps step, say).
    env -u PYTHONPATH HOME=/tmp/cognitrix-check "$venv/bin/cognitrix" --version
    env -u PYTHONPATH HOME=/tmp/cognitrix-check "$venv/bin/python" -c 'import cognitrix.cli.args, cognitrix.memory.hybrid_context'
    rm -rf /tmp/cognitrix-check
    ;;
*)
    echo "usage: install.sh requirements|deps|package ..." >&2
    exit 2
    ;;
esac
