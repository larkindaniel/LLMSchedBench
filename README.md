# LLMSchedBench

LLMSchedBench is a reproducible benchmark for evaluating request-routing and
admission policies across shared chat, coding-agent, and API/batch LLM
workloads.

The benchmark combines production-derived arrival and cache-reuse traces with
LLMServingSim execution modeling. Scheduling policy state and decisions live in
a small C++20 library exposed to Python through pybind11; Python owns data
normalization, experiment orchestration, metrics, and reporting.

## Status

The reproducible environment and first two implementation milestones are
operational. Both public sources are checksummed and normalized, the pinned
simulator backend builds and reproduces its chat and agentic examples, and the
smoke scenario deterministically generates a three-tenant LLMServingSim
workload. All four C++ policies and the CUSTOM-routing bridge are implemented
and unit tested. External `least_loaded` routing produces byte-identical output
to the simulator's built-in `LOAD` baseline. Unloaded simulator calibration now
supplies measured per-worker prefill and decode rates to latency-aware policies.
The fixed smoke mix reaches 90.05% average simulated NPU utilization at 2.0
top-level arrivals/s, establishing its initial `R_sat`. The full scenario/seed
matrix, reporting, and real-vLLM validation remain on the roadmap.

## Development setup

The supported development runtime is Python 3.11 with a C++20 compiler and
CMake 3.22 or newer.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
pytest
cmake -S . -B build/cpp-tests -G Ninja \
  -DCMAKE_PREFIX_PATH="$(python -m pybind11 --cmakedir)"
cmake --build build/cpp-tests
ctest --test-dir build/cpp-tests --output-on-failure
```

To inspect the command surface after installation:

```bash
llmschedbench --help
llmschedbench scenario compile scenarios/smoke.yaml
llmschedbench data mix scenarios/smoke.yaml
```

Raw public datasets and generated benchmark runs are intentionally excluded
from version control.

The simulator has a separate locked legacy image because its upstream runtime
is Linux AMD64:

```bash
docker build --platform linux/amd64 \
  -f docker/simulator.Dockerfile \
  -t llmschedbench-simulator .

./scripts/apply-simulator-patch.sh
```

The patch is stored outside the pinned submodule, applies idempotently, exposes
read-only request/worker snapshots to `PolicyRouter`, supports global deferral,
and does not modify ASTRA-Sim. Applying it intentionally makes the submodule
worktree show the reviewed compatibility diff.

See [the data schema](docs/DATA_SCHEMA.md) and
[the simulator regression record](docs/REGRESSIONS.md) for the exact semantics
and compatibility notes.

## Service-rate calibration

The benchmark cluster uses two simulated RTX PRO 6000 workers serving
Llama 3.1 8B. Generate a fresh immutable calibration run with:

```bash
CALIBRATION_RUN_NAME=<unique-name> ./scripts/calibrate-service-rates.sh
```

The workflow disables prefix caching, routes repeated input/output size points
round-robin, and spaces requests so every arrival follows all prior
completions. It fits prefill TTFT and post-first-token decode time separately,
then writes checksummed rates and fit diagnostics to
`runs/calibration/<unique-name>/service-rates.json`. Pass that file to the
external simulator runner with `--service-rates`.

The saturation-search point runner uses the same two-worker cluster and a
0/0/1 accounting meter to recover the fraction of simulated NPU active time:

```bash
./scripts/saturation-point.sh <arrival-rate-rps>
```
