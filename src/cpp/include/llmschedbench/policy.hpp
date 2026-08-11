#pragma once

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace llmschedbench {

enum class Tenant { Chat, CodingAgent, ApiBatch };

struct RequestView {
  std::string request_id;
  std::string session_id;
  Tenant tenant{Tenant::Chat};
  std::uint64_t input_tokens{0};
  std::uint64_t output_tokens{0};
  std::vector<std::string> prefix_blocks;
  std::int64_t arrival_time_ns{0};
  std::int64_t slo_ns{0};
};

struct WorkerView {
  std::string worker_id;
  std::uint64_t waiting_requests{0};
  std::uint64_t running_requests{0};
  std::uint64_t capacity{1};
  double estimated_queued_token_work{0.0};
  double prefill_tokens_per_second{0.0};
  double decode_tokens_per_second{0.0};
  std::uint64_t reusable_prefix_tokens{0};
  std::unordered_map<std::string, std::uint64_t>
      reusable_prefix_tokens_by_request;
};

struct DispatchDecision {
  bool admit{true};
  std::optional<std::string> worker_id;
  double predicted_ttft_seconds{0.0};
  std::uint64_t cache_hit_tokens{0};
  std::string reason_code;
  std::string request_id;
};

struct PolicyConfig {
  double chat_weight{1.0};
  double coding_agent_weight{1.0};
  double api_batch_weight{1.0};
};

class Policy {
 public:
  virtual ~Policy() = default;
  virtual void enqueue(const RequestView& request) = 0;
  virtual std::vector<DispatchDecision> dispatch(
      std::int64_t current_time_ns,
      const std::vector<WorkerView>& workers) = 0;
  virtual void on_complete(std::string_view request_id) = 0;
  virtual void reset(std::uint64_t seed) = 0;
};

class LeastLoadedPolicy final : public Policy {
 public:
  void enqueue(const RequestView& request) override;
  std::vector<DispatchDecision> dispatch(
      std::int64_t current_time_ns,
      const std::vector<WorkerView>& workers) override;
  void on_complete(std::string_view request_id) override;
  void reset(std::uint64_t seed) override;

 private:
  std::vector<RequestView> queue_;
  std::size_t tie_break_counter_{0};
};

double least_loaded_score(const WorkerView& worker);
std::unique_ptr<Policy> make_policy(
    std::string_view name, const PolicyConfig& config = {});

}  // namespace llmschedbench
