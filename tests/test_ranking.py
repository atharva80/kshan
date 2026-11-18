import math

import pytest

from vsearch import ranking
from vsearch.ranking import LEGACY, Hit, Params, fuse, probability, with_


def vis(asset, sim=0.12, ms=0):
    return Hit(asset, ms, "visual", "vec", sim)


def said(asset, ms=0, method="exact", sim=1.0, text="hello", translated=False):
    return Hit(asset, ms, "speech", method, sim, text, translated)


def ocr(asset, ms=0, method="exact", sim=1.0, text="HELLO"):
    return Hit(asset, ms, "text", method, sim, text)


def by_asset(results):
    return {v["assetId"]: v for v in results}


# --- fusion -----------------------------------------------------------------------------------
class TestFusion:
    def test_legacy_is_best_plus_a_tenth_of_the_rest(self):
        got = fuse({"visual": 0.5, "speech": 0.9, "text": 0.2}, {}, LEGACY)
        assert got == pytest.approx(0.9 + 0.1 * (0.5 + 0.2))

    def test_legacy_single_kind_is_its_score(self):
        assert fuse({"text": 0.8}, {}, LEGACY) == 0.8

    def test_legacy_end_to_end(self):
        # visual: a is 0.025 into the 0.05 wide band under the best frame (b) -> 0.4 + 0.6 * 0.5 = 0.7
        hits = [vis("a", 0.095), vis("b", 0.12), said("a")]
        got = by_asset(ranking.rank(hits, "hello", LEGACY))
        assert got["a"]["score"] == round(1.0 + 0.1 * 0.7, 3)  # exact speech 1.0 + a tenth of the visual
        assert got["a"]["matched"] == ["speech", "visual"]
        assert got["b"]["score"] == 1.0

    def test_rrf_two_kinds_beat_one_at_equal_ranks(self):
        p = with_(LEGACY, fusion="rrf")
        two = fuse({"visual": 1.0, "speech": 1.0}, {"visual": 1, "speech": 1}, p)
        one = fuse({"visual": 1.0}, {"visual": 1}, p)
        assert two == pytest.approx(2 / 61)
        assert two > one == pytest.approx(1 / 61)

    def test_rrf_rank_uses_k(self):
        p = with_(LEGACY, fusion="rrf", rrf_k=10)
        assert fuse({"visual": 0.1}, {"visual": 5}, p) == pytest.approx(1 / 15)

    def test_rrf_end_to_end(self):
        p = with_(LEGACY, fusion="rrf")
        # both videos rank 1 / 2 in visual; only "b" also matches speech (rank 1) -> b wins
        hits = [vis("a", 0.13), vis("b", 0.12), said("b")]
        results = ranking.rank(hits, "hello", p)
        assert [v["assetId"] for v in results] == ["b", "a"]
        assert results[0]["score"] == round(1 / 62 + 1 / 61, 6)
        assert results[1]["score"] == round(1 / 61, 6)

    def test_rrf_equal_ranks_end_to_end(self):
        p = with_(LEGACY, fusion="rrf")
        # "a" is visual rank 1 and speech rank 1; "b" is visual rank 2 only
        results = ranking.rank([vis("a", 0.13), vis("b", 0.12), said("a")], "hello", p)
        assert [v["assetId"] for v in results] == ["a", "b"]

    CAL = {"visual/vec": (50.0, -3.0), "speech/exact": (2.0, -0.5), "text/exact": (0.0, 1.0)}

    def test_calibrated_noisy_or(self):
        p1, p2 = 0.3, 0.6
        assert fuse({"visual": p1, "speech": p2}, {}, with_(LEGACY, fusion="calibrated")) == \
            pytest.approx(1 - (1 - p1) * (1 - p2))

    def test_calibrated_noisy_or_end_to_end(self):
        p = with_(LEGACY, fusion="calibrated", calibration=self.CAL)
        (video,) = ranking.rank([vis("a", 0.12), said("a")], "hello", p)
        p1 = 1 / (1 + math.exp(-(50.0 * 0.12 - 3.0)))
        p2 = 1 / (1 + math.exp(-(2.0 * 1.0 - 0.5)))
        assert video["score"] == round(1 - (1 - p1) * (1 - p2), 6)

    def test_calibrated_uses_probability_not_the_threshold_score(self):
        p = with_(LEGACY, fusion="calibrated", calibration=self.CAL)
        (video,) = ranking.rank([said("a")], "hello", p)
        assert video["best"]["score"] == round(probability(said("a"), p), 3)

    def test_calibrated_takes_best_probability_per_kind(self):
        p = with_(LEGACY, fusion="calibrated", calibration={"speech/exact": (0.0, 0.0), "speech/vec": (10.0, -6.0)})
        hits = [said("a", ms=0), said("a", ms=9000, method="vec", sim=0.9, text="y")]
        (video,) = ranking.rank(hits, "x y z", p)
        # exact: sigmoid(0) = 0.5; vec: sigmoid(3) ~ 0.953 -> speech kind keeps the larger
        assert video["score"] == round(probability(hits[1], p), 6)

    def test_calibrated_min_prob_drops_unlikely_videos(self):
        cal = {"speech/exact": (0.0, 3.0), "text/exact": (0.0, -3.0)}  # p = 0.953 and 0.047
        hits = [said("likely"), ocr("unlikely")]
        keep_all = ranking.rank(hits, "hello", with_(LEGACY, fusion="calibrated", calibration=cal))
        assert {v["assetId"] for v in keep_all} == {"likely", "unlikely"}
        cut = ranking.rank(hits, "hello", with_(LEGACY, fusion="calibrated", calibration=cal, min_prob=0.5))
        assert [v["assetId"] for v in cut] == ["likely"]

    def test_min_prob_only_applies_to_calibrated(self):
        hits = [said("a"), ocr("b")]
        assert len(ranking.rank(hits, "hello", with_(LEGACY, min_prob=0.99))) == 2

    def test_unknown_calibration_key_is_a_coin_flip(self):
        assert probability(said("a"), Params()) == 0.5

    def test_probability_extremes_do_not_overflow(self):
        p = Params(calibration={"speech/exact": (0.0, -1000.0)})
        assert probability(said("a"), p) == 0.0
        p = Params(calibration={"speech/exact": (0.0, 1000.0)})
        assert probability(said("a"), p) == 1.0


# --- filters ----------------------------------------------------------------------------------
class TestFilters:
    def test_translated_false_drops_translated_speech(self):
        hits = [said("a", translated=True), said("b"),
                said("c", method="vec", sim=0.8, translated=True), said("d", method="vec", sim=0.8)]
        both = ranking.rank(hits, "x y z", with_(LEGACY, translated=True))
        assert {v["assetId"] for v in both} == {"a", "b", "c", "d"}
        only_original = ranking.rank(hits, "x y z", with_(LEGACY, translated=False))
        assert {v["assetId"] for v in only_original} == {"b", "d"}

    def test_translated_false_leaves_other_kinds_alone(self):
        hits = [vis("a"), ocr("b"), said("c", translated=True)]
        got = ranking.rank(hits, "hello", with_(LEGACY, translated=False))
        assert {v["assetId"] for v in got} == {"a", "b"}

    def test_translated_moments_are_prefixed(self):
        (video,) = ranking.rank([said("a", text="a dog barks", translated=True)], "dog")
        assert video["best"]["text"] == "EN: a dog barks"

    @pytest.mark.parametrize("kinds, expected", [
        (("visual",), {"v"}),
        (("speech",), {"s"}),
        (("text",), {"t"}),
        (("speech", "text"), {"s", "t"}),
        (("visual", "speech", "text"), {"v", "s", "t"}),
        ((), set()),
    ])
    def test_kinds_filter(self, kinds, expected):
        hits = [vis("v"), said("s"), ocr("t")]
        got = ranking.rank(hits, "hello", with_(LEGACY, kinds=kinds))
        assert {v["assetId"] for v in got} == expected

    def test_kinds_filter_applies_before_thresholds(self):
        # the strong visual match must not set the visual threshold when visual is excluded
        hits = [vis("a", 0.15), vis("b", 0.09)]
        got = ranking.rank(hits, "x", with_(LEGACY, kinds=("visual",)))
        assert [v["assetId"] for v in got] == ["a", "b"]


# --- exact flag -------------------------------------------------------------------------------
def test_exact_flag_only_for_method_exact():
    hits = [vis("v"), said("se"), said("sv", method="vec", sim=0.9), ocr("te"), ocr("tf", method="fuzzy", sim=0.7)]
    got = by_asset(ranking.rank(hits, "x y z"))
    assert {a: v["best"]["exact"] for a, v in got.items()} == \
        {"v": False, "se": True, "sv": False, "te": True, "tf": False}


def test_exact_flag_per_moment():
    hits = [said("a", ms=0), said("a", ms=60_000, method="vec", sim=0.9, text="y")]
    (video,) = ranking.rank(hits, "x y z")
    assert [(m["ms"], m["exact"]) for m in video["moments"]] == [(0, True), (60_000, False)]


# --- thresholds, grouping ---------------------------------------------------------------------
class TestScoreAndGroup:
    def test_visual_below_floor_is_dropped(self):
        assert ranking.rank([vis("a", 0.06)], "x") == []

    def test_visual_spread_drops_videos_far_below_the_best(self):
        got = ranking.rank([vis("a", 0.15), vis("b", 0.10), vis("c", 0.08)], "x")
        assert [v["assetId"] for v in got] == ["a", "b"]  # cut = 0.15 - 0.06 = 0.09

    def test_visual_frames_within_a_video_must_be_near_its_best(self):
        # all three clear the cut (0.09), but only frames within 0.03 of the video's best (0.15) stay
        hits = [vis("a", 0.15, ms=0), vis("a", 0.13, ms=10_000), vis("a", 0.10, ms=20_000)]
        (video,) = ranking.rank(hits, "x")
        assert [m["ms"] for m in video["moments"]] == [0, 10_000]

    def test_exact_speech_scores_one_and_hides_the_same_segment_semantically(self):
        hits = [said("a", ms=1000), said("a", ms=1000, method="vec", sim=0.9)]
        (video,) = ranking.rank(hits, "x y z")
        assert [h.method for h, _ in ranking.score(hits, "x y z")] == ["exact"]  # the dedup is in score()
        assert len(video["moments"]) == 1 and video["best"]["score"] == 1.0

    def test_speech_vec_rescale_range(self):
        # three-word query: floor 0.60, spread 0.15 -> cut 0.75; scores run 0.4 (cut) to 0.85 (top)
        hits = [said("a", method="vec", sim=0.9), said("b", method="vec", sim=0.76), said("c", method="vec", sim=0.7)]
        got = by_asset(ranking.rank(hits, "x y z"))
        assert got["a"]["best"]["score"] == 0.85
        assert got["b"]["best"]["score"] == pytest.approx(0.4 + 0.45 * 0.01 / 0.15, abs=1e-3)
        assert "c" not in got

    def test_text_scores(self):
        hits = [ocr("a"), ocr("b", method="fuzzy", sim=0.3)]
        got = by_asset(ranking.rank(hits, "x"))
        assert got["a"]["score"] == 1.0
        assert got["b"]["score"] == 0.8  # fuzzy is a flat 0.8 whatever pg_trgm said

    def test_moments_within_gap_collapse_per_kind_only(self):
        hits = [ocr("a", ms=1000, text="1"), ocr("a", ms=3000, text="2"), said("a", ms=1500)]
        (video,) = ranking.rank(hits, "x")
        assert sorted((m["kind"], m["ms"]) for m in video["moments"]) == [("speech", 1500), ("text", 1000)]
        # exactly the gap apart is not "farther than"
        hits = [ocr("a", ms=0), ocr("a", ms=2500), ocr("a", ms=5001)]
        (video,) = ranking.rank(hits, "x")
        assert [m["ms"] for m in video["moments"]] == [0, 5001]

    def test_max_moments(self):
        hits = [ocr("a", ms=i * 10_000) for i in range(10)]
        (video,) = ranking.rank(hits, "x")
        assert len(video["moments"]) == 6
        assert len(ranking.rank(hits, "x", with_(LEGACY, max_moments=2))[0]["moments"]) == 2

    def test_moments_sorted_by_time_best_is_highest_score(self):
        hits = [said("a", ms=9000, method="vec", sim=0.8, text="x"), said("a", ms=0, method="vec", sim=0.9, text="y")]
        (video,) = ranking.rank(hits, "x y z")
        assert [m["ms"] for m in video["moments"]] == [0, 9000]
        assert video["best"]["ms"] == 0

    def test_limit(self):
        hits = [ocr(f"a{i:02d}") for i in range(10)]
        assert len(ranking.rank(hits, "x", limit=3)) == 3

    def test_videos_sorted_by_score_descending(self):
        hits = [ocr("fuzzy", method="fuzzy", sim=0.5), said("vec", method="vec", sim=0.9), ocr("exact")]
        got = ranking.rank(hits, "x y z")
        assert [v["assetId"] for v in got] == ["exact", "vec", "fuzzy"]  # 1.0, 0.85, 0.8

    def test_no_hits(self):
        assert ranking.rank([], "anything") == []

    def test_asset_id_is_stringified(self):
        import uuid
        u = uuid.uuid4()
        assert ranking.rank([ocr(u)], "x")[0]["assetId"] == str(u)
