from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from platformdirs import user_config_dir

APP_NAME = "lut-renderer"
SETTINGS_FILE = "settings.json"


def _settings_path() -> Path:
    root = Path(user_config_dir(APP_NAME))
    root.mkdir(parents=True, exist_ok=True)
    return root / SETTINGS_FILE


def load_settings() -> Dict[str, Any]:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: Dict[str, Any]) -> None:
    path = _settings_path()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
