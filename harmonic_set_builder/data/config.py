"""One JSON config file: settings plus UI state (DB override, last set, layout)."""
from __future__ import annotations

import json
from pathlib import Path

from ..core.settings import Settings
from .store import atomic_write_json


class Config:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {}
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.settings = Settings.from_dict(self.data.get("settings", {}))

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.data["settings"] = self.settings.to_dict()
        atomic_write_json(self.path, self.data)
