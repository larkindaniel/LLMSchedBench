"""Immutable, resumable orchestration for serial simulator experiments."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .data.mix import build_mixed_trace, write_mix_provenance
from .data.simulator import simulator_records
from .data.sources import load_manifest
from .scenario import compile_scenario
from .schema import read_parquet

POLICIES = ("least_loaded", "cache_max", "weighted_fair", "slo_guarded_affinity")
MILESTONE_RATES = (1.0, 1.6, 2.2)
MILESTONE_SEEDS = (1729, 1730, 1731, 1732, 1733)
MILESTONE_CI_RATE = 1.6
REQUIRED_RUN_OUTPUTS = (
    "resolved-scenario.json",
    "workload.jsonl",
    "workload-provenance.json",
    "request-map.json",
    "simulator-results.csv",
    "decisions.jsonl",
    "simulator.log",
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: str | Path, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, destination)


def rate_label(rate: float) -> str:
    rendered = f"{float(rate):.9f}".rstrip("0").rstrip(".")
    return rendered.replace(".", "p")


@dataclass(frozen=True, order=True)
class RunSpec:
    """One policy/scenario/load/seed simulator run."""

    scenario: str
    policy: str
    arrival_rate_rps: float
    seed: int

    @property
    def key(self) -> str:
        policy = self.policy.replace("_", "-")
        return (
            f"{self.scenario}-r{rate_label(self.arrival_rate_rps)}-"
            f"s{self.seed}-{policy}"
        )


def build_milestone_specs(scenario: str = "balanced") -> list[RunSpec]:
    """Return the 28 unique runs in the bounded overnight milestone."""
    primary_seed = MILESTONE_SEEDS[0]
    values = {
        RunSpec(scenario, policy, rate, primary_seed)
        for rate in MILESTONE_RATES
        for policy in POLICIES
    }
    values.update(
        RunSpec(scenario, policy, MILESTONE_CI_RATE, seed)
        for seed in MILESTONE_SEEDS
        for policy in POLICIES
    )
    return sorted(
        values,
        key=lambda item: (
            item.seed != primary_seed,
            item.arrival_rate_rps,
            item.seed,
            POLICIES.index(item.policy),
        ),
    )


def _checksums(paths: list[Path], root: Path) -> dict[str, dict[str, int | str]]:
    return {
        str(path.relative_to(root)): {
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
        for path in sorted(paths)
    }


def validate_completed_run(
    run_directory: str | Path,
    *,
    required_outputs: tuple[str, ...] = REQUIRED_RUN_OUTPUTS,
) -> dict[str, Any]:
    """Validate a completed run manifest and every declared output checksum."""
    root = Path(run_directory)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"run manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("state") != "complete":
        raise ValueError(f"run is not complete: {root}")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise TypeError(f"run output manifest is invalid: {root}")
    missing = set(required_outputs) - set(outputs)
    if missing:
        raise ValueError(f"run manifest omits outputs: {sorted(missing)}")
    for name, expected in outputs.items():
        path = root / name
        if not path.is_file():
            raise ValueError(f"run output is missing: {path}")
        if path.stat().st_size != int(expected["size"]):
            raise ValueError(f"run output size mismatch: {path}")
        if sha256_file(path) != expected["sha256"]:
            raise ValueError(f"run output checksum mismatch: {path}")
    return manifest


def _request_map(calls_path: Path) -> list[dict[str, Any]]:
    records = simulator_records(read_parquet(str(calls_path)))
    mapped: list[dict[str, Any]] = []
    internal_id = 0
    for record in records:
        if "sub_requests" in record:
            session_id = str(record["session_id"])
            arrival_ns = int(record["arrival_time_ns"])
            for index, request in enumerate(record["sub_requests"]):
                mapped.append(
                    {
                        "internal_request_id": internal_id,
                        "request_id": str(request["request_id"]),
                        "session_id": session_id,
                        "step_index": index,
                        "tenant": str(request.get("tenant", "coding_agent")),
                        "arrival_time_ns": arrival_ns if index == 0 else None,
                        "input_tokens": int(request["input_toks"]),
                        "output_tokens": int(request["output_toks"]),
                    }
                )
                internal_id += 1
        else:
            mapped.append(
                {
                    "internal_request_id": internal_id,
                    "request_id": str(record["request_id"]),
                    "session_id": str(record["session_id"]),
                    "step_index": 0,
                    "tenant": str(record["tenant"]),
                    "arrival_time_ns": int(record["arrival_time_ns"]),
                    "input_tokens": int(record["input_toks"]),
                    "output_tokens": int(record["output_toks"]),
                }
            )
            internal_id += 1
    return mapped


class SerialExperimentRunner:
    """Prepare inputs and execute simulator runs one process at a time."""

    def __init__(
        self,
        repository: str | Path,
        scenario_path: str | Path,
        *,
        run_root: str | Path = "runs/overnight-m3",
        service_rates: str | Path = (
            "runs/calibration/llama31-8b-rtxpro6000-v1/service-rates.json"
        ),
        simulator_image: str = "llmschedbench-simulator",
    ) -> None:
        self.repository = Path(repository).resolve()
        self.scenario_path = (self.repository / scenario_path).resolve()
        self.run_root = (self.repository / run_root).resolve()
        self.service_rates = (self.repository / service_rates).resolve()
        self.simulator_image = simulator_image
        self.simulator_root = self.repository / "third_party" / "LLMServingSim"
        self.runtime_root = self.run_root / "runtime"

    def _relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.repository))

    def _source_paths(self) -> tuple[Path, Path]:
        manifest = load_manifest()
        qwen_version = str(manifest["sources"]["qwen"]["version"])
        tracelab_version = str(manifest["sources"]["tracelab"]["version"])
        qwen = self.repository / "data" / "processed" / "qwen" / qwen_version / "calls.parquet"
        tracelab = (
            self.repository
            / "data"
            / "processed"
            / "tracelab"
            / tracelab_version
            / "calls.parquet"
        )
        missing = [path for path in (qwen, tracelab) if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"normalized source data missing: {missing}")
        return qwen, tracelab

    def _git_revision(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _source_tree_sha256(self) -> str:
        digest = hashlib.sha256()
        roots = (
            self.repository / "CMakeLists.txt",
            self.repository / "python",
            self.repository / "src",
        )
        paths: list[Path] = []
        for root in roots:
            if root.is_file():
                paths.append(root)
            else:
                paths.extend(
                    path
                    for path in root.rglob("*")
                    if path.is_file() and "__pycache__" not in path.parts
                )
        for path in sorted(paths):
            digest.update(str(path.relative_to(self.repository)).encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def prepare_runtime(self, *, force: bool = False) -> None:
        """Build the Linux policy extension once for all serial runs."""
        subprocess.run(
            [str(self.repository / "scripts" / "apply-simulator-patch.sh")],
            cwd=self.repository,
            check=True,
        )
        if not self.service_rates.is_file():
            raise FileNotFoundError(f"service rates missing: {self.service_rates}")
        source_identity = {
            "git_revision": self._git_revision(),
            "source_tree_sha256": self._source_tree_sha256(),
            "patch_sha256": sha256_file(
                self.repository / "patches" / "llmservingsim-custom-routing.patch"
            ),
            "simulator_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.simulator_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
        }
        runtime_manifest = self.runtime_root / "manifest.json"
        if not force and runtime_manifest.is_file():
            existing = json.loads(runtime_manifest.read_text(encoding="utf-8"))
            extension = self.runtime_root / "stage" / "llmschedbench"
            if existing.get("source") == source_identity and extension.is_dir():
                return

        image_exists = subprocess.run(
            ["docker", "image", "inspect", self.simulator_image],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
        if not image_exists:
            subprocess.run(
                [
                    "docker",
                    "build",
                    "--platform",
                    "linux/amd64",
                    "--file",
                    str(self.repository / "docker" / "simulator.Dockerfile"),
                    "--tag",
                    self.simulator_image,
                    str(self.repository),
                ],
                check=True,
            )

        self.runtime_root.mkdir(parents=True, exist_ok=True)
        relative_runtime = self._relative(self.runtime_root)
        command = f"""
set -euo pipefail
(cd astra-sim && bash build/astra_analytical/build.sh)
rm -rf /workspace/{relative_runtime}/policy-build /workspace/{relative_runtime}/stage
cmake -S /workspace -B /workspace/{relative_runtime}/policy-build \\
  -DBUILD_TESTING=OFF \\
  -Dpybind11_DIR=\"$(python -m pybind11 --cmakedir)\"
cmake --build /workspace/{relative_runtime}/policy-build
cmake --install /workspace/{relative_runtime}/policy-build \\
  --prefix /workspace/{relative_runtime}/stage
cp -a /workspace/python/llmschedbench/. \\
  /workspace/{relative_runtime}/stage/llmschedbench/
"""
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                "linux/amd64",
                "--volume",
                f"{self.repository}:/workspace",
                "--volume",
                f"{self.simulator_root}:/app/LLMServingSim",
                "--workdir",
                "/app/LLMServingSim",
                self.simulator_image,
                "bash",
                "-lc",
                command,
            ],
            check=True,
        )
        _atomic_json(
            runtime_manifest,
            {
                "created_at": _utc_now(),
                "llmschedbench_version": __version__,
                "simulator_image": self.simulator_image,
                "source": source_identity,
            },
        )

    def _workload_directory(self, spec: RunSpec) -> Path:
        return (
            self.run_root
            / "workloads"
            / f"{spec.scenario}-r{rate_label(spec.arrival_rate_rps)}-s{spec.seed}"
        )

    def prepare_workload(self, spec: RunSpec) -> Path:
        destination = self._workload_directory(spec)
        manifest_path = destination / "manifest.json"
        workload_spec = {
            "scenario": spec.scenario,
            "arrival_rate_rps": spec.arrival_rate_rps,
            "seed": spec.seed,
        }
        if manifest_path.is_file():
            manifest = validate_completed_run(
                destination,
                required_outputs=(
                    "resolved-scenario.json",
                    "workload.jsonl",
                    "workload-provenance.json",
                    "request-map.json",
                    "calls.parquet",
                ),
            )
            if manifest.get("spec") != workload_spec:
                raise ValueError(
                    f"immutable workload identity mismatch: {destination}"
                )
            return destination

        temporary = destination.with_name(destination.name + ".incomplete")
        if temporary.exists():
            failed = self.run_root / "failed" / (
                temporary.name + "-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            )
            failed.parent.mkdir(parents=True, exist_ok=True)
            temporary.rename(failed)
        temporary.mkdir(parents=True)

        resolved = compile_scenario(self.scenario_path)
        resolved["name"] = spec.scenario
        resolved["seed"] = spec.seed
        resolved["trace"] = dict(resolved["trace"])
        resolved["trace"]["arrival_rate_rps"] = spec.arrival_rate_rps
        resolved["source_file"] = str(self.scenario_path)
        _atomic_json(temporary / "resolved-scenario.json", resolved)
        qwen, tracelab = self._source_paths()
        provenance = build_mixed_trace(
            temporary / "resolved-scenario.json",
            qwen,
            tracelab,
            output_path=temporary / "calls.parquet",
            simulator_output_path=temporary / "workload.jsonl",
        )
        provenance["llmschedbench_version"] = __version__
        write_mix_provenance(provenance, temporary / "workload-provenance.json")
        _atomic_json(temporary / "request-map.json", _request_map(temporary / "calls.parquet"))
        outputs = _checksums(
            [temporary / name for name in REQUIRED_RUN_OUTPUTS[:4]]
            + [temporary / "calls.parquet"],
            temporary,
        )
        _atomic_json(
            temporary / "manifest.json",
            {
                "state": "complete",
                "kind": "workload",
                "created_at": _utc_now(),
                "spec": workload_spec,
                "outputs": outputs,
            },
        )
        temporary.rename(destination)
        return destination

    def _run_command(self, spec: RunSpec, attempt: Path) -> list[str]:
        relative_attempt = self._relative(attempt)
        relative_runtime = self._relative(self.runtime_root)
        run_id = "llmsb-" + hashlib.sha256(spec.key.encode()).hexdigest()[:16]
        python_path = f"/workspace/{relative_runtime}/stage"
        runner_arguments = [
            "python",
            "-m",
            "llmschedbench.simulator_runner",
            "--policy",
            spec.policy,
            "--scenario",
            f"/workspace/{relative_attempt}/resolved-scenario.json",
            "--service-rates",
            f"/workspace/{self._relative(self.service_rates)}",
            "--decision-log",
            f"/workspace/{relative_attempt}/decisions.jsonl",
            "--cluster-config",
            "../../workspace/configs/cluster/benchmark_2worker.json",
            "--dtype",
            "bfloat16",
            "--block-size",
            "16",
            "--dataset",
            f"../../workspace/{relative_attempt}/workload.jsonl",
            "--output",
            f"/workspace/{relative_attempt}/simulator-results.csv",
            "--run-id",
            run_id,
            "--cleanup-inputs",
            "--log-interval",
            "1",
            "--log-level",
            "WARNING",
        ]
        inner = (
            f"PYTHONPATH={shlex.quote(python_path)} "
            + " ".join(shlex.quote(value) for value in runner_arguments)
        )
        return [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--volume",
            f"{self.repository}:/workspace",
            "--volume",
            f"{self.simulator_root}:/app/LLMServingSim",
            "--workdir",
            "/app/LLMServingSim",
            self.simulator_image,
            "bash",
            "-lc",
            "set -euo pipefail; " + inner,
        ]

    def run_one(self, spec: RunSpec) -> tuple[str, float]:
        destination = self.run_root / "runs" / spec.key
        if destination.exists():
            validate_completed_run(destination)
            return "skipped", 0.0
        workload = self.prepare_workload(spec)
        attempt = destination.with_name(destination.name + ".incomplete")
        if attempt.exists():
            failed = self.run_root / "failed" / (
                attempt.name + "-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            )
            failed.parent.mkdir(parents=True, exist_ok=True)
            attempt.rename(failed)
        attempt.mkdir(parents=True)
        for name in (
            "resolved-scenario.json",
            "workload.jsonl",
            "workload-provenance.json",
            "request-map.json",
        ):
            shutil.copy2(workload / name, attempt / name)
        command = self._run_command(spec, attempt)
        started_at = _utc_now()
        started = time.monotonic()
        try:
            with (attempt / "simulator.log").open("w", encoding="utf-8") as log:
                subprocess.run(
                    command,
                    cwd=self.repository,
                    check=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
        except BaseException as error:
            _atomic_json(
                attempt / "failure.json",
                {
                    "state": "failed",
                    "started_at": started_at,
                    "failed_at": _utc_now(),
                    "error": f"{type(error).__name__}: {error}",
                    "spec": spec.__dict__,
                },
            )
            failed = self.run_root / "failed" / (
                spec.key + "-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            )
            failed.parent.mkdir(parents=True, exist_ok=True)
            attempt.rename(failed)
            raise
        elapsed = time.monotonic() - started
        output_paths = [attempt / name for name in REQUIRED_RUN_OUTPUTS]
        missing = [path for path in output_paths if not path.is_file()]
        if missing:
            raise RuntimeError(f"simulator completed without required outputs: {missing}")
        outputs = _checksums(output_paths, attempt)
        _atomic_json(
            attempt / "manifest.json",
            {
                "state": "complete",
                "kind": "simulation",
                "started_at": started_at,
                "completed_at": _utc_now(),
                "wall_seconds": elapsed,
                "git_revision": self._git_revision(),
                "simulator_commit": subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.simulator_root,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                "service_rates": {
                    "path": self._relative(self.service_rates),
                    "sha256": sha256_file(self.service_rates),
                },
                "spec": spec.__dict__,
                "outputs": outputs,
            },
        )
        attempt.rename(destination)
        validate_completed_run(destination)
        return "completed", elapsed

    def run_serial(
        self,
        specs: list[RunSpec],
        *,
        max_hours: float = 9.0,
        prepare: bool = True,
    ) -> dict[str, Any]:
        if max_hours <= 0:
            raise ValueError("max_hours must be positive")
        if prepare:
            self.prepare_runtime()
        started_at = _utc_now()
        start = time.monotonic()
        completed: list[str] = []
        skipped: list[str] = []
        failed: list[dict[str, str]] = []
        durations: list[float] = []
        stopped_for_deadline = False
        for spec in specs:
            elapsed = time.monotonic() - start
            remaining = max_hours * 3600 - elapsed
            estimate = max(durations[-3:], default=0.0)
            if estimate and remaining < estimate * 1.15:
                stopped_for_deadline = True
                break
            try:
                state, duration = self.run_one(spec)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                failed.append({"key": spec.key, "error": f"{type(error).__name__}: {error}"})
                continue
            if state == "skipped":
                skipped.append(spec.key)
            else:
                completed.append(spec.key)
                durations.append(duration)
            _atomic_json(
                self.run_root / "progress.json",
                {
                    "state": "running",
                    "started_at": started_at,
                    "updated_at": _utc_now(),
                    "completed": completed,
                    "skipped": skipped,
                    "failed": failed,
                    "remaining": [
                        item.key
                        for item in specs
                        if item.key not in set(completed + skipped)
                        and item.key not in {value["key"] for value in failed}
                    ],
                },
            )
        summary = {
            "state": "partial" if stopped_for_deadline or failed else "complete",
            "started_at": started_at,
            "completed_at": _utc_now(),
            "wall_seconds": time.monotonic() - start,
            "completed": completed,
            "skipped": skipped,
            "failed": failed,
            "remaining": [
                item.key
                for item in specs
                if item.key not in set(completed + skipped)
                and item.key not in {value["key"] for value in failed}
            ],
            "stopped_for_deadline": stopped_for_deadline,
        }
        _atomic_json(self.run_root / "progress.json", summary)
        return summary
