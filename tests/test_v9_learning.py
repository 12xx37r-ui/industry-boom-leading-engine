from __future__ import annotations

import unittest

from ible.v9_learning import _normalise


class V9LearningTests(unittest.TestCase):
    def test_weights_are_normalised(self):
        weights = _normalise({"revenue_growth": 2, "employment_growth": 1, "capex_growth": 1, "stock_return": 0, "industry_growth": 0})
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=5)
        self.assertGreater(weights["revenue_growth"], weights["stock_return"])


if __name__ == "__main__":
    unittest.main()
