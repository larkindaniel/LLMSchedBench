# Controlled prefix-affinity study

## Question and scope

Does routing toward cached prefixes reduce first-visible-output latency, and when
can it concentrate work enough to offset the saved computation? This study tests
the existing `least_loaded` and `cache_max` C++ policies on two independent A6000
GPU workers, each serving the same pinned Qwen3-4B model through vLLM.

The rented host may have four GPUs when two-GPU instances are unavailable; only
GPU 0 and GPU 1 serve requests. The other GPUs do not participate in measurements.

## Protocol fixed before the comparison

- Three synthetic conditions: unique prefixes; eight approximately equally
  popular shared prefixes; one prefix selected with 80% probability, with the
  remainder distributed over seven others.
- Every request has 4096 input tokens and 128 generated tokens. Shared prefixes
  are 3072 tokens. All suffixes, lengths, tenant labels, and exponential arrival
  schedules are matched across conditions for each seed. Token identities are
  mapped deterministically into the model's non-special vocabulary.
- Three seeds: 1729, 2718, 3141. Two policies per condition gives 18 measured runs,
  each with 192 requests (3456 measured requests total).
- A separate low-reuse, least-loaded probe uses 96 requests at each of 2, 4, 8,
  and 16 requests/second. Select the first rate with p95 first-visible-output
  latency at least 1000 ms, otherwise use 16. Abort if a probe has failed calls.
  This selects a challenging operating point; it is not a steady-state capacity
  estimate. Do not retune the load after inspecting comparative results.
- Rotate condition order between seeds; alternate the first policy between pairs.
  Three pairs per condition cannot perfectly balance a two-policy order. Eight
  separate warm-up calls precede each pair; reset prefix caches before every
  measured run. Warm-up uses the same token lengths as measured calls.
- Requests arrive independently (open loop). There are no agent chains in this
  controlled experiment. Tenant labels have identical length distributions;
  per-tenant thresholds are descriptive and do not establish weighted fairness.

## Measurements and reproducibility

Store the resolved workload, model revision, image digest, vocabulary, individual
request times, actual token usage, client routing estimates, and SHA-256 manifests.
Sample vLLM Prometheus metrics and `nvidia-smi` approximately once per second.
Retain complete warm-up and probe outputs as well as all comparison attempts.

TTFT is scheduled arrival to first nonempty streamed text, including client
scheduling delay. Completion latency ends at the client after terminal stream
validation. Cache-hit fractions use differences in vLLM's token counters, not
router estimates. Final counters are sampled three seconds after run completion.
Queue maxima are sampled, so brief peaks may be missed. GPU utilization sampling
is separate from server queue and cache telemetry.

Report each repetition, the descriptive means, and paired differences. There are
only three seeds, so avoid significance claims. A result showing little routing
separation is still a valid finding for this configuration. Cache-Max uses a
completed-prompt LRU estimate and has no visibility into actual engine evictions
or in-flight reuse. Concurrent cold requests may populate the same prefix on both
workers; this can weaken the intended affinity bottleneck and must be checked in
the decision logs rather than assumed away.

## Host preparation

Create the parent run directory as the login user before invoking the worker
launcher with `sudo`:

```bash
mkdir -p runs
test -w runs
```

Otherwise a fresh host may receive a root-owned `runs` directory, preventing the
normal-user experiment runner from saving results. The first rental attempt
exposed this deployment bug and was automatically terminated before comparison.
The replacement preserves the original absolute termination deadline.

## Running after workers are healthy

```bash
python -m llmschedbench.gpu_study \
  --config runs/gpu-host/config.json \
  --output runs/gpu-affinity \
  --stop-at '<absolute ISO timestamp with timezone>'

python -m llmschedbench.gpu_study \
  --report runs/gpu-affinity --output artifacts/gpu-affinity

# Aggregate and plot locally after downloading; plotting requires matplotlib.
python scripts/summarize-gpu-study.py artifacts/gpu-affinity/results.json \
  --output artifacts/gpu-affinity/aggregate.json

python scripts/plot-gpu-study.py artifacts/gpu-affinity/results.json \
  --output artifacts/gpu-affinity/figures
```

The runner refuses existing output directories and halts on failed calls. It
requires 360 seconds remaining before starting another run (240-second run timeout
plus 120 seconds for overhead). The experiment stop timestamp is ten minutes before
the independent 90-minute provider termination deadline. These checks do not
replace provider-side termination or constitute a hard billing cap.
