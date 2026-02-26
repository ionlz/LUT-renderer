#!/usr/bin/env bash
set -euo pipefail

# Build a single-file macOS .app bundle via PyInstaller.
# Output: dist/lut-renderer.app

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

APP_NAME="${APP_NAME:-lut-renderer}"
ENTRYPOINT="${ENTRYPOINT:-src/lut_renderer/app.py}"

export PYTHONNOUSERSITE=1

python -m PyInstaller --noconfirm --clean \
  --name "$APP_NAME" \
  --windowed --onefile \
  --collect-all PySide6 \
  --collect-data qt_material --collect-submodules qt_material \
  "$ENTRYPOINT"

echo "Built: dist/${APP_NAME}.app"

