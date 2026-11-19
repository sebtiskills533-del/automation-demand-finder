"""The services the user offers (config/services.json): automations, Etsy help,
SEO, local SEO... Every prompt and Muse job describes the freelancer from here,
so adding a service is one edit in that file."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import CONFIG_DIR
from app.errors import ConfigError

SERVICES_FILE = CONFIG_DIR / "services.json"
OTHER = "other"
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    badge: str
    details: str


def load_services(path: Path = SERVICES_FILE) -> tuple[Service, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        services = tuple(
            Service(str(s["id"]), str(s["name"]), str(s.get("badge") or s["name"]), str(s.get("details", "")))
            for s in raw["services"]
        )
    except FileNotFoundError:
        raise ConfigError(f"Services file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid JSON (line {exc.lineno}): {exc.msg}") from None
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"{path.name} is missing a field: {exc}") from None
    if not services:
        raise ConfigError(f"{path.name} lists no services.")
    ids = [s.id for s in services]
    if len(set(ids)) != len(ids) or OTHER in ids or not all(_ID_RE.match(i) for i in ids):
        raise ConfigError(f"{path.name}: service ids must be unique, short lowercase words (not 'other').")
    return services


@lru_cache(maxsize=1)
def default_services() -> tuple[Service, ...]:
    """The services from config/services.json, read once per process."""
    return load_services()


def service_ids(services: tuple[Service, ...] | None = None) -> tuple[str, ...]:
    return tuple(s.id for s in (services or default_services())) + (OTHER,)


def offer_summary(services: tuple[Service, ...] | None = None) -> str:
    """One line describing what the freelancer offers, for prompts."""
    return "; ".join(f"{s.name} ({s.details})" for s in (services or default_services()))


def service_names(services: tuple[Service, ...] | None = None) -> str:
    """'Small automations, Etsy shop help, SEO, Local SEO' (for short instructions)."""
    return ", ".join(s.name for s in (services or default_services()))


def badge_for(service_id: str | None) -> str:
    for s in default_services():
        if s.id == service_id:
            return s.badge
    return "• Other"
