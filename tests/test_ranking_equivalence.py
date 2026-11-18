"""The ranking.py refactor must not change what a default search returns.

`old_rank` below is main's vsearch/search.py scoring (`_visual`, `_speech`, `_text`) and `_group`,
copied line for line with the SQL replaced by the Hit lists those queries would have produced
(`git show main:vsearch/search.py`). Random synthetic hit sets are pushed through both and
compared: video order, video scores, matched kinds and every moment's old fields."""
import random

import pytest

from vsearch import ranking
from vsearch.ranking import LEGACY, Hit

# --- main's constants -------------------------------------------------------------------------
VISUAL_FLOOR = 0.07
VISUAL_SPREAD = 0.06
SPEECH_FLOOR = 0.60
SPEECH_FLOOR_SHORT = 0.65
SPEECH_SPREAD = 0.15
MOMENT_GAP_MS = 2500
MAX_MOMENTS = 6


def _said(text, translated):
    return f"EN: {text}" if translated else text


# --- main's search.py, minus SQL --------------------------------------------------------------
def old_visual(rows):
    """rows: (asset_id, ms, sim)"""
    if not rows:
        return []
    best: dict = {}
    for asset_id, _, sim in rows:
        best[asset_id] = max(best.get(asset_id, -1.0), sim)
    top = max(best.values())
    cut = max(VISUAL_FLOOR, top - VISUAL_SPREAD)
    return [(asset_id, ms, "visual", 0.4 + 0.6 * (sim - cut) / max(top - cut, 1e-6), None)
            for asset_id, ms, sim in rows
            if sim >= cut and sim >= best[asset_id] - 0.03]


def old_speech(exact_rows, vec_rows, query):
    """exact_rows: (asset_id, ms, text, translated); vec_rows: (asset_id, ms, text, translated, sim)"""
    hits = []
    seen = set()
    for asset_id, ms, text, translated in exact_rows:
        seen.add((asset_id, ms))
        hits.append((asset_id, ms, "speech", 1.0, _said(text, translated)))
    rows = vec_rows
    if rows:
        floor = SPEECH_FLOOR if len(query.split()) > 2 else SPEECH_FLOOR_SHORT
        top = max(r[4] for r in rows)
        cut = max(floor, top - SPEECH_SPREAD)
        for asset_id, ms, text, translated, sim in rows:
            if sim >= cut and (asset_id, ms) not in seen:
                score = 0.4 + 0.45 * (sim - cut) / max(top - cut, 1e-6)
                hits.append((asset_id, ms, "speech", score, _said(text, translated)))
    return hits


def old_text(exact_rows, fuzzy_rows):
    """rows: (asset_id, ms, ocr)"""
    hits = [(asset_id, ms, "text", 1.0, ocr) for asset_id, ms, ocr in exact_rows]
    hits += [(asset_id, ms, "text", 0.8, ocr) for asset_id, ms, ocr in fuzzy_rows]
    return hits


def old_group(hits, limit):
    videos: dict = {}
    for asset_id, ms, kind, score, text in hits:
        v = videos.setdefault(asset_id, {"assetId": str(asset_id), "kinds": {}, "moments": []})
        v["kinds"][kind] = max(v["kinds"].get(kind, 0.0), score)
        v["moments"].append({"ms": ms, "kind": kind, "score": round(score, 3), "text": text})
    out = []
    for v in videos.values():
        scores = sorted(v["kinds"].values(), reverse=True)
        v["score"] = round(scores[0] + 0.1 * sum(scores[1:]), 3)  # matching several ways ranks higher
        picked: list[dict] = []
        for m in sorted(v["moments"], key=lambda m: -m["score"]):
            if all(m["kind"] != p["kind"] or abs(m["ms"] - p["ms"]) > MOMENT_GAP_MS for p in picked):
                picked.append(m)
            if len(picked) == MAX_MOMENTS:
                break
        v["best"] = picked[0]
        v["moments"] = sorted(picked, key=lambda m: m["ms"])
        v["matched"] = sorted(v.pop("kinds"))
        out.append(v)
    out.sort(key=lambda v: -v["score"])
    return out[:limit]


def old_rank(hits: list[Hit], query: str, limit: int = 40) -> list[dict]:
    """What main's run() computed for these candidate rows (after the query-string cleanup)."""
    query = " ".join(query.split())[:200]
    visual = [(h.asset, h.ms, h.sim) for h in hits if h.kind == "visual"]
    speech_exact = [(h.asset, h.ms, h.text, h.translated) for h in hits if h.kind == "speech" and h.method == "exact"]
    speech_vec = [(h.asset, h.ms, h.text, h.translated, h.sim)
                  for h in hits if h.kind == "speech" and h.method == "vec"]
    text_exact = [(h.asset, h.ms, h.text) for h in hits if h.kind == "text" and h.method == "exact"]
    text_fuzzy = [(h.asset, h.ms, h.text) for h in hits if h.kind == "text" and h.method == "fuzzy"]
    scored = (old_visual(visual) + old_speech(speech_exact, speech_vec, query)
              + old_text(text_exact, text_fuzzy))
    return old_group(scored, limit)


# --- random hit sets --------------------------------------------------------------------------
WORDS = "cat dog birthday goa beach कुत्ते party mummy cake sunset temple train rain".split()


def make_case(rng: random.Random) -> tuple[list[Hit], str]:
    query = " ".join(rng.choices(WORDS, k=rng.randint(1, 5)))
    n_assets = rng.choice([1, 2, 3, 5, 8, 15, 60])
    assets = [f"asset-{i:02d}" for i in range(n_assets)]
    # a coarse ms grid so different hits collide on (asset, ms) and fall inside the 2.5 s gap
    ms_grid = [500 * i for i in range(rng.choice([3, 10, 30]))]

    def pick():
        return rng.choice(assets), rng.choice(ms_grid)

    def blob():
        return f"text {rng.randint(0, 99)}"

    visual_base = rng.uniform(0.02, 0.15)
    speech_base = rng.uniform(0.45, 0.9)
    visual: list[Hit] = []
    if rng.random() < 0.8:
        for _ in range(rng.randint(1, 40)):
            asset, ms = pick()
            visual.append(Hit(asset, ms, "visual", "vec", round(visual_base + rng.uniform(-0.08, 0.04), 4)))
    speech_exact = []
    if rng.random() < 0.5:
        for _ in range(rng.randint(1, 8)):
            asset, ms = pick()
            speech_exact.append(Hit(asset, ms, "speech", "exact", 1.0, blob(), translated=rng.random() < 0.3))
    speech_vec = []
    if rng.random() < 0.7:
        for _ in range(rng.randint(1, 30)):
            asset, ms = pick()
            # the same (asset, ms) as an exact hit sometimes, to exercise `seen`
            if speech_exact and rng.random() < 0.3:
                asset, ms = rng.choice(speech_exact)[:2]
            speech_vec.append(Hit(asset, ms, "speech", "vec", round(speech_base + rng.uniform(-0.25, 0.1), 4),
                                  blob(), translated=rng.random() < 0.3))
    text_exact = []
    if rng.random() < 0.4:
        for _ in range(rng.randint(1, 8)):
            asset, ms = pick()
            text_exact.append(Hit(asset, ms, "text", "exact", 1.0, blob()))
    text_fuzzy = []
    if rng.random() < 0.4:
        for _ in range(rng.randint(1, 8)):
            asset, ms = pick()
            text_fuzzy.append(Hit(asset, ms, "text", "fuzzy", round(rng.uniform(0.3, 0.9), 3), blob()))
    # the order the old code saw them in: visual, speech exact, speech vec, text exact, text fuzzy
    return visual + speech_exact + speech_vec + text_exact + text_fuzzy, query


def old_fields(video: dict) -> dict:
    return {
        "assetId": video["assetId"],
        "score": video["score"],
        "matched": video["matched"],
        "best": {k: video["best"][k] for k in ("ms", "kind", "score", "text")},
        "moments": [{k: m[k] for k in ("ms", "kind", "score", "text")} for m in video["moments"]],
    }


N_CASES = 3000


def test_rank_matches_the_pre_refactor_search():
    rng = random.Random(20241119)
    compared = multi_kind = multi_moment = collapsed = clipped = non_empty = 0
    for i in range(N_CASES):
        hits, query = make_case(rng)
        old = old_rank(hits, query)
        new = ranking.rank(hits, query, LEGACY)

        label = f"case {i}: query={query!r}, {len(hits)} hits"
        assert [v["assetId"] for v in new] == [v["assetId"] for v in old], f"video order differs ({label})"
        assert [v["score"] for v in new] == [v["score"] for v in old], f"video scores differ ({label})"
        assert [(v["best"]["ms"], v["best"]["kind"]) for v in new] == \
               [(v["best"]["ms"], v["best"]["kind"]) for v in old], f"best moment differs ({label})"
        # everything else the old code returned, field for field (new moments only add "exact")
        assert [old_fields(v) for v in new] == [old_fields(v) for v in old], f"moments differ ({label})"

        compared += len(old)
        non_empty += bool(old)
        multi_kind += sum(len(v["matched"]) > 1 for v in old)
        multi_moment += sum(len(v["moments"]) > 1 for v in old)
        clipped += sum(len(v["moments"]) == MAX_MOMENTS for v in old)
        collapsed += any(len(v["moments"]) < sum(1 for h in hits if h.asset == v["assetId"]) for v in old)

    # the generator has to actually exercise the interesting branches, or equality proves little
    assert non_empty > N_CASES * 0.8
    assert compared > 5000
    assert multi_kind > 500, "too few videos matched by several kinds"
    assert multi_moment > 1000, "too few videos with several moments"
    assert collapsed > 300, "the moment-gap collapse was rarely exercised"
    assert clipped > 20, "max_moments was rarely reached"


def test_limit_matches_old_group():
    rng = random.Random(7)
    for _ in range(200):
        hits, query = make_case(rng)
        for limit in (1, 3, 40):
            old = old_rank(hits, query, limit)
            new = ranking.rank(hits, query, LEGACY, limit)
            assert [old_fields(v) for v in new] == [old_fields(v) for v in old]


@pytest.mark.parametrize("query", ["a cat", "cat", "a cat on the sofa", "  a   cat  ", "one two three"])
def test_short_query_uses_the_higher_speech_floor(query):
    """1-2 word queries need 0.65, longer ones 0.60 (a 0.62 hit passes only for the long query)."""
    hits = [Hit("a", 0, "speech", "vec", 0.62, "x")]
    assert [v["assetId"] for v in ranking.rank(hits, query, LEGACY)] == [v["assetId"] for v in old_rank(hits, query)]
    assert bool(ranking.rank(hits, query, LEGACY)) == (len(query.split()) > 2)


def test_old_helper_is_main_search():
    """Guard the guard: the fixture constants above must stay in step with the live defaults."""
    p = LEGACY
    assert (p.visual_floor, p.visual_spread, p.speech_floor, p.speech_floor_short, p.speech_spread) == \
           (VISUAL_FLOOR, VISUAL_SPREAD, SPEECH_FLOOR, SPEECH_FLOOR_SHORT, SPEECH_SPREAD)
    assert (p.moment_gap_ms, p.max_moments, p.fuzzy_score, p.fusion) == (MOMENT_GAP_MS, MAX_MOMENTS, 0.8, "legacy")
