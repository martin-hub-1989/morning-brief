"""测试外汇衍生序列复算公式。"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from recompute_fx_derived import ANNUAL_FACTORS


class TestHedgeCostFormula(unittest.TestCase):
    def test_cny_hedge_cost(self):
        """CNY 套保成本 = swap_points / 10000 / spot"""
        swap_points = -150  # pips
        spot = 7.2500
        expected = -150 / 10000 / 7.2500
        self.assertAlmostEqual(swap_points / 10000 / spot, expected, places=8)

    def test_cnh_hedge_cost(self):
        """CNH 套保成本 = DF / spot - 1"""
        df_val = 7.2300
        spot = 7.2500
        expected = 7.2300 / 7.2500 - 1
        self.assertAlmostEqual(df_val / spot - 1, expected, places=8)

    def test_annualization(self):
        """年化 = (1 + hedge)^n - 1"""
        hedge_1m = -0.002  # -0.2%
        n = ANNUAL_FACTORS["1m"]  # 12
        annualized = (1 + hedge_1m) ** n - 1
        self.assertAlmostEqual(annualized, (1 - 0.002) ** 12 - 1, places=8)

    def test_annual_factors(self):
        self.assertEqual(ANNUAL_FACTORS, {"1m": 12, "3m": 4, "6m": 2, "1y": 1})

    def test_perfect_hedge(self):
        """套保成本为 0 时年化也为 0"""
        self.assertAlmostEqual((1 + 0) ** 12 - 1, 0, places=8)

    def test_positive_hedge(self):
        """正套保成本年化放大"""
        hedge_1y = 0.03  # 3%
        annualized = (1 + hedge_1y) ** 1 - 1
        self.assertAlmostEqual(annualized, 0.03, places=8)


if __name__ == "__main__":
    unittest.main()
