# What the real-GPU experiment showed

On September 5, 2026, LLMSchedBench completed 18 controlled runs on two independent
NVIDIA A6000 workers serving Qwen3-4B through vLLM: **3,456 measured requests, zero
failures**. Another 272 successful requests covered warm-up and load probing.

**Prefix sharing changed performance much more than routing-policy choice.**
Cache-Max modestly improved first-output latency in the reuse cases, but the
predicted popular-prefix bottleneck did not appear in this configuration.

![Individual repetitions and means](../artifacts/gpu-affinity/figures/prefix-affinity.png)

## Results

Values below are **means of three per-run p95 measurements**, not a pooled p95
and not confidence intervals. Each run contains 192 requests.

| Prefix pattern | Least-loaded p95 first output | Cache-Max p95 first output | Server cache hits, LL / CM | Maximum sampled waiting requests, LL / CM |
|---|---:|---:|---:|---:|
| Little reuse | 11.443 s | 11.276 s | 0% / 0% | 24 / 24 |
| Distributed reuse | 598 ms | 572 ms | 68.75% / 70.18% | 1 / 1 |
| Popular prefix | 514 ms | 492 ms | 69.27% / 70.83% | 1 / 0 |

The shared-prefix conditions had approximately 95% lower first-output latency
than the unique-prefix control at this operating point. **That is not a 95%
speedup from our routing policy:** both policies benefited from reuse.

Cache-Max's paired first-output reductions versus least-loaded were 12.69%,
0.65%, and 2.77% for distributed reuse; and 7.41%, 4.39%, and 1.44% for the popular
prefix. The no-reuse control varied from a 0.30% regression to a 5.22% improvement,
showing why small differences should not be treated as conclusive policy wins.
Three seeds do not establish a general ranking or statistical significance.

## What explains the measurements?

The unique-prefix control produced exactly zero measured cache hits, substantial
waiting queues, and long completion latencies. Reuse conditions produced roughly
69–71% cached-token fractions and almost no sampled waiting. These observations
are consistent with prefix reuse relieving prefill pressure. A cache-disabled
ablation on identical prompts would strengthen the causal attribution further.

The popular prefix did not force all traffic onto one worker. Its runs split
requests almost evenly: 93–99 requests on either worker out of 192. In the three
Cache-Max popular-prefix runs, the router's estimate considered **both workers
warm for 71–73% of routing decisions**. When cache estimates tie, Cache-Max uses
load as its tie-breaker. This supports an explanation for the missing bottleneck:
prefix replication reduced the conflict between affinity and load balancing.
The estimate is not per-request engine telemetry, so it cannot prove exact cache
residency or eviction behavior.

The three arrival schedules realized 3.47, 3.70, and 4.45 interarrival events/second
over their finite spans, despite sharing a nominal Poisson rate of 4/second.
Each policy pair used exactly the same schedule. This variation helps explain
large cross-seed latency differences near overload and motivates longer runs.

## Controls and practical limits

- Same model commit, container digest, GPU type, 4096-token inputs and 128-token
  outputs. Identical arrivals, suffixes and tenant labels within each seed.
- Three prefix conditions, three seeds, alternating policy order and rotating
  condition order. Kernel warm-up precedes each pair; prefix caches reset before
  every measured run.
- A separate 96-request probe chose the first tested rate whose p95 first output
  exceeded one second: 952 ms at 2 requests/s, 3003 ms at 4 requests/s. This selected
  an operating point, not a steady-state capacity estimate.
- Open-loop replay includes client dispatch lag in first-output timing. Maximum
  observed lag across the measured runs was 66.7 ms. Terminal stream and token
  counts were validated; server queried-token deltas matched all 786,432 input
  tokens in every run.
- Actual cache counters, approximate one-second queue/GPU samples, routing state,
  individual request logs, immutable inputs and checksums were retained.
- One model, two workers, one selected load and short synthetic workloads limit
  generalization. These are independent requests, not real coding-agent chains.
  Tenant labels share the same length distribution; this study does not establish
  fairness. Warm caches, more replicas, longer runs or a different cache-capacity
  constraint may behave differently. Brief queue peaks can fall between samples.

The first rental attempt failed before comparison because a root-owned output
directory blocked the normal user. It was automatically terminated. The fix
creates and checks the directory before launching workers. The replacement kept
the original termination deadline; the failed attempt is retained in the evidence.
Both attempts are confirmed terminated. The conservative compute estimate was
US$3.92 including the retry, before taxes and currency conversion; it is not an
invoice. See the [rental record](../artifacts/gpu-affinity/rental-outcome.json).

## Evidence and reproduction

- [Full run table](../artifacts/gpu-affinity/report.md)
- [Individual run summaries](../artifacts/gpu-affinity/results.json)
- [Paired differences and aggregate values](../artifacts/gpu-affinity/aggregate.json)
- [Request logs, compressed](../artifacts/gpu-affinity/requests.jsonl.gz)
- [Measurement source snapshot](../artifacts/gpu-affinity/measurement-source.tar.gz)
- [Protocol and reproduction instructions](GPU_AFFINITY_STUDY.md)
- [Interview walkthrough](GPU_AFFINITY_INTERVIEW.md)

The complete downloaded raw archive is retained locally at
`runs/gpu-affinity-retry-control/affinity-results.tar.gz`; its digest is included
in the published evidence manifest. Bulky raw workloads and telemetry remain out
of Git. The measurement source snapshot contains the code used for the runs;
`artifacts/gpu-affinity/plot-gpu-study.py` and `analysis-environment.json` specify
the final plotting code and library versions.
