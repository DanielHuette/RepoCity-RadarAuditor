#!/usr/bin/env sh
# requirements.txt carries only the third-party pins for installs without uv (the Dockerfile installs
# from uv.lock); the package itself is installed separately, so the project line is left out.
set -eu
cd "$(dirname "$0")/.."
uv export --no-dev --no-hashes --no-emit-project --format requirements-txt -o requirements.txt
