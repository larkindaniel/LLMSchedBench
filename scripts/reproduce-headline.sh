#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"
HOST_PYTHON="${LLMSCHEDBENCH_PYTHON:-${REPO_ROOT}/.venv/bin/python}"
MAX_HOURS="${LLMSCHEDBENCH_MAX_HOURS:-9}"
PREFLIGHT_ONLY=0

if [[ "${1:-}" == "--preflight-only" ]]; then
  PREFLIGHT_ONLY=1
elif [[ $# -ne 0 ]]; then
  echo "usage: $0 [--preflight-only]" >&2
  exit 2
fi

if [[ ! -x "${HOST_PYTHON}" ]]; then
  BOOTSTRAP_PYTHON="${LLMSCHEDBENCH_BOOTSTRAP_PYTHON:-python3.11}"
  if ! command -v "${BOOTSTRAP_PYTHON}" >/dev/null 2>&1; then
    echo "Python 3.11 is required; set LLMSCHEDBENCH_BOOTSTRAP_PYTHON." >&2
    exit 1
  fi
  "${BOOTSTRAP_PYTHON}" -m venv "${REPO_ROOT}/.venv"
  HOST_PYTHON="${REPO_ROOT}/.venv/bin/python"
  "${HOST_PYTHON}" -m pip install --upgrade pip
  "${HOST_PYTHON}" -m pip install -e "${REPO_ROOT}[dev]"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required for the pinned Linux AMD64 simulator." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop is installed but not running." >&2
  exit 1
fi

export PYTHONPATH="${REPO_ROOT}/python"

"${HOST_PYTHON}" -m llmschedbench.cli scenario compile \
  "${REPO_ROOT}/scenarios/balanced.yaml" >/dev/null
"${HOST_PYTHON}" -m pytest -q "${REPO_ROOT}/tests/python"
"${HOST_PYTHON}" -m ruff check "${REPO_ROOT}"

if [[ ${PREFLIGHT_ONLY} -eq 1 ]]; then
  echo "LLMSchedBench headline preflight passed."
  exit 0
fi

for SOURCE in qwen tracelab; do
  VERSION=$("${HOST_PYTHON}" - "${SOURCE}" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(
    (Path.cwd() / "python/llmschedbench/data/sources.json").read_text()
)
print(manifest["sources"][sys.argv[1]]["version"])
PY
  )
  PROCESSED="${REPO_ROOT}/data/processed/${SOURCE}/${VERSION}/calls.parquet"
  if [[ ! -f "${PROCESSED}" ]]; then
    "${HOST_PYTHON}" -m llmschedbench.cli data fetch "${SOURCE}"
    "${HOST_PYTHON}" -m llmschedbench.cli data normalize "${SOURCE}"
  fi
done

SERVICE_RATES="${REPO_ROOT}/runs/calibration/llama31-8b-rtxpro6000-v1/service-rates.json"
if [[ ! -f "${SERVICE_RATES}" ]]; then
  CALIBRATION_RUN_NAME=llama31-8b-rtxpro6000-v1 \
    "${REPO_ROOT}/scripts/calibrate-service-rates.sh"
fi

set +e
"${HOST_PYTHON}" -m llmschedbench.cli sweep \
  "${REPO_ROOT}/scenarios/balanced.yaml" \
  --milestone overnight-m3 \
  --run-root "${REPO_ROOT}/runs/overnight-m3" \
  --service-rates "${SERVICE_RATES}" \
  --max-hours "${MAX_HOURS}"
SWEEP_STATUS=$?
set -e

set +e
"${HOST_PYTHON}" -m llmschedbench.cli report \
  "${REPO_ROOT}/runs/overnight-m3" \
  --output "${REPO_ROOT}/artifacts/overnight-m3" \
  --cluster-config "${REPO_ROOT}/configs/cluster/benchmark_2worker.json" \
  --require-complete
REPORT_STATUS=$?
set -e

if [[ ${SWEEP_STATUS} -ne 0 ]]; then
  exit "${SWEEP_STATUS}"
fi
exit "${REPORT_STATUS}"
