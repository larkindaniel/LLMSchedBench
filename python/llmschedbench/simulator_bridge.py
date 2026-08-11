"""Adapter between LLMServingSim's CUSTOM callback and the C++ policy API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import _policy

_TENANTS = {
    "chat": _policy.Tenant.CHAT,
    "coding_agent": _policy.Tenant.CODING_AGENT,
    "api_batch": _policy.Tenant.API_BATCH,
}


class PolicyRouter:
    """Stateful callable consumed by the LLMServingSim compatibility patch."""

    def __init__(
        self,
        policy_name: str,
        *,
        tenant_weights: Mapping[str, float] | None = None,
        tenant_slo_ns: Mapping[str, int] | None = None,
        service_rates: Mapping[str, Mapping[str, float]] | None = None,
    ) -> None:
        weights = dict(tenant_weights or {})
        config = _policy.PolicyConfig()
        config.chat_weight = float(weights.get("chat", 1.0))
        config.coding_agent_weight = float(weights.get("coding_agent", 1.0))
        config.api_batch_weight = float(weights.get("api_batch", 1.0))
        self._policy = _policy.make_policy(policy_name, config)
        self._tenant_slo_ns = {
            tenant: int(value) for tenant, value in (tenant_slo_ns or {}).items()
        }
        self._service_rates = {
            str(worker_id): dict(rates)
            for worker_id, rates in (service_rates or {}).items()
        }
        self._enqueued: set[str] = set()
        self.decisions: list[dict[str, Any]] = []

    @staticmethod
    def _request_id(request: Mapping[str, Any]) -> str:
        if "request_id" in request:
            return str(request["request_id"])
        return str(request["index"])

    def _request_view(self, request: Mapping[str, Any]) -> Any:
        tenant_name = str(request.get("tenant", "chat"))
        try:
            tenant = _TENANTS[tenant_name]
        except KeyError as error:
            raise ValueError(f"unknown simulator tenant: {tenant_name}") from error
        input_tokens = int(request["input_toks"])
        total_tokens = int(request["output_toks"])
        value = _policy.RequestView()
        value.request_id = self._request_id(request)
        value.session_id = str(request.get("session_id", value.request_id))
        value.tenant = tenant
        value.input_tokens = input_tokens
        value.output_tokens = max(0, total_tokens - input_tokens)
        value.prefix_blocks = [str(token) for token in request.get("input_hash_ids", ())]
        value.arrival_time_ns = int(request["arrival_time_ns"])
        value.slo_ns = int(
            request.get("slo_ns", self._tenant_slo_ns.get(tenant_name, 0))
        )
        return value

    def _worker_views(self, workers: Sequence[Mapping[str, Any]]) -> list[Any]:
        values = []
        for worker in workers:
            worker_id = str(worker["worker_id"])
            rates = self._service_rates.get(worker_id, {})
            value = _policy.WorkerView()
            value.worker_id = worker_id
            value.waiting_requests = int(worker.get("waiting_requests", 0))
            value.running_requests = int(worker.get("running_requests", 0))
            value.capacity = int(worker.get("capacity", 0))
            value.estimated_queued_token_work = float(
                worker.get("estimated_queued_token_work", 0.0)
            )
            value.prefill_tokens_per_second = float(
                rates.get(
                    "prefill_tokens_per_second",
                    worker.get("prefill_tokens_per_second", 0.0),
                )
            )
            value.decode_tokens_per_second = float(
                rates.get(
                    "decode_tokens_per_second",
                    worker.get("decode_tokens_per_second", 0.0),
                )
            )
            reuse = worker.get("reusable_prefix_tokens_by_request", {})
            value.reusable_prefix_tokens_by_request = {
                str(request_id): int(tokens) for request_id, tokens in reuse.items()
            }
            values.append(value)
        return values

    def __call__(
        self,
        requests: Sequence[Mapping[str, Any]],
        current_time_ns: int,
        workers: Sequence[Mapping[str, Any]],
        role: str = "prefill",
    ) -> list[dict[str, Any]]:
        """Enqueue new arrivals and return zero or more routing decisions."""
        del role
        ready = {self._request_id(request): request for request in requests}
        for request_id, request in ready.items():
            if request_id not in self._enqueued:
                self._policy.enqueue(self._request_view(request))
                self._enqueued.add(request_id)

        worker_views = self._worker_views(workers)
        worker_by_id = {worker.worker_id: worker for worker in worker_views}
        emitted: list[dict[str, Any]] = []
        for _ in range(len(ready)):
            decisions = self._policy.dispatch(int(current_time_ns), worker_views)
            if not decisions:
                break
            decision = decisions[0]
            record = {
                "admit": bool(decision.admit),
                "cache_hit_tokens": int(decision.cache_hit_tokens),
                "current_time_ns": int(current_time_ns),
                "predicted_ttft_seconds": float(decision.predicted_ttft_seconds),
                "reason_code": decision.reason_code,
                "request_id": decision.request_id,
                "session_id": str(
                    ready[decision.request_id].get(
                        "session_id", decision.request_id
                    )
                ),
                "tenant": str(ready[decision.request_id].get("tenant", "chat")),
                "worker_id": decision.worker_id,
            }
            emitted.append(record)
            self.decisions.append(record)
            if not decision.admit:
                break
            if decision.worker_id is None:
                raise RuntimeError("admitted dispatch decision has no worker")
            self._enqueued.discard(decision.request_id)
            worker_by_id[decision.worker_id].waiting_requests += 1
        return emitted

    def on_complete(self, request_id: str) -> None:
        self._policy.on_complete(str(request_id))

    def reset(self, seed: int) -> None:
        self._policy.reset(int(seed))
        self._enqueued.clear()
        self.decisions.clear()
