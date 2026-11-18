"""eval/metrics.py (eval/ is put on sys.path by conftest.py)."""
import pytest

import metrics
from metrics import mean, moment_hit, mrr, percentile, rank_of, recall_at, summary


class TestRankOf:
    def test_one_based(self):
        assert rank_of("a.mp4", ["a.mp4", "b.mp4"]) == 1
        assert rank_of("b.mp4", ["a.mp4", "b.mp4"]) == 2

    def test_missing_is_none(self):
        assert rank_of("c.mp4", ["a.mp4", "b.mp4"]) is None

    def test_empty_list(self):
        assert rank_of("a.mp4", []) is None

    def test_duplicates_count_the_first(self):
        assert rank_of("b", ["a", "b", "b"]) == 2


class TestRecallAt:
    def test_empty_is_zero_not_a_division_error(self):
        assert recall_at([], 5) == 0.0

    def test_cutoff_is_inclusive(self):
        assert recall_at([1, 5, 6], 5) == pytest.approx(2 / 3)
        assert recall_at([1, 5, 6], 1) == pytest.approx(1 / 3)
        assert recall_at([1, 5, 6], 6) == 1.0

    def test_none_ranks_never_hit_but_count_in_the_denominator(self):
        assert recall_at([None, None, 1, 2], 10) == 0.5
        assert recall_at([None, None], 10) == 0.0

    def test_all_hit(self):
        assert recall_at([1, 1, 1], 1) == 1.0


class TestMrr:
    def test_empty(self):
        assert mrr([]) == 0.0

    def test_reciprocal_ranks(self):
        assert mrr([1, 2, 4]) == pytest.approx((1 + 0.5 + 0.25) / 3)

    def test_none_contributes_zero_but_counts(self):
        assert mrr([1, None]) == 0.5
        assert mrr([None, None, None]) == 0.0

    def test_perfect(self):
        assert mrr([1, 1]) == 1.0


class TestMomentHit:
    WINDOW = (10.0, 12.0)

    def test_inside_the_window(self):
        assert moment_hit("g", self.WINDOW, "g", 11_000)
        assert moment_hit("g", self.WINDOW, "g", 10_000)
        assert moment_hit("g", self.WINDOW, "g", 12_000)

    def test_exactly_one_second_outside_is_still_a_hit(self):
        assert moment_hit("g", self.WINDOW, "g", 9_000)    # start - 1 s
        assert moment_hit("g", self.WINDOW, "g", 13_000)   # end + 1 s

    def test_beyond_the_tolerance_is_a_miss(self):
        assert not moment_hit("g", self.WINDOW, "g", 8_999)
        assert not moment_hit("g", self.WINDOW, "g", 13_001)

    def test_custom_tolerance(self):
        assert moment_hit("g", self.WINDOW, "g", 7_000, tolerance_s=3.0)
        assert not moment_hit("g", self.WINDOW, "g", 6_999, tolerance_s=3.0)
        assert not moment_hit("g", self.WINDOW, "g", 9_000, tolerance_s=0.0)
        assert moment_hit("g", self.WINDOW, "g", 10_000, tolerance_s=0.0)

    def test_default_tolerance_is_one_second(self):
        assert metrics.MOMENT_TOLERANCE_S == 1.0

    def test_wrong_top_file_is_a_miss_even_with_the_right_time(self):
        assert not moment_hit("g", self.WINDOW, "other", 11_000)

    def test_no_top_file(self):
        assert not moment_hit("g", self.WINDOW, None, 11_000)

    def test_none_jump(self):
        assert not moment_hit("g", self.WINDOW, "g", None)

    def test_none_window(self):
        assert not moment_hit("g", None, "g", 11_000)

    def test_jump_at_zero_is_a_real_time_not_missing(self):
        assert moment_hit("g", (0.0, 2.0), "g", 0)
        assert moment_hit("g", (0.0, 2.0), "g", 0, tolerance_s=0)

    def test_window_given_as_a_list(self):
        assert moment_hit("g", [10, 12], "g", 11_500)

    def test_zero_length_window(self):
        assert moment_hit("g", (5.0, 5.0), "g", 6_000)
        assert not moment_hit("g", (5.0, 5.0), "g", 6_001)


class TestPercentile:
    def test_empty_is_none(self):
        assert percentile([], 50) is None

    def test_single_value(self):
        assert percentile([7.0], 0) == 7.0
        assert percentile([7.0], 50) == 7.0
        assert percentile([7.0], 100) == 7.0

    def test_endpoints(self):
        data = [4, 1, 3, 2]
        assert percentile(data, 0) == 1
        assert percentile(data, 100) == 4

    def test_median_even_and_odd(self):
        assert percentile([1, 2, 3, 4], 50) == 2.5
        assert percentile([1, 2, 3, 4, 5], 50) == 3

    # numpy.percentile([1, 2, 3, 4], q), linear interpolation: pos = (n - 1) * q / 100
    @pytest.mark.parametrize("q, expected", [(25, 1.75), (50, 2.5), (75, 3.25), (90, 3.7), (95, 3.85), (99, 3.97)])
    def test_numpy_linear_interpolation(self, q, expected):
        assert percentile([1, 2, 3, 4], q) == pytest.approx(expected)

    def test_hand_computed_with_uneven_spacing(self):
        # sorted: 10 20 40 100; pos(p90) = 3 * 0.9 = 2.7 -> 40 + 0.7 * 60 = 82
        assert percentile([100, 10, 40, 20], 90) == pytest.approx(82.0)
        # pos(p10) = 0.3 -> 10 + 0.3 * 10 = 13
        assert percentile([100, 10, 40, 20], 10) == pytest.approx(13.0)

    def test_unsorted_input_and_input_not_mutated(self):
        data = [5.0, 1.0, 3.0]
        assert percentile(data, 50) == 3.0
        assert data == [5.0, 1.0, 3.0]

    def test_two_values(self):
        assert percentile([10, 20], 50) == 15
        assert percentile([10, 20], 95) == pytest.approx(19.5)

    def test_duplicates(self):
        assert percentile([2, 2, 2, 2], 73) == 2


class TestSummary:
    def test_empty(self):
        assert summary([], []) == {"n": 0, "R@1": 0.0, "R@5": 0.0, "R@10": 0.0, "MRR": 0.0}

    def test_full(self):
        answerable = [
            {"rank": 1, "moment": True},
            {"rank": 3, "moment": False},
            {"rank": 7, "moment": None},    # no time window (e.g. FLEURS): not in the moment rate
            {"rank": None, "moment": None},
            {"rank": 12},                    # moment key absent
        ]
        no_answer = [{"returned": 0}, {"returned": 4}, {"returned": 0}, {"returned": 1}]
        got = summary(answerable, no_answer)
        assert got == {
            "n": 5,
            "R@1": pytest.approx(1 / 5), "R@5": pytest.approx(2 / 5), "R@10": pytest.approx(3 / 5),
            "MRR": pytest.approx((1 + 1 / 3 + 1 / 7 + 1 / 12) / 5),
            "moment@1": 0.5, "n_moment": 2,
            "noans_FP": 0.5, "n_noans": 4,
        }

    def test_moment_keys_absent_without_moment_rows(self):
        got = summary([{"rank": 1, "moment": None}, {"rank": 2}], [])
        assert "moment@1" not in got and "n_moment" not in got
        assert "noans_FP" not in got and "n_noans" not in got

    def test_moment_false_everywhere_still_reported(self):
        got = summary([{"rank": 1, "moment": False}], [])
        assert got["moment@1"] == 0.0 and got["n_moment"] == 1

    def test_only_no_answer_rows(self):
        got = summary([], [{"returned": 2}])
        assert got["n"] == 0 and got["noans_FP"] == 1.0 and got["n_noans"] == 1


class TestMean:
    def test_empty_is_none(self):
        assert mean([]) is None

    def test_values(self):
        assert mean([1, 2, 6]) == 3
