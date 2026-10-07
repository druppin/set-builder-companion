"""Atomic autosave of sets with version history and corrupt-file recovery.

Layout: ``<data dir>/sets/<set id>/<timestamp>.json`` (newest = current),
keeping the last ``KEEP`` versions per set.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..core.setlist import SetModel

KEEP = 20


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@dataclass
class LoadResult:
    model: Optional[SetModel]
    recovered_from: Optional[str] = None  # set when the newest file was corrupt


class SetStore:
    def __init__(self, root: Path):
        self.root = Path(root) / "sets"
        self.root.mkdir(parents=True, exist_ok=True)
        self._last_stamp = 0

    def _dir(self, set_id: str) -> Path:
        return self.root / set_id

    def _versions(self, set_id: str) -> list[Path]:
        d = self._dir(set_id)
        if not d.is_dir():
            return []
        return sorted(d.glob("*.json"), reverse=True)

    def save(self, model: SetModel) -> Path:
        stamp = max(time.time_ns() // 1000, self._last_stamp + 1)  # µs, strictly increasing
        self._last_stamp = stamp
        path = self._dir(model.id) / f"{stamp:020d}.json"
        atomic_write_json(path, model.to_dict())
        for old in self._versions(model.id)[KEEP:]:
            old.unlink(missing_ok=True)
        return path

    def load(self, set_id: str) -> LoadResult:
        versions = self._versions(set_id)
        for i, p in enumerate(versions):
            try:
                with open(p, encoding="utf-8") as f:
                    model = SetModel.from_dict(json.load(f))
                return LoadResult(model, versions[0].name if i > 0 else None)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return LoadResult(None, versions[0].name if versions else None)

    def list_sets(self) -> list[tuple[str, str]]:
        """(id, name) of every stored set, newest first."""
        out = []
        dirs = [d for d in self.root.iterdir() if d.is_dir()]
        dirs.sort(key=lambda d: max((p.name for p in d.glob("*.json")), default=""), reverse=True)
        for d in dirs:
            r = self.load(d.name)
            if r.model:
                out.append((r.model.id, r.model.name))
        return out

    def delete(self, set_id: str) -> None:
        shutil.rmtree(self._dir(set_id), ignore_errors=True)
