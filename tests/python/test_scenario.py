import unittest
from pathlib import Path

from llmschedbench.scenario import compile_scenario

ROOT = Path(__file__).resolve().parents[2]


class ScenarioTests(unittest.TestCase):
    def test_smoke_scenario_compiles(self):
        compiled = compile_scenario(ROOT / "scenarios" / "smoke.yaml")
        self.assertEqual(compiled["scenario_schema_version"], "1.0.0")
        self.assertEqual(compiled["traffic"]["chat"], 0.60)


if __name__ == "__main__":
    unittest.main()
