# Controlled GPU prefix-affinity study

Synthetic 4096-token prompts, 128-token outputs, two independent Qwen3-4B workers. Three seeds; fixed load selected by a separate probe. TTFT means scheduled release to first visible output. Individual repetitions are shown; no significance claim.

| Condition | Seed | Policy | Calls | p95 first output ms | p95 completion ms | Cache hit % | Worker calls | Peak waiting |
|---|---:|---|---:|---:|---:|---:|---|---|
| distributed | 1729 | cache_max | 192/192 | 411.9 | 4193.1 | 71.1 | [97, 95] | [0.0, 0.0] |
| distributed | 1729 | least_loaded | 192/192 | 471.8 | 4293.3 | 68.8 | [96, 96] | [0.0, 0.0] |
| distributed | 2718 | cache_max | 192/192 | 780.1 | 5842.8 | 69.9 | [97, 95] | [1.0, 1.0] |
| distributed | 2718 | least_loaded | 192/192 | 785.2 | 5846.5 | 68.8 | [96, 96] | [1.0, 1.0] |
| distributed | 3141 | cache_max | 192/192 | 523.4 | 5312.0 | 69.5 | [96, 96] | [1.0, 0.0] |
| distributed | 3141 | least_loaded | 192/192 | 538.3 | 5413.2 | 68.8 | [96, 96] | [1.0, 0.0] |
| hot_prefix | 1729 | cache_max | 192/192 | 429.5 | 3972.3 | 70.7 | [93, 99] | [0.0, 0.0] |
| hot_prefix | 1729 | least_loaded | 192/192 | 463.9 | 3988.9 | 68.8 | [97, 95] | [0.0, 0.0] |
| hot_prefix | 2718 | cache_max | 192/192 | 536.0 | 4088.6 | 71.1 | [95, 97] | [0.0, 0.0] |
| hot_prefix | 2718 | least_loaded | 192/192 | 560.6 | 4468.6 | 69.5 | [95, 97] | [0.0, 1.0] |
| hot_prefix | 3141 | cache_max | 192/192 | 510.9 | 4387.3 | 70.7 | [96, 96] | [0.0, 0.0] |
| hot_prefix | 3141 | least_loaded | 192/192 | 518.4 | 4404.9 | 69.5 | [96, 96] | [0.0, 0.0] |
| low_reuse | 1729 | cache_max | 192/192 | 6098.5 | 38484.5 | 0.0 | [96, 96] | [7.0, 7.0] |
| low_reuse | 1729 | least_loaded | 192/192 | 6080.3 | 38284.1 | 0.0 | [96, 96] | [9.0, 7.0] |
| low_reuse | 2718 | cache_max | 192/192 | 9042.2 | 42492.0 | 0.0 | [97, 95] | [13.0, 10.0] |
| low_reuse | 2718 | least_loaded | 192/192 | 9539.8 | 42850.4 | 0.0 | [96, 96] | [12.0, 11.0] |
| low_reuse | 3141 | cache_max | 192/192 | 18686.9 | 44937.0 | 0.0 | [95, 97] | [22.0, 24.0] |
| low_reuse | 3141 | least_loaded | 192/192 | 18709.3 | 44968.7 | 0.0 | [95, 97] | [22.0, 24.0] |

Cache fractions are differences of server token counters, not router estimates. Peak queues are sampled approximately once per second. Tenant labels use identical request lengths here; this does not test heterogeneous agent behavior. Warm-up precedes each pair; caches reset before each policy. Three seeds provide limited repeatability evidence. All attempted runs, including probe and warm-up runs, are retained.
