#include "llmschedbench/policy.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace llmschedbench {
namespace {

std::size_t tenant_index(Tenant tenant) {
  switch (tenant) {
    case Tenant::Chat:
      return 0;
    case Tenant::CodingAgent:
      return 1;
    case Tenant::ApiBatch:
      return 2;
  }
  throw std::invalid_argument("unknown tenant");
}

double tenant_weight(const PolicyConfig& config, Tenant tenant) {
  switch (tenant) {
    case Tenant::Chat:
      return config.chat_weight;
    case Tenant::CodingAgent:
      return config.coding_agent_weight;
    case Tenant::ApiBatch:
      return config.api_batch_weight;
  }
  throw std::invalid_argument("unknown tenant");
}

void validate_config(const PolicyConfig& config) {
  for (const auto weight : {config.chat_weight, config.coding_agent_weight,
                            config.api_batch_weight}) {
    if (!std::isfinite(weight) || weight <= 0.0) {
      throw std::invalid_argument("tenant weights must be finite and positive");
    }
  }
}

std::size_t select_least_loaded(const std::vector<WorkerView>& workers,
                                std::size_t& tie_break_counter) {
  std::size_t best_index = tie_break_counter % workers.size();
  double best_score = least_loaded_score(workers[best_index]);
  for (std::size_t offset = 1; offset < workers.size(); ++offset) {
    const auto index = (tie_break_counter + offset) % workers.size();
    const auto score = least_loaded_score(workers[index]);
    if (score < best_score) {
      best_index = index;
      best_score = score;
    }
  }
  tie_break_counter = (best_index + 1) % workers.size();
  return best_index;
}

std::uint64_t reusable_prefix_tokens(const WorkerView& worker,
                                     const RequestView& request) {
  const auto match =
      worker.reusable_prefix_tokens_by_request.find(request.request_id);
  return match == worker.reusable_prefix_tokens_by_request.end()
             ? worker.reusable_prefix_tokens
             : match->second;
}

double predicted_ttft_seconds(const RequestView& request,
                              const WorkerView& worker) {
  double queued_seconds = 0.0;
  if (worker.estimated_queued_token_work > 0.0) {
    if (worker.decode_tokens_per_second <= 0.0) {
      return std::numeric_limits<double>::infinity();
    }
    queued_seconds = worker.estimated_queued_token_work /
                     worker.decode_tokens_per_second;
  }

  const auto reusable =
      std::min(reusable_prefix_tokens(worker, request), request.input_tokens);
  const auto uncached = request.input_tokens - reusable;
  double prefill_seconds = 0.0;
  if (uncached > 0) {
    if (worker.prefill_tokens_per_second <= 0.0) {
      return std::numeric_limits<double>::infinity();
    }
    prefill_seconds =
        static_cast<double>(uncached) / worker.prefill_tokens_per_second;
  }
  return queued_seconds + prefill_seconds;
}

class CacheMaxPolicy final : public Policy {
 public:
  void enqueue(const RequestView& request) override { queue_.push_back(request); }

  std::vector<DispatchDecision> dispatch(
      std::int64_t /*current_time_ns*/,
      const std::vector<WorkerView>& workers) override {
    if (queue_.empty()) {
      return {};
    }
    if (workers.empty()) {
      return {{false, std::nullopt, 0.0, 0, "no_worker_available",
               queue_.front().request_id}};
    }

    std::size_t best_index = tie_break_counter_ % workers.size();
    for (std::size_t offset = 1; offset < workers.size(); ++offset) {
      const auto index = (tie_break_counter_ + offset) % workers.size();
      const auto& candidate = workers[index];
      const auto& best = workers[best_index];
      const auto candidate_reuse =
          reusable_prefix_tokens(candidate, queue_.front());
      const auto best_reuse = reusable_prefix_tokens(best, queue_.front());
      if (candidate_reuse > best_reuse ||
          (candidate_reuse == best_reuse &&
           least_loaded_score(candidate) < least_loaded_score(best))) {
        best_index = index;
      }
    }
    tie_break_counter_ = (best_index + 1) % workers.size();
    const auto& worker = workers[best_index];
    const auto request = queue_.front();
    const auto cache_hit =
        std::min(reusable_prefix_tokens(worker, request), request.input_tokens);
    queue_.erase(queue_.begin());
    return {{true, worker.worker_id, 0.0, cache_hit, "cache_max",
             request.request_id}};
  }

  void on_complete(std::string_view /*request_id*/) override {}

  void reset(std::uint64_t /*seed*/) override {
    queue_.clear();
    tie_break_counter_ = 0;
  }

 private:
  std::vector<RequestView> queue_;
  std::size_t tie_break_counter_{0};
};

struct TaggedRequest {
  RequestView request;
  double finish_tag{0.0};
  std::uint64_t sequence{0};
};

class WeightedPolicy final : public Policy {
 public:
  WeightedPolicy(PolicyConfig config, bool slo_guarded)
      : config_(std::move(config)), slo_guarded_(slo_guarded) {
    validate_config(config_);
  }

  void enqueue(const RequestView& request) override {
    const auto index = tenant_index(request.tenant);
    const auto work = std::max<std::uint64_t>(
        1, request.input_tokens + request.output_tokens);
    const auto start = std::max(last_finish_[index], virtual_time_);
    const auto finish =
        start + static_cast<double>(work) / tenant_weight(config_, request.tenant);
    last_finish_[index] = finish;
    queue_.push_back({request, finish, next_sequence_++});
  }

  std::vector<DispatchDecision> dispatch(
      std::int64_t /*current_time_ns*/,
      const std::vector<WorkerView>& workers) override {
    if (queue_.empty()) {
      return {};
    }
    if (workers.empty()) {
      return {{false, std::nullopt, 0.0, 0, "no_worker_available", ""}};
    }

    auto request_it = std::min_element(
        queue_.begin(), queue_.end(),
        [](const TaggedRequest& left, const TaggedRequest& right) {
          if (left.finish_tag != right.finish_tag) {
            return left.finish_tag < right.finish_tag;
          }
          return left.sequence < right.sequence;
        });
    const auto request = request_it->request;
    const auto finish_tag = request_it->finish_tag;

    std::size_t worker_index = 0;
    double predicted = 0.0;
    std::string reason = "weighted_fair";
    if (slo_guarded_) {
      worker_index = select_slo_worker(request, workers, predicted, reason);
    } else {
      worker_index = select_least_loaded(workers, tie_break_counter_);
    }
    const auto& worker = workers[worker_index];
    const auto cache_hit =
        std::min(reusable_prefix_tokens(worker, request), request.input_tokens);
    virtual_time_ = std::max(virtual_time_, finish_tag);
    queue_.erase(request_it);
    return {{true, worker.worker_id, predicted, cache_hit, std::move(reason),
             request.request_id}};
  }

  void on_complete(std::string_view /*request_id*/) override {}

  void reset(std::uint64_t /*seed*/) override {
    queue_.clear();
    last_finish_.fill(0.0);
    virtual_time_ = 0.0;
    next_sequence_ = 0;
    tie_break_counter_ = 0;
  }

 private:
  std::size_t select_slo_worker(const RequestView& request,
                                const std::vector<WorkerView>& workers,
                                double& predicted,
                                std::string& reason) {
    std::size_t warmest = tie_break_counter_ % workers.size();
    for (std::size_t offset = 1; offset < workers.size(); ++offset) {
      const auto index = (tie_break_counter_ + offset) % workers.size();
      const auto& candidate = workers[index];
      const auto& best = workers[warmest];
      const auto candidate_reuse = reusable_prefix_tokens(candidate, request);
      const auto best_reuse = reusable_prefix_tokens(best, request);
      if (candidate_reuse > best_reuse ||
          (candidate_reuse == best_reuse &&
           least_loaded_score(candidate) < least_loaded_score(best))) {
        warmest = index;
      }
    }

    const auto warm_prediction = predicted_ttft_seconds(request, workers[warmest]);
    const auto slo_seconds = static_cast<double>(request.slo_ns) / 1'000'000'000.0;
    if (request.slo_ns <= 0 || warm_prediction <= slo_seconds) {
      tie_break_counter_ = (warmest + 1) % workers.size();
      predicted = warm_prediction;
      reason = "affinity_slo_safe";
      return warmest;
    }

    std::size_t fastest = tie_break_counter_ % workers.size();
    double fastest_prediction = predicted_ttft_seconds(request, workers[fastest]);
    for (std::size_t offset = 1; offset < workers.size(); ++offset) {
      const auto index = (tie_break_counter_ + offset) % workers.size();
      const auto candidate_prediction =
          predicted_ttft_seconds(request, workers[index]);
      if (candidate_prediction < fastest_prediction ||
          (candidate_prediction == fastest_prediction &&
           least_loaded_score(workers[index]) <
               least_loaded_score(workers[fastest]))) {
        fastest = index;
        fastest_prediction = candidate_prediction;
      }
    }
    tie_break_counter_ = (fastest + 1) % workers.size();
    predicted = fastest_prediction;
    reason = "slo_fallback_min_ttft";
    return fastest;
  }

  PolicyConfig config_;
  bool slo_guarded_{false};
  std::vector<TaggedRequest> queue_;
  std::array<double, 3> last_finish_{};
  double virtual_time_{0.0};
  std::uint64_t next_sequence_{0};
  std::size_t tie_break_counter_{0};
};

}  // namespace

double least_loaded_score(const WorkerView& worker) {
  const auto raw_score = static_cast<double>(worker.waiting_requests) * 4.0 +
                         static_cast<double>(worker.running_requests);
  return worker.capacity == 0 ? raw_score
                              : raw_score / static_cast<double>(worker.capacity);
}

void LeastLoadedPolicy::enqueue(const RequestView& request) {
  queue_.push_back(request);
}

std::vector<DispatchDecision> LeastLoadedPolicy::dispatch(
    std::int64_t /*current_time_ns*/,
    const std::vector<WorkerView>& workers) {
  if (queue_.empty()) {
    return {};
  }
  if (workers.empty()) {
    return {{false, std::nullopt, 0.0, 0, "no_worker_available",
             queue_.front().request_id}};
  }

  const auto best_index = select_least_loaded(workers, tie_break_counter_);
  const auto& worker = workers[best_index];
  const auto request = queue_.front();
  const auto cache_hit =
      std::min(reusable_prefix_tokens(worker, request), request.input_tokens);
  queue_.erase(queue_.begin());
  return {{true, worker.worker_id, 0.0, cache_hit, "least_loaded",
           request.request_id}};
}

void LeastLoadedPolicy::on_complete(std::string_view /*request_id*/) {}

void LeastLoadedPolicy::reset(std::uint64_t /*seed*/) {
  queue_.clear();
  tie_break_counter_ = 0;
}

std::unique_ptr<Policy> make_policy(std::string_view name,
                                    const PolicyConfig& config) {
  if (name == "least_loaded") {
    return std::make_unique<LeastLoadedPolicy>();
  }
  if (name == "cache_max") {
    return std::make_unique<CacheMaxPolicy>();
  }
  if (name == "weighted_fair") {
    return std::make_unique<WeightedPolicy>(config, false);
  }
  if (name == "slo_guarded_affinity") {
    return std::make_unique<WeightedPolicy>(config, true);
  }
  throw std::invalid_argument("unknown policy: " + std::string(name));
}

}  // namespace llmschedbench
