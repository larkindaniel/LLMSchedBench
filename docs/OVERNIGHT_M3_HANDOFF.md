# LLMSchedBench overnight-m3 final handoff

Last updated: 2026-08-11 20:27 EDT

## Outcome

The bounded `overnight-m3` milestone is complete on branch `overnight-m3`.

- Completed immutable run directories: 28 of 28
- Failed runs: 0
- Remaining runs: 0
- Incomplete run directories: 0
- Active benchmark processes: 0
- Policies: `least_loaded`, `cache_max`, `weighted_fair`, and
  `slo_guarded_affinity`
- Seed-1729 descriptive matrix: all four policies at 1.0, 1.6, and 2.2
  arrivals/s
- Five-seed slice: all four policies at 1.6 arrivals/s for seeds 1729–1733
- Confidence intervals: two-sided 95% Student-t intervals with `n = 5` in
  every policy/metric group

The first long sweep invocation reached its 7.5-hour active-time guard after
run 27 and exited cleanly with no simulator running. A fresh serial invocation
checksum-validated and skipped those 27 runs, then completed only the final
run. No runs were overwritten or executed concurrently.

## Timing

The earliest run started at `2026-08-11T03:27:12.593574Z` and the final run
completed at `2026-08-12T00:22:14.995567Z`, a calendar span of 20 h 55 m 02 s.
That span includes macOS sleep and pauses and should not be interpreted as
active compute time. The sum of all 28 per-run `wall_seconds` values is
28,620.07 seconds, or 7 h 57 m 00 s.

## Results and interpretation

At the five-seed 1.6-arrivals/s slice:

| Policy pair | Mean p95 TTFT [95% CI] ms | Mean prefix hit [95% CI] | Mean goodput [95% CI] calls/s | Mean horizon starvation [95% CI] |
|---|---:|---:|---:|---:|
| `least_loaded`, `weighted_fair` | 267.57 [201.97, 333.16] | 44.45% [35.39%, 53.52%] | 1.76 [1.33, 2.19] | 29.44% [23.18%, 35.70%] |
| `cache_max`, `slo_guarded_affinity` | 238.65 [170.98, 306.33] | 52.80% [40.48%, 65.11%] | 1.84 [1.35, 2.33] | 26.83% [20.24%, 33.41%] |

All four policies achieved 100% TTFT-SLO attainment among the measured calls.
The intervals overlap substantially, so this bounded experiment does not
establish a statistically decisive policy ranking. The two policy pairs have
equal aggregate metrics in this slice: `least_loaded` and `weighted_fair` made
the same admit/worker choices in all five seeds; `cache_max` and
`slo_guarded_affinity` did so in four seeds, while the final seed swapped two
worker assignments without changing aggregate metrics. This is evidence that
the controlled base mix has limited power to separate those policies and
motivates the deferred affinity/fairness and workload-reuse ablations.

The 12-run seed-1729 load matrix is descriptive single-seed evidence, not an
interval estimate. All results are Linux AMD64 simulator measurements under
emulation on Apple Silicon, not real-GPU measurements. SLO thresholds, the
60/25/15 tenant mix, cluster shape, and service rates are controlled benchmark
assumptions. Horizon starvation is intentionally separate from TTFT-SLO
attainment and exposes work not completed by the fixed offered-load horizon.

## Published artifacts

Compact version-controlled outputs are under `artifacts/overnight-m3/`:

- `report.md`: technical report, metric definitions, and limitations
- `run-summaries.jsonl`: 28 traceable per-run summaries
- `aggregate.json`: nine five-seed interval metrics for each policy
- `figures/load-sweep.svg`: 12-point descriptive load sweep
- `figures/ci-slice.svg`: four-policy five-seed p95-TTFT intervals
- `manifest.json`: sizes and SHA-256 hashes for every compact artifact

An independent audit verified:

- the exact 28-key scenario/load/seed/policy matrix and uniqueness;
- all 196 raw run-output sizes and SHA-256 hashes;
- all 28 summary-to-manifest/workload/decision/result hash links;
- 36 independently recomputed Student-t intervals;
- exact agreement for all 12 descriptive and four CI report rows;
- all five compact artifact sizes and hashes; and
- valid SVG XML with 12 load points and four `n = 5` CI groups.

Raw runs, public datasets, builds, virtual environments, and caches remain
ignored because they are bulky and reproducible from checksummed inputs.

## Verification record

All required checks passed after the final report was generated:

```text
./scripts/reproduce-headline.sh --preflight-only  PASS
pytest -q                                      47 passed
ruff check .                                   PASS
cmake configure/build                          PASS
ctest --test-dir build/headline-tests          1 passed
pytest -q tests/python/test_simulator_patch.py  2 passed
patch reverse-apply check                      PASS
report/data/hash/SVG audit                     PASS
```

The applied compatibility patch is
`patches/llmservingsim-custom-routing.patch`, with SHA-256:

```text
05a1c0fc315cb8bcacf5a95988bd7e99eaeb42b2ced744d2164108703a899db9
```

It covers only `serving/__main__.py`, `serving/core/router.py`, and
`tests/test_custom_router.py` in the pinned submodule. The parent repository is
expected to show only `m third_party/LLMServingSim` after this handoff commit;
that dirtiness is intentional, reproducible, and not staged in the parent.

## Exact reproduction

From the repository root in the supported Python 3.11/C++20/CMake/Docker
environment:

```bash
source .venv/bin/activate
./scripts/reproduce-headline.sh
```

The command is serial, resumable, checksum-validates completed runs, and exits
unsuccessfully if its time window ends before all 28 runs and the complete
report are valid. Re-run the same command to continue safely.

## Milestone commits

```text
7ed3487 chore: checkpoint reproducible benchmark baseline
d4e254c feat: add resumable serial experiment sweep
3bde97e fix: use supported simulator cleanup option
d37a561 fix: prioritize primary load matrix in serial sweep
18c4bad feat: add traceable metrics and static reporting
164f4ee feat: add one-command headline reproduction
ed549c7 results: checkpoint low-load policy matrix
de329b5 results: checkpoint primary policy matrix
fbe5e52 docs: add overnight milestone handoff
a0a0424 test: verify headline reproduction workflow
462eb89 results: publish five-seed milestone report
```

The final handoff documentation commit is the current `HEAD`; resolve its hash
with `git rev-parse --short HEAD`.

## Deferred work

The following remain explicitly outside this milestone:

- the broader 0.5×/0.8×/1.1× scenario matrix;
- agent-burst, low-prefix-reuse, and fairness/affinity ablations;
- real-vLLM replay on two identical 24 GB-or-larger GPU workers; and
- cloud provisioning or spending.

No cloud resources, paid services, credentials, pushes, or remote changes were
used for this milestone.
