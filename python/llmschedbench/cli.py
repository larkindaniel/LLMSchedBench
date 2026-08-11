"""LLMSchedBench command-line interface."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .calibration import (
    build_unloaded_workload,
    count_cluster_workers,
    estimate_service_rates,
    write_calibration,
    write_unloaded_workload,
)
from .calibration import (
    sha256_file as calibration_sha256_file,
)
from .data.mix import build_mixed_trace, write_mix_provenance
from .data.normalize import iter_normalize_paths
from .data.simulator import write_simulator_jsonl
from .data.sources import (
    fetch_source,
    load_manifest,
    sha256_file,
    source_asset_paths,
)
from .saturation import parse_simulator_utilization, rescale_workload_arrivals
from .scenario import compile_scenario
from .schema import SCHEMA_VERSION, write_parquet, write_parquet_stream

POLICIES = ("least_loaded", "cache_max", "weighted_fair", "slo_guarded_affinity")


def _not_implemented(command: str) -> int:
    raise SystemExit(f"{command} is part of the public interface but is not implemented yet")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llmschedbench")
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0.dev0")
    commands = parser.add_subparsers(dest="command", required=True)

    data = commands.add_parser("data", help="fetch and normalize source traces")
    data_commands = data.add_subparsers(dest="data_command", required=True)
    fetch = data_commands.add_parser("fetch")
    fetch.add_argument("source", choices=("qwen", "tracelab"))
    fetch.add_argument("--output-root", default="data/raw")
    fetch.add_argument("--force", action="store_true")
    normalize = data_commands.add_parser("normalize")
    normalize.add_argument("source", choices=("qwen", "tracelab"))
    normalize.add_argument("--input", action="append", dest="inputs")
    normalize.add_argument("--raw-root", default="data/raw")
    normalize.add_argument("--output")
    normalize.add_argument("--simulator-output")
    normalize.add_argument("--provenance-output")
    mix = data_commands.add_parser("mix", help="build a controlled three-tenant trace")
    mix.add_argument("scenario")
    mix.add_argument("--qwen")
    mix.add_argument("--tracelab")
    mix.add_argument("--processed-root", default="data/processed")
    mix.add_argument("--entries", type=int)
    mix.add_argument("--output")
    mix.add_argument("--simulator-output")
    mix.add_argument("--provenance-output")

    scenario = commands.add_parser("scenario", help="validate and resolve scenarios")
    scenario_commands = scenario.add_subparsers(dest="scenario_command", required=True)
    compile_command = scenario_commands.add_parser("compile")
    compile_command.add_argument("scenario")
    compile_command.add_argument("--output")

    calibrate = commands.add_parser(
        "calibrate", help="measure unloaded simulator service rates"
    )
    calibrate_commands = calibrate.add_subparsers(
        dest="calibrate_command", required=True
    )
    workload = calibrate_commands.add_parser("workload")
    workload.add_argument("--cluster-config", required=True)
    workload.add_argument("--output", required=True)
    workload.add_argument("--gap-seconds", type=float, default=30.0)
    estimate = calibrate_commands.add_parser("estimate")
    estimate.add_argument("--input", required=True)
    estimate.add_argument("--output", required=True)
    estimate.add_argument("--expected-workers", type=int)
    estimate.add_argument("--max-overlap-ns", type=int, default=0)
    estimate.add_argument("--workload")
    estimate.add_argument("--cluster-config")

    saturation = commands.add_parser(
        "saturation", help="prepare and measure saturation-search points"
    )
    saturation_commands = saturation.add_subparsers(
        dest="saturation_command", required=True
    )
    saturation_workload = saturation_commands.add_parser("workload")
    saturation_workload.add_argument("--input", required=True)
    saturation_workload.add_argument("--output", required=True)
    saturation_workload.add_argument("--arrival-rate-rps", type=float, required=True)
    saturation_measure = saturation_commands.add_parser("measure")
    saturation_measure.add_argument("--log", required=True)
    saturation_measure.add_argument("--cluster-config", required=True)
    saturation_measure.add_argument("--arrival-rate-rps", type=float, required=True)
    saturation_measure.add_argument("--output", required=True)

    run = commands.add_parser("run")
    run.add_argument("scenario")
    run.add_argument("--policy", required=True, choices=POLICIES)

    sweep = commands.add_parser("sweep")
    sweep.add_argument("scenario")

    report = commands.add_parser("report")
    report.add_argument("run_directory")

    validate = commands.add_parser("validate")
    validate.add_argument("scenario")
    validate.add_argument("--endpoints", nargs="+", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "data" and args.data_command == "fetch":
        paths = fetch_source(
            args.source,
            output_root=args.output_root,
            force=args.force,
        )
        for path in paths:
            print(path)
        return 0
    if args.command == "data" and args.data_command == "normalize":
        inputs = (
            [Path(path) for path in args.inputs]
            if args.inputs
            else source_asset_paths(args.source, args.raw_root)
        )
        missing = [path for path in inputs if not path.is_file()]
        if missing:
            rendered = ", ".join(str(path) for path in missing)
            raise SystemExit(
                f"source assets not found: {rendered}; run "
                f"'llmschedbench data fetch {args.source}' first"
            )
        manifest = load_manifest()
        version = str(manifest["sources"][args.source]["version"])
        output = Path(args.output or f"data/processed/{args.source}/{version}/calls.parquet")
        simulator_output = Path(args.simulator_output) if args.simulator_output else None
        calls = iter_normalize_paths(args.source, inputs)
        if simulator_output is not None:
            materialized = list(calls)
            write_parquet(materialized, str(output))
            write_simulator_jsonl(materialized, simulator_output)
            count = len(materialized)
        else:
            count = write_parquet_stream(calls, str(output))
        provenance_output = Path(
            args.provenance_output or f"{output}.provenance.json"
        )
        source_entry = manifest["sources"][args.source]
        provenance = {
            "llmschedbench_version": __version__,
            "normalization_schema_version": SCHEMA_VERSION,
            "source": args.source,
            "source_license": source_entry["license"],
            "source_repository": source_entry["repository"],
            "source_revision": source_entry["revision"],
            "source_version": version,
            "inputs": [
                {
                    "name": path.name,
                    "sha256": sha256_file(path),
                    "size": path.stat().st_size,
                }
                for path in inputs
            ],
            "outputs": {
                "parquet": {
                    "name": output.name,
                    "rows": count,
                    "sha256": sha256_file(output),
                    "size": output.stat().st_size,
                }
            },
        }
        if simulator_output is not None:
            provenance["outputs"]["simulator_jsonl"] = {
                "name": simulator_output.name,
                "sha256": sha256_file(simulator_output),
                "size": simulator_output.stat().st_size,
            }
        provenance_output.parent.mkdir(parents=True, exist_ok=True)
        temporary_provenance = provenance_output.with_suffix(
            provenance_output.suffix + ".part"
        )
        temporary_provenance.write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_provenance, provenance_output)
        print(
            json.dumps(
                {
                    "calls": count,
                    "parquet": str(output),
                    "provenance": str(provenance_output),
                    "simulator_jsonl": (
                        str(simulator_output) if simulator_output is not None else None
                    ),
                    "source": args.source,
                    "version": version,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "data" and args.data_command == "mix":
        manifest = load_manifest()
        qwen_version = str(manifest["sources"]["qwen"]["version"])
        tracelab_version = str(manifest["sources"]["tracelab"]["version"])
        processed_root = Path(args.processed_root)
        qwen = Path(
            args.qwen
            or processed_root / "qwen" / qwen_version / "calls.parquet"
        )
        tracelab = Path(
            args.tracelab
            or processed_root / "tracelab" / tracelab_version / "calls.parquet"
        )
        missing = [path for path in (qwen, tracelab) if not path.is_file()]
        if missing:
            rendered = ", ".join(str(path) for path in missing)
            raise SystemExit(
                f"normalized inputs not found: {rendered}; run both data normalize "
                "commands first"
            )
        scenario_name = Path(args.scenario).stem
        output_root = processed_root / "mixed" / scenario_name
        output = Path(args.output or output_root / "calls.parquet")
        simulator_output = Path(
            args.simulator_output or output_root / "workload.jsonl"
        )
        provenance_output = Path(
            args.provenance_output or output_root / "provenance.json"
        )
        provenance = build_mixed_trace(
            args.scenario,
            qwen,
            tracelab,
            output_path=output,
            simulator_output_path=simulator_output,
            entries=args.entries,
        )
        provenance["llmschedbench_version"] = __version__
        provenance["normalization_schema_version"] = SCHEMA_VERSION
        provenance["source_versions"] = {
            "qwen": qwen_version,
            "tracelab": tracelab_version,
        }
        write_mix_provenance(provenance, provenance_output)
        print(
            json.dumps(
                {
                    "calls": provenance["calls"],
                    "entries": provenance["entries"],
                    "parquet": str(output),
                    "provenance": str(provenance_output),
                    "simulator_jsonl": str(simulator_output),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "scenario" and args.scenario_command == "compile":
        compiled = compile_scenario(args.scenario)
        rendered = json.dumps(compiled, indent=2, sort_keys=True) + "\n"
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    if args.command == "calibrate" and args.calibrate_command == "workload":
        workers = count_cluster_workers(args.cluster_config)
        requests = build_unloaded_workload(
            workers=workers, gap_seconds=args.gap_seconds
        )
        count = write_unloaded_workload(requests, args.output)
        print(
            json.dumps(
                {
                    "calls": count,
                    "output": str(Path(args.output)),
                    "sha256": calibration_sha256_file(args.output),
                    "workers": workers,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "calibrate" and args.calibrate_command == "estimate":
        value = estimate_service_rates(
            args.input,
            expected_workers=args.expected_workers,
            max_overlap_ns=args.max_overlap_ns,
            workload_path=args.workload,
            cluster_config_path=args.cluster_config,
        )
        write_calibration(value, args.output)
        print(
            json.dumps(
                {
                    "output": str(Path(args.output)),
                    "sha256": calibration_sha256_file(args.output),
                    "workers": len(value["workers"]),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "saturation" and args.saturation_command == "workload":
        count = rescale_workload_arrivals(
            args.input,
            args.output,
            arrival_rate_rps=args.arrival_rate_rps,
        )
        print(
            json.dumps(
                {
                    "arrival_rate_rps": args.arrival_rate_rps,
                    "entries": count,
                    "output": str(Path(args.output)),
                    "sha256": calibration_sha256_file(args.output),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "saturation" and args.saturation_command == "measure":
        value = parse_simulator_utilization(args.log, args.cluster_config)
        value["arrival_rate_rps"] = args.arrival_rate_rps
        write_calibration(value, args.output)
        print(json.dumps(value, sort_keys=True))
        return 0
    return _not_implemented(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
