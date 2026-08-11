#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SIM_ROOT="${REPO_ROOT}/third_party/LLMServingSim"

"${REPO_ROOT}/scripts/apply-simulator-patch.sh"

docker build --platform linux/amd64 \
  --file "${REPO_ROOT}/docker/simulator.Dockerfile" \
  --tag llmschedbench-simulator \
  "${REPO_ROOT}"

mkdir -p "${REPO_ROOT}/runs"
docker run --rm --platform linux/amd64 \
  --volume "${REPO_ROOT}:/workspace" \
  --volume "${SIM_ROOT}:/app/LLMServingSim" \
  --workdir /app/LLMServingSim \
  llmschedbench-simulator \
  bash -lc '
    set -euo pipefail
    (cd astra-sim && bash build/astra_analytical/build.sh)

    rm -rf /tmp/llmschedbench-policy-build /tmp/llmschedbench-policy-stage
    cmake -S /workspace -B /tmp/llmschedbench-policy-build \
      -DBUILD_TESTING=OFF \
      -Dpybind11_DIR="$(python -m pybind11 --cmakedir)"
    cmake --build /tmp/llmschedbench-policy-build --parallel 2
    cmake --install /tmp/llmschedbench-policy-build \
      --prefix /tmp/llmschedbench-policy-stage
    cp -a /workspace/python/llmschedbench/. \
      /tmp/llmschedbench-policy-stage/llmschedbench/

    python -m serving \
      --cluster-config configs/cluster/single_node_single_instance.json \
      --dtype bfloat16 \
      --block-size 16 \
      --dataset workloads/example_trace.jsonl \
      --output /workspace/runs/upstream-example.csv \
      --num-reqs 10 \
      --run-id upstream-example \
      --log-level WARNING

    PYTHONPATH=/tmp/llmschedbench-policy-stage \
      python -m llmschedbench.simulator_runner \
      --policy least_loaded \
      --scenario /workspace/scenarios/smoke.yaml \
      --decision-log /workspace/runs/external-least-loaded-decisions.jsonl \
      --cluster-config configs/cluster/single_node_single_instance.json \
      --dtype bfloat16 \
      --block-size 16 \
      --dataset workloads/example_trace.jsonl \
      --output /workspace/runs/external-least-loaded.csv \
      --num-reqs 10 \
      --run-id external-least-loaded \
      --log-level WARNING

    cmp /workspace/runs/upstream-example.csv \
      /workspace/runs/external-least-loaded.csv
  '
