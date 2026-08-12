# LLMSchedBench

LLMSchedBench is a reproducible benchmark for evaluating request-routing and
admission policies across shared chat, coding-agent, and API/batch LLM
workloads.

The benchmark combines production-derived arrival and cache-reuse traces with
LLMServingSim execution modeling. Scheduling policy state and decisions live in
a small C++20 library exposed to Python through pybind11; Python owns data
normalization, experiment orchestration, metrics, and reporting.

## Status

The reproducible environment and first three bounded implementation milestones
are operational. Both public sources are checksummed and normalized, the pinned
simulator backend builds and reproduces its chat and agentic examples, and all
four C++ policies run through the tested CUSTOM-routing bridge. The completed
`overnight-m3` milestone contains 28 immutable simulator runs: a three-load,
four-policy matrix at seed 1729 and a five-seed policy comparison at 1.6
arrivals/s. Its traceable summaries, Student-t confidence intervals, figures,
and limitations are published in the
[bounded simulation report](artifacts/overnight-m3/report.md). The broader
scenario/ablation matrix and real-vLLM validation remain on the roadmap.

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

## Bounded serial milestone

The first benchmark milestone is a resumable 28-run subset: all four policies
at 1.0, 1.6, and 2.2 arrivals/s for seed 1729, plus four additional seeds at
1.6 arrivals/s. Workloads are shared by scenario/load/seed, while each policy
run receives an immutable directory with resolved configuration, request-map,
routing decisions, simulator output, logs, and checksums. Simulations run one
at a time.

```bash
llmschedbench sweep scenarios/balanced.yaml \
  --milestone overnight-m3 \
  --max-hours 9
```

Completed runs are checksum-validated and skipped on resume. Failed attempts
are retained under `runs/overnight-m3/failed/` for diagnosis rather than
overwritten.

From the supported Python 3.11/C++20/Docker environment, the complete bounded
milestone—including missing public-data preparation, calibration, the serial
sweep, validation, figures, and report—is reproduced with:

```bash
./scripts/reproduce-headline.sh
```

The command is resumable and exits unsuccessfully if the nine-hour limit is
reached before all 28 runs are valid. Re-run the same command to continue from
the checksum-validated artifacts. Use `--preflight-only` to validate the local
environment and test suite without starting a simulator run.

The completed compact result set is under `artifacts/overnight-m3/`. Raw run
directories remain ignored because they total substantially more data and are
reproducible from checksummed inputs. At the five-seed 1.6-arrivals/s slice,
`cache_max` and `slo_guarded_affinity` produced a mean p95 TTFT of 238.65 ms,
versus 267.57 ms for `least_loaded` and `weighted_fair`; the corresponding 95%
intervals overlap, so this bounded experiment does not establish a decisive
ranking. See the report for the full metrics and scope limits.
