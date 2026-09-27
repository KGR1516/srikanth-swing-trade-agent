"""Config loading helpers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"


def load_yaml(name: str) -> dict[str, Any]:
    with (CONFIG_DIR / name).open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass
class Config:
    settings: dict[str, Any]
    rules: dict[str, Any]
    universe: dict[str, Any]

    def path(self, key: str) -> Path:
        p = ROOT / self.settings["paths"][key]
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_config() -> Config:
    return Config(
        settings=load_yaml("settings.yaml"),
        rules=load_yaml("financial_rules.yaml"),
        universe=load_yaml("universe.yaml"),
    )
