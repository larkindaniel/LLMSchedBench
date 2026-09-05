# Interview walkthrough: real-GPU routing experiment

## A concise explanation

> I built a trace-driven LLM routing benchmark and added a real vLLM backend.
> I then tested cache affinity against least-loaded routing on two A6000 GPUs.
> I controlled request lengths and arrival schedules, varied prefix sharing, and
> ran 18 comparisons across three seeds. All 3,456 measured requests succeeded.
> Shared prefixes reduced first-output latency dramatically, but both routers
> benefited. Cache-aware routing added modest improvements, and the predicted
> hotspot penalty did not appear. The routing logs showed that both replicas
> were often estimated to hold the popular prefix, allowing affinity routing to
> use load balancing as its tie-breaker. I reported that boundary rather than
> claiming a universal policy win.

Use this as a description of the work and evidence. Distinguish the components
you personally designed, implemented or reviewed, including use of coding tools;
do not imply unaided implementation if asked about authorship.

## Questions worth preparing for

**What was your hypothesis?**
Saved prefill computation might outweigh queueing at a warm worker under some
conditions, while concentrated popularity could make affinity harmful. This
experiment found reuse benefits and modest routing differences, but did not
establish the predicted harmful crossover.

**What did you actually control?**
4096 input tokens, 128 output tokens, model and image revisions, two independent
workers, and matched arrival schedules/suffixes within each seed. The experimental
changes were prefix-sharing pattern and routing policy. I reset prefix caches,
used separate warm-up calls, rotated conditions, and alternated policy order.

**Why open-loop requests?**
Sending the next request only after the previous one finishes would reduce load
when the system slows down. Independent scheduled arrivals preserve offered load.
I measured from scheduled arrival and recorded dispatch lag rather than hiding it.

**How did you know requests really succeeded?**
The client required a complete stream, nonempty output, and reported token usage
matching the requested input and output lengths. Failures remained failures; the
study had no automatic request retries. Every run retained a checked manifest.

**How does the router know what is cached?**
It estimates cache contents using a bounded LRU of completed prompt blocks.
Least-loaded uses outstanding requests, not the server's exact queue depth.
Those are practical approximations. I recorded them separately from actual
server cache counters and queue samples; engine evictions and in-flight reuse
are not directly visible to the router.

**Did your policy deliver a 95% speedup?**
No. The large improvement was between unique-prefix and shared-prefix workloads,
and occurred with both policies. Mean per-run p95 first output was 11.44 seconds
versus 0.60 seconds for least-loaded under those two conditions. Cache-Max's
additional differences were much smaller: 572 versus 598 ms for distributed
reuse, and 492 versus 514 ms for the popular prefix. These are descriptive means.

**Why didn't the popular prefix create a bottleneck?**
Requests remained almost evenly split. In Cache-Max's popular-prefix runs, its
estimate considered both workers warm for 71–73% of decisions. When both have an
equal estimated cache benefit, the policy prefers the less-loaded worker. That is
a plausible explanation supported by the routing logs; it is not proof of exact
per-request engine cache residency.

**Are three seeds enough?**
Enough for an initial repeatability check, not a general policy ranking. I show
each repetition. The no-reuse control itself varied by up to about 5% between
policies in one pair. Longer trials and more seeds are needed to separate small
policy effects from timing variation. Individual requests are not independent
replicates of the whole system experiment.

**What are the biggest limitations?**
One model, two GPUs, a single selected load, short synthetic fixed-length prompts,
and no true agent chains. The probe selected an operating point rather than
measuring steady-state capacity. A cache-disabled ablation would strengthen the
causal claim about reuse; one-second telemetry can miss short queue peaks.

**What would you test next?**
A load sweep and a controlled cache-topology or cache-capacity constraint, to test
when a popular prefix is available on only one worker. Keep that exploratory
follow-up separate from this completed protocol. Then test longer, heterogeneous
trace workloads and compare hardware trends with a matched simulator setup.

**What operational issue did you encounter?**
A fresh-host directory was accidentally owned by root, causing setup to fail
before comparison. Automatic cleanup terminated that attempt. I added an explicit
normal-user directory/write check and preserved the original rental deadline for
the replacement. Unit tests had not covered that deployment permission boundary.
