#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <arrival-rate-rps>" >&2
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SIM_ROOT="${REPO_ROOT}/third_party/LLMServingSim"
HOST_PYTHON="${LLMSCHEDBENCH_PYTHON:-${REPO_ROOT}/.venv/bin/python}"
ARRIVAL_RATE="$1"
RATE_LABEL="${ARRIVAL_RATE//./p}"
RUN_ROOT="${REPO_ROOT}/runs/saturation/rate-${RATE_LABEL}"
SOURCE_WORKLOAD="${REPO_ROOT}/data/processed/mixed/smoke/workload.jsonl"
CLUSTER_CONFIG="${REPO_ROOT}/configs/cluster/benchmark_2worker.json"

if [[ ! -x "${HOST_PYTHON}" ]]; then
  echo "Python 3.11 environment not found: ${HOST_PYTHON}" >&2
  exit 1
fi
if [[ ! -f "${SOURCE_WORKLOAD}" ]]; then
  echo "Mixed workload not found: ${SOURCE_WORKLOAD}" >&2
  exit 1
fi
if [[ -e "${RUN_ROOT}" ]]; then
  echo "Saturation point already exists and will not be overwritten: ${RUN_ROOT}" >&2
  exit 1
fi

mkdir -p "${RUN_ROOT}"
PYTHONPATH="${REPO_ROOT}/python" "${HOST_PYTHON}" -m llmschedbench.cli \
  saturation workload \
  --input "${SOURCE_WORKLOAD}" \
  --output "${RUN_ROOT}/workload.jsonl" \
  --arrival-rate-rps "${ARRIVAL_RATE}" \
  > "${RUN_ROOT}/workload-summary.json"

"${REPO_ROOT}/scripts/apply-simulator-patch.sh"

docker run --rm --platform linux/amd64 \
  --volume "${REPO_ROOT}:/workspace" \
  --volume "${SIM_ROOT}:/app/LLMServingSim" \
  --workdir /app/LLMServingSim \
  llmschedbench-simulator \
  bash -lc '
    set -euo pipefail
    python -m serving \
      --cluster-config ../../workspace/configs/cluster/benchmark_2worker.json \
      --dtype bfloat16 \
      --block-size 16 \
      --request-routing-policy LOAD \
      --dataset ../../workspace/runs/saturation/rate-'"${RATE_LABEL}"'/workload.jsonl \
      --output /workspace/runs/saturation/rate-'"${RATE_LABEL}"'/simulator-results.csv \
      --run-id saturation-rate-'"${RATE_LABEL}"' \
      --log-interval 1 \
      --log-level WARNING
  ' > "${RUN_ROOT}/simulator.log" 2>&1

PYTHONPATH="${REPO_ROOT}/python" "${HOST_PYTHON}" -m llmschedbench.cli \
  saturation measure \
  --log "${RUN_ROOT}/simulator.log" \
  --cluster-config "${CLUSTER_CONFIG}" \
  --arrival-rate-rps "${ARRIVAL_RATE}" \
  --output "${RUN_ROOT}/utilization.json" \
  > "${RUN_ROOT}/measure-summary.json"

cat "${RUN_ROOT}/utilization.json"
