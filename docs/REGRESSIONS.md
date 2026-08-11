# Simulator regression record

## Pinned environment

- LLMServingSim commit: `2c2042ce4bf1b0283ebeed1db95db6f25e3e7511`
- Upstream base image digest:
  `sha256:567a0f020f18de9b7391d620041d2fd10120d6e0f1485b5110e0db79c2849305`
- Runtime architecture: Linux AMD64 under Docker Desktop emulation on Apple
  Silicon
- Resolved Python environment: `docker/simulator-requirements.txt`

The analytical backend compiled successfully. Both congestion-aware and
congestion-unaware binaries were produced.

## Unloaded service-rate calibration

The two-worker Llama 3.1 8B / RTX PRO 6000 benchmark cluster was calibrated
with 16 isolated requests. Four prompt sizes (128, 512, 2,048, and 4,096
tokens) and four output sizes (32, 64, 128, and 256 tokens) were delivered to
each worker through round-robin routing. Prefix caching was disabled, and
every arrival occurred after all earlier requests completed.

- Simulator-results SHA-256:
  `ed452a7a68125b50770e84fc805c8eaee80f1d398028d2457a2d32109ffd1e1f`
- Workload SHA-256:
  `e6a5303376adbd7a70c8834aa2b6f745b8ee62cc97939aa2ff35153df94e7375`
- Worker 0 prefill / decode: 23,927.74 / 88.7049 tokens/s
- Worker 1 prefill / decode: 23,878.78 / 88.7049 tokens/s
- Prefill OLS R²: 0.9977 / 0.9974
- Decode OLS R²: 0.99999997 on both workers
- Calibrated-rate file SHA-256:
  `6344c64abe04845f8cdeb0cc410cceabc91e9deccc812f9dd5ae7cbcd7d51bac`

The prefill rate is the inverse slope of TTFT against input length. The decode
rate is the inverse slope of post-first-token latency against remaining output
tokens. Fit intercepts and all source checksums are retained in the generated
`service-rates.json`.

## Initial saturation point

The 20-entry, 47-call mixed smoke workload was rescaled to 2.0 top-level
arrivals/s and run through built-in `LOAD` on two Llama 3.1 8B / RTX PRO 6000
workers. A neutral 0/0/1 power-accounting configuration makes reported NPU
energy numerically equal to active NPU-seconds without changing execution.

- Arrival rate: 2.0 top-level entries/s
- Simulated duration: 42.802925091 s
- Active NPU time: 77.09 NPU-s
- Average utilization: 90.0523%
- Workers: 2
- Simulator-log SHA-256:
  `5c783e06ab51d1839924c16b5db16910748a2c744edc89f5cde227947929c91b`
- Metered cluster-config SHA-256:
  `46d11cca95734161afc714ca28c6bbcbe8bef766a0ab408758600f544d990ca9`

This defines the initial smoke-mix `R_sat` as 2.0 arrivals/s. The controlled
experiment loads are therefore 1.0, 1.6, and 2.2 arrivals/s for 0.5×, 0.8×,
and 1.1× `R_sat`, respectively.

## Included single-instance example

The 10-request example completed twice with byte-identical CSV output:

- Rows including header: 11
- SHA-256: `3698da9319250353de62e69ff947ac66d864e7a00a7375cf609320d80fbb9934`
- Requests: 10
- Simulated latency: 1.665 s
- Total input/generated tokens: 120 / 591
- Request throughput: 6.01 requests/s
- Mean TTFT: 15.73 ms
- Mean TPOT: 11.15 ms

The pinned repository's checked-in example CSV has a different hash and
request token counts. The upstream launcher installs unpinned dependencies, so
that historical artifact is not an exact numerical oracle. LLMSchedBench uses
the repeated output from the locked environment as its regression baseline.

## Included agentic MoE example

The included 20-call SWE-bench-style agentic session completed against the
two-instance MoE configuration:

- Rows including header: 21
- SHA-256: `dda2817867c048da0aa86777676c0601e9abd00c921923671c6bfb33bce4aef6`
- Simulated latency: 36.636 s
- Total input/generated tokens: 143,284 / 4,402
- Request throughput: 0.55 requests/s
- Prefix-cache hits: 119,712 tokens (83.55%)
- Instance 0 mean / median / p99 TTFT: 82.95 / 65.28 / 191.63 ms
- Instance 1 mean / median / p99 TTFT: 83.31 / 70.82 / 189.03 ms

Together with the single-instance example, this covers both an independent
chat workload and a prefix-reusing, closed-loop agentic workload from the
pinned upstream simulator.

## Upstream compatibility findings

- Chakra 0.0.4 declares Protobuf 6, but its generated code requires Protobuf
  7.35.1. The simulator image deliberately installs the matching generated-code
  runtime and installs Chakra with `--no-deps`.
- The included `serving/run.sh` has a commented shebang and must be invoked with
  `bash`.
- Logging intervals greater than one second make the pinned reporting path
  compute an integer rate of zero and fail after simulation. Regression runs
  retain the supported one-second interval.

These are environment/launcher workarounds only. ASTRA-Sim and the pinned
LLMServingSim source have not been modified.

## CUSTOM-routing compatibility patch

The reviewed patch is stored at `patches/llmservingsim-custom-routing.patch`
(SHA-256
`05a1c0fc315cb8bcacf5a95988bd7e99eaeb42b2ced744d2164108703a899db9`).
It applies cleanly to the pinned commit and changes only the serving entry
point, router, and router tests. Three patch-level tests confirm unchanged LOAD
tie behavior, read-only CUSTOM snapshots with deferral, and end-to-end routing
through LLMSchedBench's `PolicyRouter`. ASTRA-Sim is not touched.

The external `least_loaded` policy was then run against the included
single-instance trace and compared with the built-in `LOAD` policy:

- External CSV SHA-256:
  `3698da9319250353de62e69ff947ac66d864e7a00a7375cf609320d80fbb9934`
- Built-in CSV SHA-256:
  `3698da9319250353de62e69ff947ac66d864e7a00a7375cf609320d80fbb9934`
- Comparison: byte-identical, including all 10 request rows
- Decision records: 10
- Decision-log SHA-256:
  `bf374702a4940f196da0aa36ef9601e9e84e4f95e4d102dab9e2bf6b133ab39e`

This establishes that the compatibility hook and C++ bridge preserve the
upstream least-load baseline before policy experiments begin.

## LLMSchedBench mixed smoke trace

The deterministic 20-entry smoke workload completed through the pinned
single-instance simulator and wrote 47 unique request rows (coding sessions
expand into multiple closed-loop calls):

- Output CSV SHA-256:
  `bd4246f9d9c89522ff502e7d37eff090f8918643c1e74f09b4b835d84b133dfd`
- Simulated latency: 44.942 s
- Total input/generated tokens: 192,205 / 13,617
- Request throughput: 1.05 requests/s
- Prefix-cache hits: 122,848 tokens (63.92%)
- Mean / median / p99 TTFT: 93.36 / 38.53 / 366.84 ms
- Mean TPOT: 15.18 ms

This is a pipeline regression, not a policy comparison or headline benchmark
result. It uses the pinned simulator's built-in LOAD router on one configured
instance.
