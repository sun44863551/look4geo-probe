from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f"configuration root must be a mapping: {path}")
    return value


def load_configuration(config_dir: Path) -> tuple[dict[str, dict], dict]:
    platform_data = _load_yaml(config_dir / "platforms.yaml")
    routing_data = _load_yaml(config_dir / "routing.yaml")
    return platform_data["platforms"], routing_data

