import json

import pytest

from app.config import CONFIG_DIR
from app.errors import ConfigError
from app.models_registry import load_registry


@pytest.fixture
def registry():
    return load_registry(CONFIG_DIR / "models.json")


def test_real_registry_has_four_levels_in_order(registry):
    assert [m.id for m in registry.models] == [
        "claude-haiku-5-5",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
        "claude-fable-5-2",
    ]
    assert [m.level for m in registry.models] == [1, 2, 3, 4]
    assert registry.get("claude-haiku-5-5").display.startswith("Level 1 - Claude Haiku 5.5")


def test_cost_calculation(registry):
    haiku = registry.get("claude-haiku-5-5")
    assert haiku.cost_usd(1_000_000, 1_000_000) == pytest.approx(0.60)
    assert registry.get("claude-opus-5-5").cost_usd(1500, 300) == pytest.approx(0.012)


def test_task_model_defaults_and_can_be_changed(conn, registry):
    assert registry.model_for_task(conn, "analyze").id == "claude-haiku-5-5"
    registry.set_model_for_task(conn, "analyze", "claude-sonnet-5-5")
    assert registry.model_for_task(conn, "analyze").id == "claude-sonnet-5-5"


def test_unknown_model_or_task_rejected(conn, registry):
    with pytest.raises(ConfigError):
        registry.set_model_for_task(conn, "analyze", "gpt-9")
    with pytest.raises(ValueError):
        registry.model_for_task(conn, "dance")


def test_stale_saved_choice_falls_back_to_default(conn, registry):
    from app import store

    store.set_setting(conn, "model.analyze", "claude-removed-model")
    assert registry.model_for_task(conn, "analyze").id == "claude-haiku-5-5"


def _write(tmp_path, data):
    path = tmp_path / "models.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


GOOD_MODEL = {
    "id": "m1", "name": "M1", "level": 1, "level_label": "Basic",
    "input_usd_per_mtok": 1, "output_usd_per_mtok": 2,
}


@pytest.mark.parametrize(
    "data",
    [
        "{broken",
        {"models": [], "task_defaults": {"analyze": "m1"}},
        {"models": [GOOD_MODEL], "task_defaults": {}},
        {"models": [GOOD_MODEL], "task_defaults": {"analyze": "missing"}},
        {"models": [GOOD_MODEL, GOOD_MODEL], "task_defaults": {"analyze": "m1"}},
        {"models": [{**GOOD_MODEL, "input_usd_per_mtok": -1}], "task_defaults": {"analyze": "m1"}},
        {"models": [{"id": "m1"}], "task_defaults": {"analyze": "m1"}},
    ],
)
def test_invalid_registry_files(tmp_path, data):
    with pytest.raises(ConfigError):
        load_registry(_write(tmp_path, data))


def test_missing_registry_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_registry(tmp_path / "nope.json")
