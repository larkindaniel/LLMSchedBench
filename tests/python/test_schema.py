import unittest

from llmschedbench.schema import SCHEMA_VERSION, NormalizedCall


def valid_call(**overrides):
    values = {
        "schema_version": SCHEMA_VERSION,
        "source": "fixture",
        "source_record_id": "source-1",
        "request_id": "request-1",
        "tenant": "chat",
        "workload_class": "interactive",
        "session_id": "session-1",
        "step_index": 0,
        "dependency_mode": "open_loop",
        "session_arrival_ns": 1,
        "input_tokens": 32,
        "output_tokens": 8,
        "prefix_block_ids": ("a", "b"),
        "tool_delay_after_ns": 0,
    }
    values.update(overrides)
    return NormalizedCall(**values)


class NormalizedCallTests(unittest.TestCase):
    def test_valid_call_round_trips(self):
        call = valid_call()
        self.assertEqual(NormalizedCall.from_mapping(call.to_mapping()), call)

    def test_zero_length_output_is_valid(self):
        self.assertEqual(valid_call(output_tokens=0).output_tokens, 0)

    def test_negative_token_count_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "input_tokens"):
            valid_call(input_tokens=-1)

    def test_unknown_tenant_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid tenant"):
            valid_call(tenant="unknown")

    def test_repeated_source_blocks_preserve_order(self):
        call = valid_call(prefix_block_ids=("a", "a", "b"))
        self.assertEqual(call.prefix_block_ids, ("a", "a", "b"))


if __name__ == "__main__":
    unittest.main()
