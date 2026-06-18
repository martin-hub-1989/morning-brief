"""测试 lib.values_match 验证逻辑。"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lib import values_match


class TestValuesMatch(unittest.TestCase):
    CONFIG = {
        "float_relative_tolerance": 0.0005,
        "float_absolute_tolerance": 0.005,
    }

    def test_exact_match(self):
        self.assertTrue(values_match(3.14, 3.14, self.CONFIG))

    def test_within_relative_tolerance(self):
        # 0.04% 差异 < 0.05% 容差
        self.assertTrue(values_match(10000, 10004, self.CONFIG))

    def test_exceeds_relative_tolerance(self):
        # 0.1% 差异 > 0.05% 容差
        self.assertFalse(values_match(10000, 10010, self.CONFIG))

    def test_within_absolute_tolerance(self):
        # 差异 0.004 < 绝对容差 0.005
        self.assertTrue(values_match(0.001, 0.005, self.CONFIG))

    def test_zero_db_value(self):
        # db_val=0 时只看绝对容差
        self.assertTrue(values_match(0, 0.003, self.CONFIG))
        self.assertFalse(values_match(0, 0.01, self.CONFIG))

    def test_non_numeric(self):
        self.assertTrue(values_match("abc", "abc", self.CONFIG))
        self.assertFalse(values_match("abc", "def", self.CONFIG))

    def test_category_override(self):
        """category 容差覆盖生效。"""
        config = {
            "float_relative_tolerance": 0.0005,
            "float_absolute_tolerance": 0.005,
            "category_overrides": {
                "fx_swap": {"float_absolute_tolerance": 50},
            },
        }
        # 差异 30pips，默认容差 0.005 会拒绝，但 fx_swap 容差 50 允许
        self.assertTrue(values_match(100, 130, config, category="fx_swap"))
        # 相同差异但不对应 category 时用默认容差
        self.assertFalse(values_match(100, 130, config, category="fx"))

    def test_category_no_override(self):
        """无对应 category 时使用默认容差。"""
        config = {
            "float_relative_tolerance": 0.0005,
            "float_absolute_tolerance": 0.005,
            "category_overrides": {
                "fx_swap": {"float_absolute_tolerance": 50},
            },
        }
        # fx_forward 不在 overrides 中，用默认容差
        self.assertTrue(values_match(7.25, 7.25001, config, category="fx_forward"))


if __name__ == "__main__":
    unittest.main()
