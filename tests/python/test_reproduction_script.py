import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "reproduce-headline.sh"


def test_headline_reproduction_script_is_valid_and_serial():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    text = SCRIPT.read_text(encoding="utf-8")

    assert "llmschedbench.cli sweep" in text
    assert "--milestone overnight-m3" in text
    assert "--require-complete" in text
    assert "parallel" not in text
    assert " &\n" not in text
    assert "wait " not in text
