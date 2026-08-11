"""Scenario loading and validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .schema import TENANTS

REQUIRED_POLICIES = frozenset(
    {"least_loaded", "cache_max", "weighted_fair", "slo_guarded_affinity"}
)


def _load_yaml_or_json(path: Path) -> Mapping[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        value = json.loads(text)
    else:
        value = yaml.safe_load(text)
    if not isinstance(value, Mapping):
        raise TypeError("scenario root must be a mapping")
    return value


def compile_scenario(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    value = dict(_load_yaml_or_json(source))
    for key in ("name", "seed", "cluster", "tenants", "traffic"):
        if key not in value:
            raise ValueError(f"scenario missing required field: {key}")

    tenants = value["tenants"]
    traffic = value["traffic"]
    if not isinstance(tenants, Mapping) or set(tenants) != TENANTS:
        raise ValueError("scenario must configure exactly the three benchmark tenants")
    if not isinstance(traffic, Mapping) or set(traffic) != TENANTS:
        raise ValueError("traffic must define exactly the three benchmark tenants")

    total = sum(float(traffic[name]) for name in TENANTS)
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"traffic proportions must sum to 1.0, got {total}")
    if int(value["seed"]) < 0:
        raise ValueError("seed must be non-negative")

    trace = value.get("trace")
    if trace is not None:
        if not isinstance(trace, Mapping):
            raise ValueError("trace must be a mapping")
        if int(trace.get("entries", 0)) <= 0:
            raise ValueError("trace.entries must be positive")
        if float(trace.get("arrival_rate_rps", 0)) <= 0:
            raise ValueError("trace.arrival_rate_rps must be positive")
        coding = trace.get("coding_session", {})
        if not isinstance(coding, Mapping):
            raise ValueError("trace.coding_session must be a mapping")
        values = {
            name: int(coding.get(name, default))
            for name, default in (
                ("min_steps", 1),
                ("max_steps", 0),
                ("max_input_tokens", 0),
            )
        }
        if any(value < 0 for value in values.values()):
            raise ValueError("coding-session selection limits must be non-negative")
        if values["min_steps"] == 0:
            raise ValueError("trace.coding_session.min_steps must be positive")
        if values["max_steps"] and values["max_steps"] < values["min_steps"]:
            raise ValueError("trace.coding_session.max_steps must be >= min_steps")

    compiled = dict(value)
    compiled["source_file"] = str(source.resolve())
    compiled["scenario_schema_version"] = "1.0.0"
    return compiled
