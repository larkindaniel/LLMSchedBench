# Data schema and mixed-trace semantics

The canonical Parquet schema has one row per LLM call and is versioned by
`schema_version`. Source-derived fields remain separate from controlled
scenario assumptions: tenant weights, SLOs, traffic proportions, selection
limits, and arrival rate live in scenario files.

## Prefix representation

Qwen rows store their source 16-token block hashes directly as stable block
identities. Conversion to LLMServingSim expands each identity to 16
deterministic synthetic token IDs, so equal source hashes produce equal token
prefixes without retaining prompt content.

TraceLab contexts can contain hundreds of thousands of blocks, so normalized
Parquet uses lossless compact descriptors instead of eagerly enumerating every
identity:

- `tracelab-bootstrap:<session>:<count>` creates the source-reported cached
  context for the first selected request in a session.
- `tracelab-reuse:<request>:<count>` reuses the leading blocks of the named
  predecessor context.
- `tracelab-append:<request>:<count>` creates stable blocks appended by the
  current request.

Only the selected sessions are expanded when simulator JSONL is generated.
This keeps complete-source normalization bounded while preserving the exact
block continuity required by prefix-cache simulation.

## Controlled mixed traces

`llmschedbench data mix` selects contiguous, seeded windows from each tenant's
source-ordered entries. A TraceLab session is indivisible: selecting one entry
includes every call in that closed-loop session.

The command allocates an exact entry count using largest remainders. For the
smoke scenario, 20 entries at 60/25/15 become 12 chat requests, 5 coding-agent
sessions, and 3 API/batch requests. It then resamples those entries onto the
scenario's aggregate `arrival_rate_rps`, preserving source order within each
tenant. This resampling is a controlled benchmark assumption; the untouched
normalized source Parquet retains the original timestamps.

The smoke scenario bounds coding-session size only to keep the local end-to-end
regression fast. Benchmark scenarios should state any such selection bounds and
include unbounded sensitivity runs.
