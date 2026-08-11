#include "llmschedbench/policy.hpp"

#include <cassert>
#include <cmath>
#include <optional>
#include <string>
#include <vector>

namespace {

using llmschedbench::PolicyConfig;
using llmschedbench::RequestView;
using llmschedbench::Tenant;
using llmschedbench::WorkerView;

RequestView request(std::string id, Tenant tenant, std::uint64_t input = 10,
                    std::uint64_t output = 10) {
  RequestView value;
  value.request_id = std::move(id);
  value.session_id = "session";
  value.tenant = tenant;
  value.input_tokens = input;
  value.output_tokens = output;
  return value;
}

WorkerView worker(std::string id) {
  WorkerView value;
  value.worker_id = std::move(id);
  value.capacity = 1;
  return value;
}

void test_least_loaded_score_and_ties() {
  auto value = worker("worker");
  value.waiting_requests = 2;
  value.running_requests = 3;
  assert(llmschedbench::least_loaded_score(value) == 11.0);

  value.capacity = 11;
  assert(llmschedbench::least_loaded_score(value) == 1.0);

  auto policy = llmschedbench::make_policy("least_loaded");
  policy->enqueue(request("r1", Tenant::Chat));
  policy->enqueue(request("r2", Tenant::Chat));
  std::vector<WorkerView> tied_workers{worker("worker-0"), worker("worker-1")};
  const auto first = policy->dispatch(0, tied_workers);
  const auto second = policy->dispatch(0, tied_workers);
  assert(first[0].worker_id == std::optional<std::string>("worker-0"));
  assert(second[0].worker_id == std::optional<std::string>("worker-1"));
}

void test_cache_max_prefers_reuse_then_load() {
  auto policy = llmschedbench::make_policy("cache_max");
  policy->enqueue(request("r1", Tenant::Chat, 100, 10));
  auto cold = worker("cold");
  auto warm = worker("warm");
  warm.waiting_requests = 4;
  warm.reusable_prefix_tokens = 80;
  const auto decision = policy->dispatch(0, {cold, warm})[0];
  assert(decision.worker_id == std::optional<std::string>("warm"));
  assert(decision.cache_hit_tokens == 80);

  policy->enqueue(request("r2", Tenant::Chat, 100, 10));
  cold.reusable_prefix_tokens = 80;
  cold.waiting_requests = 0;
  const auto tie = policy->dispatch(0, {warm, cold})[0];
  assert(tie.worker_id == std::optional<std::string>("cold"));
}

void test_weighted_fair_order_and_starvation_bound() {
  PolicyConfig config;
  config.chat_weight = 1.0;
  config.api_batch_weight = 0.25;
  auto policy = llmschedbench::make_policy("weighted_fair", config);
  for (int index = 0; index < 4; ++index) {
    policy->enqueue(request("chat-" + std::to_string(index), Tenant::Chat, 5, 5));
  }
  policy->enqueue(request("batch", Tenant::ApiBatch, 5, 5));
  const std::vector<WorkerView> workers{worker("worker")};
  for (int index = 0; index < 4; ++index) {
    const auto decision = policy->dispatch(0, workers)[0];
    assert(decision.request_id == "chat-" + std::to_string(index));
  }
  // The lower-weight batch request has finish tag 40 and is selected no later
  // than the fourth equal-work chat request with the same tag.
  const auto final = policy->dispatch(0, workers)[0];
  assert(final.request_id == "batch");
}

void test_slo_guarded_affinity_and_fallback() {
  auto warm = worker("warm");
  warm.reusable_prefix_tokens = 100;
  warm.estimated_queued_token_work = 100;
  warm.decode_tokens_per_second = 100;
  warm.prefill_tokens_per_second = 1000;

  auto cold = worker("cold");
  cold.decode_tokens_per_second = 100;
  cold.prefill_tokens_per_second = 1000;

  auto safe_policy = llmschedbench::make_policy("slo_guarded_affinity");
  auto safe_request = request("safe", Tenant::CodingAgent, 100, 10);
  safe_request.slo_ns = 2'000'000'000;
  safe_policy->enqueue(safe_request);
  const auto safe = safe_policy->dispatch(0, {warm, cold})[0];
  assert(safe.worker_id == std::optional<std::string>("warm"));
  assert(safe.reason_code == "affinity_slo_safe");
  assert(std::abs(safe.predicted_ttft_seconds - 1.0) < 1e-12);

  auto fallback_policy = llmschedbench::make_policy("slo_guarded_affinity");
  auto urgent_request = request("urgent", Tenant::CodingAgent, 100, 10);
  urgent_request.slo_ns = 500'000'000;
  fallback_policy->enqueue(urgent_request);
  const auto fallback = fallback_policy->dispatch(0, {warm, cold})[0];
  assert(fallback.worker_id == std::optional<std::string>("cold"));
  assert(fallback.reason_code == "slo_fallback_min_ttft");
  assert(std::abs(fallback.predicted_ttft_seconds - 0.1) < 1e-12);
}

void test_no_worker_defers_without_dropping_request() {
  auto policy = llmschedbench::make_policy("cache_max");
  policy->enqueue(request("r1", Tenant::Chat));
  const auto deferred = policy->dispatch(0, {})[0];
  assert(!deferred.admit);
  assert(!deferred.worker_id.has_value());
  const auto admitted = policy->dispatch(0, {worker("worker")})[0];
  assert(admitted.admit);
}

}  // namespace

int main() {
  test_least_loaded_score_and_ties();
  test_cache_max_prefers_reuse_then_load();
  test_weighted_fair_order_and_starvation_bound();
  test_slo_guarded_affinity_and_fallback();
  test_no_worker_defers_without_dropping_request();
  return 0;
}
