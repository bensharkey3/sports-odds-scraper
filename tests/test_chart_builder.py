import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import chart_builder


class TestNameFromKey:
    def test_strips_prefix_and_suffix(self):
        assert chart_builder._name_from_key("parquet/world-cup-winner.parquet") == "world-cup-winner"

    def test_simple(self):
        assert chart_builder._name_from_key("parquet/odds.parquet") == "odds"


class TestLegendNcols:
    def test_few_series_single_column(self):
        assert chart_builder._legend_ncols(1) == 1
        assert chart_builder._legend_ncols(30) == 1

    def test_rolls_to_more_columns(self):
        assert chart_builder._legend_ncols(31) == 2
        assert chart_builder._legend_ncols(60) == 2
        assert chart_builder._legend_ncols(61) == 3

    def test_zero_is_at_least_one(self):
        assert chart_builder._legend_ncols(0) == 1
