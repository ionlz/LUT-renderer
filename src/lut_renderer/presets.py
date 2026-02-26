from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from platformdirs import user_config_dir

from .models import ProcessingParams

APP_NAME = "lut-renderer"


def presets_dir() -> Path:
    config_root = Path(user_config_dir(APP_NAME))
    path = config_root / "presets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_presets() -> List[str]:
    path = presets_dir()
    names = []
    for file in path.glob("*.json"):
        names.append(file.stem)
    return sorted(names)


def load_preset(name: str) -> ProcessingParams:
    file_path = presets_dir() / f"{name}.json"
    if not file_path.exists():
        raise FileNotFoundError(f"Preset not found: {name}")
    data = json.loads(file_path.read_text(encoding="utf-8"))
    return ProcessingParams.from_dict(data)


def save_preset(name: str, params: ProcessingParams) -> Path:
    file_path = presets_dir() / f"{name}.json"
    if file_path.exists():
        raise FileExistsError(f"Preset already exists: {name}")
    file_path.write_text(json.dumps(params.to_dict(), indent=2), encoding="utf-8")
    return file_path


def overwrite_preset(name: str, params: ProcessingParams) -> Path:
    file_path = presets_dir() / f"{name}.json"
    file_path.write_text(json.dumps(params.to_dict(), indent=2), encoding="utf-8")
    return file_path


def delete_preset(name: str) -> None:
    file_path = presets_dir() / f"{name}.json"
    if file_path.exists():
        file_path.unlink()


def rename_preset(old_name: str, new_name: str) -> Path:
    src = presets_dir() / f"{old_name}.json"
    dst = presets_dir() / f"{new_name}.json"
    if not src.exists():
        raise FileNotFoundError(f"Preset not found: {old_name}")
    if dst.exists():
        raise FileExistsError(f"Preset already exists: {new_name}")
    src.rename(dst)
    return dst


def load_all_presets() -> Dict[str, ProcessingParams]:
    presets = {}
    for name in list_presets():
        try:
            presets[name] = load_preset(name)
        except Exception:
            continue
    return presets
