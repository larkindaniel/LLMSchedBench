"""Programmatic LLMServingSim entry point for external C++ routing policies."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .scenario import compile_scenario
from .simulator_bridge import PolicyRouter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m llmschedbench.simulator_runner")
    parser.add_argument("--policy", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--service-rates")
    parser.add_argument("--decision-log", required=True)
    return parser


def _write_decisions(records: list[dict], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        os.replace(temporary, destination)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise


def _load_service_rates(path: str | Path) -> dict[str, dict[str, float]]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("service-rate file must contain a JSON object")
    rates = value.get("workers", value)
    if not isinstance(rates, dict):
        raise TypeError("service-rate workers must be a JSON object")
    required = {"prefill_tokens_per_second", "decode_tokens_per_second"}
    normalized: dict[str, dict[str, float]] = {}
    for worker_id, worker_rates in rates.items():
        if not isinstance(worker_rates, dict) or not required <= worker_rates.keys():
            raise ValueError(f"worker {worker_id} is missing calibrated service rates")
        normalized[str(worker_id)] = {
            name: float(worker_rates[name]) for name in sorted(required)
        }
        if any(value <= 0 for value in normalized[str(worker_id)].values()):
            raise ValueError(f"worker {worker_id} service rates must be positive")
    return normalized


def main(argv: Sequence[str] | None = None) -> int:
    args, serving_args = build_parser().parse_known_args(argv)
    scenario = compile_scenario(args.scenario)
    weights = {
        tenant: float(config["weight"])
        for tenant, config in scenario["tenants"].items()
    }
    slo_ns = {
        tenant: round(float(config["ttft_slo_ms"]) * 1_000_000)
        for tenant, config in scenario["tenants"].items()
    }
    service_rates = {}
    if args.service_rates:
        service_rates = _load_service_rates(args.service_rates)
    router = PolicyRouter(
        args.policy,
        tenant_weights=weights,
        tenant_slo_ns=slo_ns,
        service_rates=service_rates,
    )

    if "--request-routing-policy" in serving_args:
        index = serving_args.index("--request-routing-policy")
        if index + 1 >= len(serving_args) or serving_args[index + 1] != "CUSTOM":
            raise ValueError("external policy runner requires CUSTOM routing")
    else:
        serving_args.extend(["--request-routing-policy", "CUSTOM"])
    old_argv = sys.argv
    try:
        sys.argv = ["python -m serving", *serving_args]
        from serving.__main__ import main as serving_main

        serving_main(custom_selector=router)
    finally:
        sys.argv = old_argv
    _write_decisions(router.decisions, args.decision_log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
