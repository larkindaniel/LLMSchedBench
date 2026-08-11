"""Deterministic synthetic prefix reconstruction."""

from __future__ import annotations

import hashlib

TRACELAB_BOOTSTRAP_PREFIX = "tracelab-bootstrap-v1"
TRACELAB_REUSE_PREFIX = "tracelab-reuse-v1"
TRACELAB_APPEND_PREFIX = "tracelab-append-v1"


def synthetic_block_tokens(block_id: str, block_size: int = 16) -> tuple[int, ...]:
    """Expand a stable block identifier into simulator-safe token IDs."""
    if not block_id:
        raise ValueError("block_id must be non-empty")
    if block_size <= 0:
        raise ValueError("block_size must be positive")

    tokens = []
    for position in range(block_size):
        payload = f"llmschedbench:{block_id}:{position}".encode()
        digest = hashlib.blake2b(payload, digest_size=8).digest()
        tokens.append(int.from_bytes(digest, "big", signed=False) % 2_000_000_000)
    return tuple(tokens)


def qwen_block_tokens(block_hash: str, block_size: int = 16) -> tuple[int, ...]:
    """Expand a source block hash into stable synthetic token IDs.

    Token IDs are derived independently for each position. This preserves exact
    source block equality without treating the source hash as a vocabulary ID.
    """
    return synthetic_block_tokens(f"qwen:{block_hash}", block_size)


def tracelab_block_id(session_id: str, block_index: int) -> str:
    """Create a stable session-scoped block identifier for TraceLab context."""
    if not session_id:
        raise ValueError("session_id must be non-empty")
    if block_index < 0:
        raise ValueError("block_index must be non-negative")
    digest = hashlib.sha256(f"tracelab:{session_id}:{block_index}".encode()).hexdigest()
    return f"tracelab:{digest[:24]}"


def tracelab_output_block_id(request_id: str, block_index: int) -> str:
    """Return the output block ID also used when a later round reuses it."""
    if not request_id:
        raise ValueError("request_id must be non-empty")
    if block_index < 0:
        raise ValueError("block_index must be non-negative")
    digest = hashlib.sha256(
        f"tracelab-output:{request_id}:{block_index}".encode()
    ).hexdigest()
    return f"tracelab-output:{digest[:24]}"


def tracelab_bootstrap_descriptor(session_id: str, count: int) -> str:
    """Encode cached blocks that predate the first released session round."""
    if not session_id:
        raise ValueError("session_id must be non-empty")
    if count <= 0:
        raise ValueError("count must be positive")
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    return f"{TRACELAB_BOOTSTRAP_PREFIX}|{session_hash}|{count}"


def tracelab_reuse_descriptor(previous_request_id: str, count: int) -> str:
    """Encode reuse of a prefix from the preceding request context."""
    if not previous_request_id:
        raise ValueError("previous_request_id must be non-empty")
    if count <= 0:
        raise ValueError("count must be positive")
    return f"{TRACELAB_REUSE_PREFIX}|{previous_request_id}|{count}"


def tracelab_append_descriptor(request_id: str, count: int) -> str:
    """Encode new input blocks introduced by one request."""
    if not request_id:
        raise ValueError("request_id must be non-empty")
    if count <= 0:
        raise ValueError("count must be positive")
    return f"{TRACELAB_APPEND_PREFIX}|{request_id}|{count}"


def parse_tracelab_descriptor(value: str) -> tuple[str, str, int]:
    """Parse one compact TraceLab prefix recipe descriptor."""
    try:
        kind, identity, raw_count = value.split("|", maxsplit=2)
        count = int(raw_count)
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid TraceLab prefix descriptor: {value!r}") from error
    if kind not in {
        TRACELAB_BOOTSTRAP_PREFIX,
        TRACELAB_REUSE_PREFIX,
        TRACELAB_APPEND_PREFIX,
    }:
        raise ValueError(f"unknown TraceLab prefix descriptor: {kind!r}")
    if not identity or count <= 0:
        raise ValueError(f"invalid TraceLab prefix descriptor: {value!r}")
    return kind, identity, count
