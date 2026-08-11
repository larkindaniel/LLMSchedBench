import unittest

from hypothesis import assume, given
from hypothesis import strategies as st
from llmschedbench.prefixes import qwen_block_tokens, tracelab_block_id


class PrefixTests(unittest.TestCase):
    def test_qwen_block_expands_to_sixteen_stable_tokens(self):
        first = qwen_block_tokens("hash-a")
        self.assertEqual(len(first), 16)
        self.assertEqual(first, qwen_block_tokens("hash-a"))

    def test_unrelated_qwen_blocks_do_not_overlap(self):
        self.assertTrue(
            set(qwen_block_tokens("hash-a")).isdisjoint(qwen_block_tokens("hash-b"))
        )

    def test_tracelab_blocks_are_session_scoped(self):
        self.assertNotEqual(tracelab_block_id("session-a", 0), tracelab_block_id("session-b", 0))
        self.assertEqual(tracelab_block_id("session-a", 0), tracelab_block_id("session-a", 0))

    @given(st.text(min_size=1), st.text(min_size=1))
    def test_qwen_block_identity_property(self, left, right):
        assume(left != right)
        self.assertEqual(qwen_block_tokens(left), qwen_block_tokens(left))
        self.assertNotEqual(qwen_block_tokens(left), qwen_block_tokens(right))

    @given(st.text(min_size=1), st.text(min_size=1), st.integers(min_value=0))
    def test_unrelated_tracelab_sessions_do_not_collide(self, left, right, index):
        assume(left != right)
        self.assertNotEqual(
            tracelab_block_id(left, index),
            tracelab_block_id(right, index),
        )


if __name__ == "__main__":
    unittest.main()
