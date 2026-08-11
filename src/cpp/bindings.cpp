#include "llmschedbench/policy.hpp"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;
using namespace llmschedbench;

PYBIND11_MODULE(_policy, module) {
  module.doc() = "LLMSchedBench C++ scheduling policy engine";

  py::enum_<Tenant>(module, "Tenant")
      .value("CHAT", Tenant::Chat)
      .value("CODING_AGENT", Tenant::CodingAgent)
      .value("API_BATCH", Tenant::ApiBatch);

  py::class_<RequestView>(module, "RequestView")
      .def(py::init<>())
      .def_readwrite("request_id", &RequestView::request_id)
      .def_readwrite("session_id", &RequestView::session_id)
      .def_readwrite("tenant", &RequestView::tenant)
      .def_readwrite("input_tokens", &RequestView::input_tokens)
      .def_readwrite("output_tokens", &RequestView::output_tokens)
      .def_readwrite("prefix_blocks", &RequestView::prefix_blocks)
      .def_readwrite("arrival_time_ns", &RequestView::arrival_time_ns)
      .def_readwrite("slo_ns", &RequestView::slo_ns);

  py::class_<WorkerView>(module, "WorkerView")
      .def(py::init<>())
      .def_readwrite("worker_id", &WorkerView::worker_id)
      .def_readwrite("waiting_requests", &WorkerView::waiting_requests)
      .def_readwrite("running_requests", &WorkerView::running_requests)
      .def_readwrite("capacity", &WorkerView::capacity)
      .def_readwrite("estimated_queued_token_work",
                     &WorkerView::estimated_queued_token_work)
      .def_readwrite("prefill_tokens_per_second",
                     &WorkerView::prefill_tokens_per_second)
      .def_readwrite("decode_tokens_per_second",
                     &WorkerView::decode_tokens_per_second)
      .def_readwrite("reusable_prefix_tokens",
                     &WorkerView::reusable_prefix_tokens)
      .def_readwrite("reusable_prefix_tokens_by_request",
                     &WorkerView::reusable_prefix_tokens_by_request);

  py::class_<DispatchDecision>(module, "DispatchDecision")
      .def_readonly("admit", &DispatchDecision::admit)
      .def_readonly("worker_id", &DispatchDecision::worker_id)
      .def_readonly("predicted_ttft_seconds",
                    &DispatchDecision::predicted_ttft_seconds)
      .def_readonly("cache_hit_tokens", &DispatchDecision::cache_hit_tokens)
      .def_readonly("reason_code", &DispatchDecision::reason_code)
      .def_readonly("request_id", &DispatchDecision::request_id);

  py::class_<PolicyConfig>(module, "PolicyConfig")
      .def(py::init<>())
      .def_readwrite("chat_weight", &PolicyConfig::chat_weight)
      .def_readwrite("coding_agent_weight", &PolicyConfig::coding_agent_weight)
      .def_readwrite("api_batch_weight", &PolicyConfig::api_batch_weight);

  py::class_<Policy, std::unique_ptr<Policy>>(module, "Policy")
      .def("enqueue", &Policy::enqueue)
      .def("dispatch", &Policy::dispatch)
      .def("on_complete", &Policy::on_complete)
      .def("reset", &Policy::reset);

  module.def(
      "make_policy",
      [](const std::string& name, const PolicyConfig& config) {
        return make_policy(name, config);
      },
      py::arg("name"), py::arg("config") = PolicyConfig{});
  module.def("least_loaded_score", &least_loaded_score);
}
