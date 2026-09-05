# Running on rented GPUs

The first hardware backend supports `least_loaded` and `cache_max`. It uses the
existing C++ policy engine, asynchronous streaming HTTP, and the existing mixed
workload builder. No cloud account is required for development. This implementation
passed a 47-call single-A6000 validation on Lambda on September 5, 2026.
The subsequent two-A6000 comparison also completed all 47 calls per policy.

| Policy | Successful calls | p95 first visible output | p95 completion latency |
|---|---:|---:|---:|
| `least_loaded` | 47/47 | 528.85 ms | 15420.88 ms |
| `cache_max` | 47/47 | 509.36 ms | 16607.49 ms |

Both runs met every tenant's TTFT target. They used the same prepared workload,
model revision, and container digest, with prefix caches reset before each run.
This single-seed, fixed-order smoke test establishes successful real inference;
the small differences do not establish a policy winner. Raw results and checked
manifests are retained locally under
`runs/lambda-comparison-control/downloaded/runs/gpu-live-comparison`, with the
report at `artifacts/gpu-comparison/report.md` (generated artifacts are Git-ignored).

## Completed controlled study

The subsequent prefix-affinity study completed **18 runs of 192 requests**:
3,456 measured requests with zero failures. Another 272 requests covered probes
and warm-up. These values are means of three per-run p95 first-output timings:

| Prefix pattern | `least_loaded` | `cache_max` | Measured cached-token fraction (LL / CM) |
|---|---:|---:|---:|
| Little reuse | 11,443 ms | 11,276 ms | 0% / 0% |
| Distributed reuse | 598 ms | 572 ms | 68.75% / 70.18% |
| Popular prefix | 514 ms | 492 ms | 69.27% / 70.83% |

Both policies benefited strongly from prefix sharing. Cache-Max's extra benefits
were modest, and the popular-prefix bottleneck was not observed at the tested
load. These are descriptive results, not confidence intervals. The
[findings and figure](GPU_AFFINITY_FINDINGS.md) explain the measurements; the
[protocol](GPU_AFFINITY_STUDY.md) specifies reproduction and limitations.

Both rental attempts are confirmed terminated. The conservative compute estimate
was US$3.92 including the setup retry, before taxes and conversion, not an invoice.

## Rental target

Use a Linux x86-64 host with two identical NVIDIA GPUs, at least 24 GB VRAM per GPU,
Docker with NVIDIA Container Toolkit, Python 3.11, a C++20 compiler, and CMake.
Start with Qwen3-4B, one independent replica per GPU. Context length and concurrency
must be validated against available memory. The initial scenario is a 20-entry
smoke test, not a statistically adequate benchmark. Do not rent a long reservation
until this smoke test succeeds. Run the replay client on the GPU host.

The worker launcher starts containers; it does not provision or terminate a cloud
instance. The separate Lambda watchdog can request provider-side termination when
configured and scheduled. Set rental-duration and spending safeguards separately.
Stopping containers does not stop cloud billing; terminate the instance afterward.

## Install and prepare

Copy the project or the recorded measurement source snapshot to the rented host. Exclude
`.venv`, `build`, credentials, and simulator outputs. Install in a fresh environment:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[gpu,dev]'
python -m pytest tests/python/test_gpu.py -q
```

Prepare the normalized data using the existing pipeline, or transfer the
checksummed normalized `data/processed/qwen` and `data/processed/tracelab` trees:

```bash
llmschedbench data fetch qwen
llmschedbench data normalize qwen
llmschedbench data fetch tracelab
llmschedbench data normalize tracelab
```

## Launch workers

vLLM 0.15.0 was validated on Lambda A6000 hardware with the host-driver
workaround described below.
The launcher resolves its registry digest and the model commit, stores both in
`runs/gpu-host/config.json`, and passes the same model/tokenizer commit to both
workers. It also saves image metadata and `nvidia-smi -q`. Keep those files with
results. For a later reproduction, pass the saved image digest and model commit
through `--image` and `--revision` instead of resolving tags again.

```bash
mkdir -p runs
test -w runs
python scripts/gpu-workers.py --image vllm/vllm-openai:v0.15.0
```

If Docker requires `sudo`, create the parent `runs` directory as the login user
before running the launcher with elevated privileges. See the compatibility
finding below for the `--host-cuda-driver` flag used in the measured deployment.

Servers bind to host loopback ports 8000 and 8001. Development endpoints are enabled
for prefix-cache reset; do not expose these ports publicly. The launcher refuses to
replace its output directory or existing named containers. It removes containers
it launched if the second launch fails. Model loading continues asynchronously:

```bash
docker logs llmschedbench-worker-0
docker logs llmschedbench-worker-1
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8001/health
```

Wait for both health checks to succeed. If model loading fails, inspect logs and
adjust context length or memory configuration before replay. Avoid other clients
on these dedicated workers. Before each policy run the backend clears each worker's
prefix cache; it fails if the reset is rejected. This defines a cold-prefix-cache
experiment. Kernel warm-up effects still require separate warm-up experiments for
publication-quality measurements.

## Run and compare

```bash
llmschedbench run scenarios/real_gpu.yaml --backend vllm \
  --gpu-config runs/gpu-host/config.json --policy least_loaded --run-root runs/gpu-smoke
llmschedbench run scenarios/real_gpu.yaml --backend vllm \
  --gpu-config runs/gpu-host/config.json --policy cache_max --run-root runs/gpu-smoke
llmschedbench gpu-report \
  runs/gpu-smoke/real_gpu-r1p6-s1729-least-loaded-vllm \
  runs/gpu-smoke/real_gpu-r1p6-s1729-cache-max-vllm \
  --output artifacts/gpu-smoke/report.md
```

The backend prepares workloads without starting or building the simulator. It
refuses to overwrite a run directory, including failed runs. Use a new run root
for repetitions. `--workload-directory` accepts an existing checksum-validated
prepared workload; scenario name, seed, and arrival rate must match the CLI.
Reports validate artifact checksums and refuse to compare different workload or
configuration identities. Hardware reports are separate from simulator reports:
no synthetic simulator utilization or inferred cache-hit rate is presented as a
hardware measurement.

## Measurement contract and limitations

- Anonymous synthetic simulator tokens are mapped deterministically into the
  pinned tokenizer's non-special vocabulary. Shared prefixes remain identical;
  mapping can introduce token collisions. Prompts are not natural conversations.
- Output length is fixed with `ignore_eos`, and returned usage must match requested
  input/output lengths. Empty/malformed/truncated streams fail the request. SSE
  chunk counts are never treated as token counts.
- TTFT is scheduled release to first nonempty text chunk at the client. It includes
  client scheduling lag and HTTP overhead; it is not engine-only first-token time.
  Some tokens can decode to empty text, so this is a visible-output proxy.
- Top-level arrivals run independently. Agent calls wait for the previous call to
  finish plus its tool delay. Later prompts are reconstructed from the trace;
  actual generated output is not fed forward. Consequently, reuse involving a
  predecessor's generated output can differ from simulation. Initial bootstrap
  cache entries are not prepopulated.
- `least_loaded` uses client-tracked outstanding requests as a load proxy; it does
  not observe the engine's exact running/waiting split. `cache_max` uses a bounded
  LRU of completed prompt blocks, not vLLM's true cache state. Block size is aligned
  with the deployment. Evictions and in-flight prefix reuse are not observed.
- Worker Prometheus snapshots are saved before/after replay for later analysis.
  Routing estimates and raw server counters remain separate. The saved image and
  revision in the run configuration are deployment declarations; `/v1/models`
  checks the served model name, not a cryptographic attestation of the worker.
- Client overload is recorded as a failure rather than silently changing the
  arrival rate. Failed calls block later calls in the same agent session. There
  are no automatic retries. A run deadline cancels unfinished work and preserves
  a failed manifest. Restart dedicated workers after an interrupted run before
  another experiment, to ensure no old server work remains.
- Reports include p95 TTFT/completion latency and SLO goodput within the fixed
  offered-load window. Per-tenant summaries and raw requests are retained. Failed
  runs cannot enter a completed-run comparison. Cross-seed confidence intervals,
  server-side timing, fairness admission control, measured cache-location events,
  hardware service-rate calibration, and GPU utilization analysis are future work.

For a meaningful study, increase entries, measure this hardware's saturation,
compare high/low prefix reuse and hot-prefix workloads, repeat seeds and run order,
and record warm-up procedures. Do not compare Qwen GPU numbers directly to the
published Llama simulation as if only the backend changed.

## Finish the rental

Copy `runs/gpu-host`, hardware run directories, and the generated report back to
local storage. Remove the two named containers if needed:

```bash
docker rm -f llmschedbench-worker-0 llmschedbench-worker-1
```

Then terminate the rented instance through the provider. Retain model/image pins,
hardware metadata, token workloads, request logs, and manifests for reproduction.

API references: [vLLM completions protocol](https://docs.vllm.ai/en/v0.15.0/api/vllm/entrypoints/openai/completion/protocol/),
[vLLM metrics](https://docs.vllm.ai/en/stable/usage/metrics/).

## Staged validation and termination safeguards

For the first hardware validation, use `scenarios/real_gpu_single.yaml` and launch
with `scripts/gpu-workers.py --workers 1`. Prepare that workload locally before
renting. Start with a 30-minute termination deadline for this small validation;
installation delays may require another session, not extending the deadline.
Only proceed to the two-worker experiment after validation succeeds. The larger
experiment targets termination after 90 minutes, with an independent manual check
before the original two-hour maximum. Reconfirm capacity and price before launch.

`python -m llmschedbench.lambda_watchdog` implements termination of one exact
instance ID via Lambda's API. It verifies the instance name/type, waits for an
absolute UTC deadline, retries failed requests, and polls until status is
`terminated`. A successful termination request alone is not counted as verified.
It never launches instances. The watchdog requires a running, separately scheduled
process; adding this code does not activate a timer or establish a hard spend cap.

Before renting:

1. Create a temporary Lambda API key yourself. Lambda API keys have full API
   access, not just termination permission. Do not paste the key into chat.
2. Run `python3 scripts/save-lambda-key.py` locally and paste into its hidden
   prompt. The key is stored outside the repository with owner-only permissions.
3. Verify authentication read-only. After the instance ID is known, record its
   exact ID/name/type and absolute UTC termination deadline in a private run plan.
4. Preflight and schedule the watchdog immediately. Run an independent backup
   outside the GPU host, and record its deadline. Confirm both are armed before
   starting model installation. Do not rely on an OS shutdown command for billing.
5. Download results throughout the run, verify termination in Lambda, and revoke
   the temporary API key after all termination checks finish.

Example invocation for a plan created from a real instance (not a launch command):

```bash
python -m llmschedbench.lambda_watchdog \
  --plan runs/gpu-validation/termination-plan.json \
  --key-file ~/.config/llmschedbench/lambda-api-key --preflight-only
```

The plan requires `instance_id`, `instance_name`, `instance_type`, and
`terminate_at_utc` (ISO timestamp with timezone). Omit `--preflight-only` in the
scheduled watchdog process. Use the same immutable deadline after any restart.
Do not put API credentials in the plan, source code, command arguments, or logs.

API reference: [Lambda authentication and instance operations](https://docs.lambda.ai/public-cloud/cloud-api/).

## Lambda A6000 compatibility finding

On driver 580.105.08, the vLLM 0.15.0 image initially failed CUDA initialization
with error 803. Its bundled compatibility library was version 575.57.08.
`--host-cuda-driver` prioritizes `/lib/x86_64-linux-gnu` over that library. A GPU
tensor operation and the full 47-call replay both succeeded afterward. The flag
is persisted as `host_cuda_driver` in run configuration. Use it on this verified
configuration; it is not a recommendation to override compatibility libraries on
every host. The tested container digest is
`sha256:7764931211e5b408a10d6e289ab9eca8d6ecd105e9239cb2d528c2c4d0ad67b0`
and Qwen3-4B revision is `1cfa9a7208912126459214e8b04321603b3df60c`.

The independent cloud-init watchdog needs an explicit HTTP User-Agent on this
provider: default Python urllib requests returned HTTP 403, whereas
`llmschedbench-watchdog/1.0` successfully authenticated. This is included in
`scripts/lambda-remote-watchdog.py`. Verify API connectivity from both watchdog
hosts; a running process alone does not establish that termination will work.
