#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SIM_ROOT="${REPO_ROOT}/third_party/LLMServingSim"
CLUSTER_CONFIG="${SIM_ROOT}/configs/cluster/single_node_multi_instance.json"
RUN_NAME="${CALIBRATION_RUN_NAME:-llama31-8b-rtxpro6000-v1}"
RUN_ROOT="${REPO_ROOT}/runs/calibration/${RUN_NAME}"
HOST_PYTHON="${LLMSCHEDBENCH_PYTHON:-${REPO_ROOT}/.venv/bin/python}"

if [[ ! -x "${HOST_PYTHON}" ]]; then
  echo "Python 3.11 environment not found: ${HOST_PYTHON}" >&2
  exit 1
fi

if [[ -e "${RUN_ROOT}" ]]; then
  echo "Calibration run already exists and will not be overwritten: ${RUN_ROOT}" >&2
  exit 1
fi

mkdir -p "${RUN_ROOT}"

PYTHONPATH="${REPO_ROOT}/python" "${HOST_PYTHON}" -m llmschedbench.cli calibrate workload \
  --cluster-config "${CLUSTER_CONFIG}" \
  --output "${RUN_ROOT}/workload.jsonl" \
  > "${RUN_ROOT}/workload-summary.json"

"${REPO_ROOT}/scripts/apply-simulator-patch.sh"

docker build --platform linux/amd64 \
  --file "${REPO_ROOT}/docker/simulator.Dockerfile" \
  --tag llmschedbench-simulator \
  "${REPO_ROOT}"

docker run --rm --platform linux/amd64 \
  --volume "${REPO_ROOT}:/workspace" \
  --volume "${SIM_ROOT}:/app/LLMServingSim" \
  --workdir /app/LLMServingSim \
  llmschedbench-simulator \
  bash -lc '
    set -euo pipefail
    (cd astra-sim && bash build/astra_analytical/build.sh)
    python -m serving \
      --cluster-config configs/cluster/single_node_multi_instance.json \
      --dtype bfloat16 \
      --block-size 16 \
      --no-enable-prefix-caching \
      --request-routing-policy RR \
      --dataset ../../workspace/runs/calibration/'"${RUN_NAME}"'/workload.jsonl \
      --output /workspace/runs/calibration/'"${RUN_NAME}"'/simulator-results.csv \
      --run-id calibration-'"${RUN_NAME}"' \
      --log-interval 1 \
      --log-level WARNING
  ' > "${RUN_ROOT}/simulator.log" 2>&1

PYTHONPATH="${REPO_ROOT}/python" "${HOST_PYTHON}" -m llmschedbench.cli calibrate estimate \
  --input "${RUN_ROOT}/simulator-results.csv" \
  --output "${RUN_ROOT}/service-rates.json" \
  --expected-workers 2 \
  --workload "${RUN_ROOT}/workload.jsonl" \
  --cluster-config "${CLUSTER_CONFIG}" \
  > "${RUN_ROOT}/estimate-summary.json"

echo "Calibrated service rates: ${RUN_ROOT}/service-rates.json"
