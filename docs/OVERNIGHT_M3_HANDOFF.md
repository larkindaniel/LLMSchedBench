# LLMSchedBench overnight-m3 handoff

Last updated: 2026-08-11 03:27 EDT

## Objective and boundary

Finish the bounded `overnight-m3` milestone locally and serially: 28 immutable
simulator runs, per-run metrics, the five-seed confidence-interval slice,
comparison plots, a technical report, full Python/C++ verification, and a tested
one-command reproduction workflow. Do not start the deferred scenario matrix,
fairness/affinity ablations, cloud work, or real-GPU validation in this milestone.

## Repository state at handoff

- Repository: `/Users/daniellarkin/Documents/ChatGPT/LLM-bench-project`
- Branch: `codex/overnight-m3`
- Baseline on `main`: `7ed3487`
- Latest committed checkpoint: `de329b5` (`results: checkpoint primary policy matrix`)
- Completed immutable run directories: 16 of 28
- Active run at this timestamp:
  `balanced-r1p6-s1731-least-loaded.incomplete`
- Sweep failures: none
- Primary 3-load x 4-policy matrix: complete and committed
- Remaining work at this timestamp: 12 runs including the active run, final
  report/CI verification, full tests, reproduction preflight, final audit, and
  final commits
- Sweep start: 2026-08-10 23:51 EDT
- Runner launch cap: 7.5 hours, ending around 2026-08-11 07:21 EDT

`progress.json` reports 15 completed plus one skipped because
`balanced-r1-s1729-least-loaded` was already complete and checksum-validated
before this sweep invocation. The filesystem total of 16 completed run
directories is therefore correct.

The expected uncommitted state is:

```text
 M scripts/reproduce-headline.sh
 m third_party/LLMServingSim
```

The script edit adds the C++ suite to reproduction preflight and still needs its
final test and commit. The submodule is intentionally dirty because the parent
repository's tracked compatibility patch is applied in place. Do not commit the
derived submodule edits directly. The patch is
`patches/llmservingsim-custom-routing.patch`, currently documented with SHA-256
`05a1c0fc315cb8bcacf5a95988bd7e99eaeb42b2ced744d2164108703a899db9`.

## First checks tomorrow

Open Terminal and run:

```bash
cd "/Users/daniellarkin/Documents/ChatGPT/LLM-bench-project"
git status --short --branch
date
find runs/overnight-m3/runs -mindepth 1 -maxdepth 1 -type d \
  ! -name '*.incomplete' | wc -l
find runs/overnight-m3/runs -mindepth 1 -maxdepth 1 -type d \
  -name '*.incomplete' -print
jq '{state, started_at, completed_at, wall_seconds,
     completed_count:(.completed|length), skipped_count:(.skipped|length),
     failed, remaining_count:(.remaining|length), stopped_for_deadline}' \
  runs/overnight-m3/progress.json
ps -axo pid,etime,command | rg \
  'llmschedbench sweep|docker run.*llmschedbench-simulator' | rg -v 'rg '
```

Do not launch another sweep if the last command shows the benchmark sweep or
its `llmschedbench-simulator` container. `llmschedbench-sim-regression` is a
separate long-lived regression container and is not a concurrent benchmark run.

If a run is active, inspect progress without interrupting it:

```bash
active=$(find runs/overnight-m3/runs -mindepth 1 -maxdepth 1 -type d \
  -name '*.incomplete' -print -quit)
rg '^\[[0-9.]+s\]' "$active/simulator.log" | tail -n 1
tail -n 30 "$active/simulator.log"
```

## Safe resume path

If no sweep process is active and `progress.json` is partial or runs remain,
resume from the same milestone directory. Completed runs are revalidated before
they are skipped; an incomplete attempt is not treated as complete.

```bash
cd "/Users/daniellarkin/Documents/ChatGPT/LLM-bench-project"
source .venv/bin/activate
llmschedbench sweep scenarios/balanced.yaml \
  --milestone overnight-m3 \
  --max-hours 3.0 \
  --no-prepare
```

The `3.0`-hour window is a conservative fresh recovery budget, not permission
to overlap the original sweep or start deferred experiments. If preserving the
original overnight wall-clock deadline is mandatory and 07:21 EDT has passed,
do not launch more simulations; instead use the verified partial-results
handoff path below.

If an `.incomplete` directory remains after an interrupted process, leave it in
place. The orchestrator archives stale/failed attempts rather than overwriting
completed data. Never manually rename an incomplete directory to completed.

## Final report path after 28 runs

Generate the report only after the sweep process exits:

```bash
cd "/Users/daniellarkin/Documents/ChatGPT/LLM-bench-project"
source .venv/bin/activate
llmschedbench report runs/overnight-m3 \
  --output artifacts/overnight-m3 \
  --require-complete
```

The command must exit zero. Then verify the core completion invariants:

```bash
test "$(wc -l < artifacts/overnight-m3/run-summaries.jsonl | tr -d ' ')" = 28
jq -e '.completed_runs == 28 and .planned_runs == 28 and
  (.missing_runs|length) == 0 and
  ([.confidence_intervals[].n] | all(. == 5))' \
  artifacts/overnight-m3/aggregate.json >/dev/null
xmllint --noout \
  artifacts/overnight-m3/figures/load-sweep.svg \
  artifacts/overnight-m3/figures/ci-slice.svg
```

The report must continue to label the 12-run seed-1729 load matrix as
descriptive single-seed evidence and the 1.6-arrivals/s slice as five-seed 95%
Student-t confidence intervals.

## Required final verification

Run the one-command preflight, Python suite, lint, and C++ suite:

```bash
cd "/Users/daniellarkin/Documents/ChatGPT/LLM-bench-project"
source .venv/bin/activate
./scripts/reproduce-headline.sh --preflight-only
pytest -q
ruff check .
cmake -S . -B build/headline-tests \
  -DCMAKE_PREFIX_PATH="$(python -m pybind11 --cmakedir)"
cmake --build build/headline-tests
ctest --test-dir build/headline-tests --output-on-failure
```

Verify the compatibility patch is exactly represented and already applied:

```bash
git -C third_party/LLMServingSim apply --reverse --check \
  ../../patches/llmservingsim-custom-routing.patch
shasum -a 256 patches/llmservingsim-custom-routing.patch
git -C third_party/LLMServingSim status --short
```

Expected submodule paths are `serving/__main__.py`, `serving/core/router.py`,
and `tests/test_custom_router.py`. The Python patch test also enforces this:

```bash
pytest -q tests/python/test_simulator_patch.py
```

Before each final commit, inspect the staged files and reject secrets or bulky
raw outputs:

```bash
git diff --check
git diff --stat
git diff --cached --check
git diff --cached --stat
git status --short
```

Commit only compact summaries, manifests, plots, report sources, documentation,
and the tested reproduction-script edit. Raw `runs/`, datasets, builds, caches,
and environment files are intentionally ignored.

Suggested final commits:

```bash
git add scripts/reproduce-headline.sh docs/OVERNIGHT_M3_HANDOFF.md
git commit -m "test: verify headline reproduction workflow"
git add artifacts/overnight-m3 docs/ROADMAP.md README.md
git commit -m "results: publish five-seed milestone report"
```

Adjust the staged file list to the actual diff; do not stage unrelated changes
or the dirty submodule contents. Do not push, rewrite history, force anything,
or discard existing work.

## Partial-results path if the deadline stops the sweep

Let the active run finish. Do not launch another run after the deadline guard
stops the sweep. Regenerate the report without `--require-complete`, verify its
reported completed/missing counts against the run directories, and commit the
valid compact checkpoint. Record the exact remaining run keys from:

```bash
jq '.remaining' runs/overnight-m3/progress.json
```

Do not describe preliminary `n < 5` intervals as completed confidence
intervals, and do not fabricate or duplicate missing results.

## Existing commit history for this milestone

```text
7ed3487 chore: checkpoint reproducible benchmark baseline
d4e254c feat: add resumable serial experiment sweep
3bde97e fix: use supported simulator cleanup option
d37a561 fix: prioritize primary load matrix in serial sweep
18c4bad feat: add traceable metrics and static reporting
164f4ee feat: add one-command headline reproduction
ed549c7 results: checkpoint low-load policy matrix
de329b5 results: checkpoint primary policy matrix
```

## Definition of done

- Exactly 28 unique completed run directories and no unexplained failures.
- Four policies at 1.0, 1.6, and 2.2 arrivals/s for seed 1729.
- Four policies at 1.6 arrivals/s for seeds 1729 through 1733.
- Every reported value traces to scenario, load, seed, policy, run manifest,
  workload checksum, decision log, and simulator result checksum.
- Every confidence-interval row has `n = 5`.
- Report tables agree with machine-readable summaries; both SVGs parse.
- Full Python, lint, C++, patch, and reproduction-preflight checks pass.
- Compact final artifacts and documentation are committed on
  `codex/overnight-m3`.
- Final Git status is clean except for the fully explained, reproducible dirty
  `third_party/LLMServingSim` submodule.
- Final handoff lists completed/failed/remaining counts, elapsed time, all
  created commits, and the exact reproduction command.
