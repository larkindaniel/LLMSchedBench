import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_custom_router_patch_is_applicable_or_already_applied():
    patch = ROOT / "patches" / "llmservingsim-custom-routing.patch"
    simulator = ROOT / "third_party" / "LLMServingSim"
    applicable = subprocess.run(
        ["git", "-C", str(simulator), "apply", "--check", str(patch)],
        check=False,
        capture_output=True,
        text=True,
    ).returncode == 0
    already_applied = subprocess.run(
        [
            "git",
            "-C",
            str(simulator),
            "apply",
            "--reverse",
            "--check",
            str(patch),
        ],
        check=False,
        capture_output=True,
        text=True,
    ).returncode == 0
    assert applicable or already_applied


def test_custom_router_patch_does_not_touch_astra_sim():
    text = (ROOT / "patches" / "llmservingsim-custom-routing.patch").read_text()
    changed_files = {
        line.removeprefix("+++ b/")
        for line in text.splitlines()
        if line.startswith("+++ b/")
    }
    assert changed_files == {
        "serving/__main__.py",
        "serving/core/router.py",
        "tests/test_custom_router.py",
    }
