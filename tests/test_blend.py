import random

import pytest

from vsearch.immich_search import blend, mergeable


def immich(*ids):
    return [{"id": i} for i in ids]


def ours(*specs):
    """specs: asset id, optionally suffixed with '!' when one of its moments is an exact match."""
    out = []
    for spec in specs:
        exact = spec.endswith("!")
        out.append({"assetId": spec.rstrip("!"), "moments": [{"ms": 0, "kind": "speech", "exact": False},
                                                              {"ms": 5000, "kind": "text", "exact": exact}]})
    return out


class TestBlend:
    def test_no_matches_keeps_immichs_order(self):
        assert blend(immich("a", "b", "c"), []) == ["a", "b", "c"]

    def test_exact_videos_go_first(self):
        assert blend(immich("a", "b", "c"), ours("c!")) == ["c", "a", "b"]

    def test_exact_videos_keep_our_order_and_need_not_be_in_immich(self):
        assert blend(immich("a", "b"), ours("x!", "b!", "y!")) == ["x", "b", "y", "a"]

    def test_exact_means_any_moment_is_exact(self):
        r = {"assetId": "z", "moments": [{"exact": False}, {"exact": False}, {"exact": True}]}
        assert blend(immich("a"), [r])[0] == "z"

    def test_moments_without_exact_key_are_not_exact(self):
        # woven in as a plain match (key 0.5, after "a"), not promoted to the front
        assert blend(immich("a", "b", "c"), [{"assetId": "c", "moments": [{"ms": 0}]}]) == ["a", "c", "b"]

    def test_non_exact_match_is_woven_in_near_the_top(self):
        # keys: a 0, x 0.5, b 1, c 2
        assert blend(immich("a", "b", "c"), ours("x")) == ["a", "x", "b", "c"]

    def test_non_exact_matches_outside_immich_interleave_every_other_slot(self):
        # keys: a 0, x 0.5, b 1, c 2, y 2.5, d 3, z 4.5
        assert blend(immich("a", "b", "c", "d"), ours("x", "y", "z")) == ["a", "x", "b", "c", "y", "d", "z"]

    def test_non_exact_already_in_immich_moves_up_but_never_down(self):
        # c is at Immich rank 2; as our best match it is lifted to 0.5. d is ours at index 1 -> 2.5
        # but Immich had it at 3 (key 3) -> min is 2.5 (up). b is ours at index 2 -> 4.5 > key 1: stays at 1.
        got = blend(immich("a", "b", "c", "d"), ours("c", "d", "b"))
        assert got == ["a", "c", "b", "d"]

    def test_non_exact_match_late_in_our_list_does_not_sink_below_immichs_rank(self):
        # as our 4th match "a" would be keyed 6.5, but Immich already had it first (key 0)
        got = blend(immich("a", "b", "c"), ours("x", "y", "z", "a"))
        assert got.index("a") < got.index("b")

    def test_exact_beats_non_exact_beats_the_rest(self):
        got = blend(immich("a", "b", "c", "d"), ours("c", "d!"))
        assert got[0] == "d"
        assert got.index("c") < got.index("b")

    def test_no_duplicates_and_nothing_lost(self):
        got = blend(immich("a", "b", "c"), ours("c!", "b", "x", "y!"))
        assert sorted(got) == ["a", "b", "c", "x", "y"]

    def test_empty_everything(self):
        assert blend([], []) == []

    def test_immich_empty_only_ours(self):
        assert blend([], ours("x", "y!")) == ["y", "x"]


def test_blend_invariants_on_random_inputs():
    rng = random.Random(1)
    pool = [f"a{i}" for i in range(30)]
    for _ in range(500):
        im = rng.sample(pool, rng.randint(0, 20))
        mine = [(a, rng.random() < 0.3) for a in rng.sample(pool, rng.randint(0, 12))]
        got = blend(immich(*im), ours(*[a + ("!" if e else "") for a, e in mine]))
        exact = [a for a, e in mine if e]
        woven = [a for a, e in mine if not e]
        assert len(got) == len(set(got)) and set(got) == set(im) | {a for a, _ in mine}
        # exact first, in our order
        assert got[:len(exact)] == exact
        # items we know nothing about keep Immich's relative order
        others = [a for a in im if a not in exact and a not in woven]
        assert [a for a in got if a in set(others)] == others
        # a woven video is never pushed below an Immich item it was ranked above
        for a in woven:
            if a in im:
                below = [b for b in others if im.index(b) > im.index(a)]
                assert all(got.index(a) < got.index(b) for b in below)


# --- mergeable --------------------------------------------------------------------------------
PHONE_APP_DEFAULTS = {
    "query": "birthday cake", "page": 1, "size": 100, "withExif": True, "isNotInAlbum": False,
    "personIds": [], "tagIds": None, "albumIds": [], "city": None, "state": None, "country": None,
    "make": None, "model": None, "lensModel": None, "libraryId": None, "rating": None, "isFavorite": None,
    "isEncoded": None, "isMotion": None, "isOffline": None, "takenAfter": None, "takenBefore": None,
    "createdAfter": None, "createdBefore": None, "updatedAfter": None, "updatedBefore": None,
    "trashedAfter": None, "trashedBefore": None, "queryAssetId": None, "language": "en",
    "visibility": None, "type": None, "withDeleted": False, "withStacked": False, "ocr": "",
}


class TestMergeable:
    def test_plain_query(self):
        assert mergeable({"query": "cat"})

    def test_empty_query(self):
        assert not mergeable({"query": ""})
        assert not mergeable({"query": "   "})
        assert not mergeable({"query": None})
        assert not mergeable({})

    def test_not_a_dict(self):
        assert not mergeable(None)
        assert not mergeable("cat")
        assert not mergeable(["cat"])

    def test_phone_app_empty_defaults_are_still_mergeable(self):
        assert mergeable(PHONE_APP_DEFAULTS)

    @pytest.mark.parametrize("empty", [False, None, [], ""])
    def test_each_kind_of_empty_default(self, empty):
        assert mergeable({"query": "cat", "personIds": empty, "isFavorite": empty})

    def test_people_filter_set_is_not_mergeable(self):
        assert not mergeable({"query": "cat", "personIds": ["8f3c"]})
        assert not mergeable({**PHONE_APP_DEFAULTS, "personIds": ["8f3c"]})

    @pytest.mark.parametrize("field, value", [
        ("city", "Pune"), ("country", "India"), ("albumIds", ["x"]), ("tagIds", ["t"]),
        ("takenAfter", "2023-01-01T00:00:00.000Z"), ("isFavorite", True), ("rating", 5),
        ("queryAssetId", "abc"), ("libraryId", "lib"), ("model", "Pixel"), ("unknownNewFilter", 1),
    ])
    def test_any_other_filter_that_is_set_is_not_mergeable(self, field, value):
        assert not mergeable({"query": "cat", field: value})

    def test_type_image_is_not_mergeable(self):
        assert not mergeable({"query": "cat", "type": "IMAGE"})
        assert not mergeable({**PHONE_APP_DEFAULTS, "type": "IMAGE"})

    def test_type_video_and_null_are_mergeable(self):
        assert mergeable({"query": "cat", "type": "VIDEO"})
        assert mergeable({"query": "cat", "type": None})

    def test_type_other_is_not_mergeable(self):
        assert not mergeable({"query": "cat", "type": "AUDIO"})

    def test_with_deleted_and_not_in_album(self):
        assert not mergeable({"query": "cat", "withDeleted": True})
        assert not mergeable({"query": "cat", "isNotInAlbum": True})
        assert mergeable({"query": "cat", "withDeleted": False, "isNotInAlbum": False})

    @pytest.mark.parametrize("visibility, ok", [
        (None, True), ("timeline", True), ("archive", False), ("hidden", False), ("locked", False),
    ])
    def test_visibility(self, visibility, ok):
        assert mergeable({"query": "cat", "visibility": visibility}) is ok

    def test_paging_and_size_do_not_matter(self):
        assert mergeable({"query": "cat", "page": 7, "size": 250, "withExif": False, "language": "hi"})
