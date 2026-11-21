#!/bin/sh
# Installs the app's own CLI (`cognitrix`) into its own venv in the sandbox
# image, from the same source the image is built from, so it matches the
# engine of that commit/release. Run by sandbox/desktop/Dockerfile:
#
#   install.sh <source folder> <venv folder>
#
# chromadb (long-term vector memory: ~280 MB with onnxruntime, kubernetes and
# grpc) is left out; the package turns that memory off when chromadb is
# missing (cognitrix/memory/hybrid_context.py). Every other dependency comes
# from the package's own metadata, so pyproject.toml stays the one list.
set -eu
src="$1"
venv="$2"
skip="chromadb"

python3 -m venv "$venv"
"$venv/bin/pip" install --no-cache-dir --disable-pip-version-check --no-deps "$src"
"$venv/bin/python" - "$skip" > /tmp/cognitrix-requirements.txt <<'PY'
import importlib.metadata as md
import re
import sys

skip = set(sys.argv[1].split(','))
for req in md.requires('cognitrix') or []:
    name = re.match(r'[A-Za-z0-9._-]+', req).group(0).lower().replace('_', '-')
    if name not in skip:
        print(req)
PY
"$venv/bin/pip" install --no-cache-dir --disable-pip-version-check -r /tmp/cognitrix-requirements.txt
rm -f /tmp/cognitrix-requirements.txt
mkdir -p /tmp/cognitrix-check
# Fails the build if the CLI can't start (a dependency that now needs chromadb
# at import time, say).
env -u PYTHONPATH HOME=/tmp/cognitrix-check "$venv/bin/cognitrix" --version
env -u PYTHONPATH HOME=/tmp/cognitrix-check "$venv/bin/python" -c 'import cognitrix.cli.args, cognitrix.memory.hybrid_context'
rm -rf /tmp/cognitrix-check
