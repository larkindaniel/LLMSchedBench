import json
from pathlib import Path

import pytest
from llmschedbench.experiment import (
    MILESTONE_CI_RATE,
    MILESTONE_SEEDS,
    POLICIES,
    REQUIRED_RUN_OUTPUTS,
    RunSpec,
    SerialExperimentRunner,
    build_milestone_specs,
    rate_label,
    sha256_file,
    validate_completed_run,
)

ROOT = Path(__file__).resolve().parents[2]


def test_bounded_milestone_has_28_unique_serial_runs():
    specs = build_milestone_specs()

    assert len(specs) == 28
    assert len({spec.key for spec in specs}) == 28
    assert {spec.policy for spec in specs} == set(POLICIES)
    assert {
        spec.seed for spec in specs if spec.arrival_rate_rps == MILESTONE_CI_RATE
    } == set(MILESTONE_SEEDS)
    assert sum(spec.seed == MILESTONE_SEEDS[0] for spec in specs) == 12


def test_run_key_is_stable_and_path_safe():
    spec = RunSpec("balanced", "slo_guarded_affinity", 1.6, 1729)

    assert rate_label(1.6000000000) == "1p6"
    assert spec.key == "balanced-r1p6-s1729-slo-guarded-affinity"


def test_completed_run_validation_checks_every_output(tmp_path: Path):
    outputs = {}
    for name in REQUIRED_RUN_OUTPUTS:
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        outputs[name] = {"sha256": sha256_file(path), "size": path.stat().st_size}
    (tmp_path / "manifest.json").write_text(
        json.dumps({"state": "complete", "outputs": outputs}), encoding="utf-8"
    )

    assert validate_completed_run(tmp_path)["state"] == "complete"
    (tmp_path / REQUIRED_RUN_OUTPUTS[0]).write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch"):
        validate_completed_run(tmp_path)


def test_simulator_command_uses_pinned_cleanup_flag():
    runner = SerialExperimentRunner(ROOT, "scenarios/balanced.yaml")
    command = runner._run_command(
        RunSpec("balanced", "least_loaded", 1.0, 1729),
        ROOT / "runs" / "test.incomplete",
    )

    assert "--cleanup-inputs" in command[-1]
    assert "--cleanup-run-inputs" not in command[-1]
