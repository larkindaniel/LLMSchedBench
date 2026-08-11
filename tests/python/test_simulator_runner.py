import sys
import types
from pathlib import Path

import pytest
from llmschedbench.simulator_runner import main

ROOT = Path(__file__).resolve().parents[2]


def test_runner_injects_custom_policy_and_writes_decision_log(tmp_path, monkeypatch):
    captured = {}
    serving_package = types.ModuleType("serving")
    serving_package.__path__ = []
    serving_main = types.ModuleType("serving.__main__")

    def fake_main(custom_selector=None):
        captured["selector"] = custom_selector
        captured["argv"] = list(sys.argv)

    serving_main.main = fake_main
    monkeypatch.setitem(sys.modules, "serving", serving_package)
    monkeypatch.setitem(sys.modules, "serving.__main__", serving_main)
    decision_log = tmp_path / "decisions.jsonl"

    assert (
        main(
            [
                "--policy",
                "least_loaded",
                "--scenario",
                str(ROOT / "scenarios" / "smoke.yaml"),
                "--decision-log",
                str(decision_log),
                "--dataset",
                "workload.jsonl",
            ]
        )
        == 0
    )
    assert captured["selector"] is not None
    assert captured["argv"][-2:] == ["--request-routing-policy", "CUSTOM"]
    assert decision_log.read_text() == ""


def test_runner_rejects_non_custom_override(tmp_path):
    with pytest.raises(ValueError, match="requires CUSTOM"):
        main(
            [
                "--policy",
                "least_loaded",
                "--scenario",
                str(ROOT / "scenarios" / "smoke.yaml"),
                "--decision-log",
                str(tmp_path / "decisions.jsonl"),
                "--request-routing-policy",
                "LOAD",
            ]
        )
