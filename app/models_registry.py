"""The Claude models the app can use, their levels and prices.

The list lives in config/models.json so prices can be updated without code.
Which model each task uses is saved in the database (Settings page).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app import store
from app.errors import ConfigError

# Every task that calls Claude. Add new tasks (e.g. "reply") here.
TASKS = {
    "analyze": "Daily post analysis",
    "reply": "Draft replies",
    "report": "Decision reports",
    "cluster": "Grouping into top problems",
    "spec": "Product specs",
    "email": "Email drafts",
}

_SETTING_PREFIX = "model."


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    level: int
    level_label: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    description: str

    @property
    def display(self) -> str:
        return f"Level {self.level} - {self.name} ({self.level_label})"

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_usd_per_mtok + output_tokens * self.output_usd_per_mtok
        ) / 1_000_000


@dataclass(frozen=True)
class ModelRegistry:
    models: tuple[ModelInfo, ...]
    task_defaults: dict[str, str]

    def get(self, model_id: str) -> ModelInfo:
        for model in self.models:
            if model.id == model_id:
                return model
        raise ConfigError(f"Unknown model {model_id!r}. Add it to config/models.json first.")

    def model_for_task(self, conn: sqlite3.Connection, task: str) -> ModelInfo:
        """The model chosen for `task` in Settings, or the default from models.json."""
        _check_task(task)
        chosen = store.get_setting(conn, _SETTING_PREFIX + task)
        if chosen and any(m.id == chosen for m in self.models):
            return self.get(chosen)
        return self.get(self.task_defaults[task])

    def set_model_for_task(self, conn: sqlite3.Connection, task: str, model_id: str) -> None:
        _check_task(task)
        self.get(model_id)  # raises if unknown
        store.set_setting(conn, _SETTING_PREFIX + task, model_id)


def load_registry(path: Path) -> ModelRegistry:
    """Read and validate config/models.json."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        models = tuple(
            sorted((_parse_model(item) for item in raw["models"]), key=lambda m: m.level)
        )
        defaults = dict(raw["task_defaults"])
    except FileNotFoundError:
        raise ConfigError(f"Model list not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid JSON (line {exc.lineno}): {exc.msg}") from None
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"{path.name} is missing or has a wrong field: {exc}") from None

    if not models:
        raise ConfigError(f"{path.name} lists no models.")
    ids = [m.id for m in models]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"{path.name} lists the same model id twice.")
    registry = ModelRegistry(models, defaults)
    for task in TASKS:
        if task not in defaults:
            raise ConfigError(f"{path.name} has no default model for task {task!r}.")
        registry.get(defaults[task])
    return registry


def _parse_model(item: dict) -> ModelInfo:
    model = ModelInfo(
        id=str(item["id"]),
        name=str(item["name"]),
        level=int(item["level"]),
        level_label=str(item["level_label"]),
        input_usd_per_mtok=float(item["input_usd_per_mtok"]),
        output_usd_per_mtok=float(item["output_usd_per_mtok"]),
        description=str(item.get("description", "")),
    )
    if not model.id or model.level < 1 or model.input_usd_per_mtok < 0 or model.output_usd_per_mtok < 0:
        raise ValueError(f"invalid values for model {model.id!r}")
    return model


def _check_task(task: str) -> None:
    if task not in TASKS:
        raise ValueError(f"Unknown task {task!r}")
