#!/usr/bin/env bash
set -euo pipefail

# Run the built app from the terminal (useful for seeing stderr logs).
# Usage:
#   scripts/run_built_app.sh            # runs dist/lut-renderer.app
#   APP_NAME=lut-renderer scripts/run_built_app.sh

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

APP_NAME="${APP_NAME:-lut-renderer}"
BIN="dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}"

if [[ ! -x "$BIN" ]]; then
  echo "Not found or not executable: $BIN" >&2
  echo "Build first: scripts/build_onefile_app.sh (or build_dir_app.sh)" >&2
  exit 1
fi

# When launching a .app via Finder, PATH may not include Homebrew locations.
# Running from terminal usually inherits PATH, but this makes it robust either way.
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"

exec "$BIN"

