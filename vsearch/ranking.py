"""Scoring, fusion and grouping for search hits. Pure functions and stdlib only, so the
same code ranks live searches and the offline evaluation (eval/), and can be unit-tested
without a database.

search.py turns SQL rows into Hits; everything after that happens here."""
import math
from dataclasses import dataclass, field, replace
from typing import NamedTuple


class Hit(NamedTuple):
    """One candidate moment, before any threshold.

    method: "vec"   - embedding similarity (CLIP frame or bge-m3 segment); sim is the cosine
            "exact" - the query's words appear in the transcript / OCR text; sim is 1.0
            "fuzzy" - trigram word similarity against OCR (misread letters); sim is pg_trgm's
    """
    asset: str
    ms: int
    kind: str  # visual | speech | text
    method: str
    sim: float
    text: str | None = None
    translated: bool = False  # speech: matched Whisper's English translation, not the original


@dataclass(frozen=True)
class Params:
    # Calibrated with test/calibrate.py on the four functional-test clips (see docs/models.md);
    # eval/ replaces these with values chosen on the benchmark's dev split.
    visual_floor: float = 0.07     # a frame below this doesn't match at all
    visual_spread: float = 0.06    # keep videos within this much of the best visual match
    speech_floor: float = 0.60
    # 1-2 word queries embed vaguely ("a cat" scored 0.606 against a dog sentence)
    speech_floor_short: float = 0.65
    speech_spread: float = 0.15
    fuzzy_score: float = 0.8
    # How per-kind matches combine into one video score:
    #   legacy     - best kind + 0.1 * the others, on per-query rescaled scores
    #   rrf        - reciprocal rank fusion of the per-kind rankings
    #   calibrated - per-kind logistic fit of P(correct video | similarity), noisy-OR across kinds
    fusion: str = "legacy"
    rrf_k: int = 60
    # calibrated: {"visual/vec": (a, b), ...} with P = sigmoid(a * sim + b)
    calibration: dict = field(default_factory=dict)
    min_prob: float = 0.0          # calibrated: drop videos below this probability
    kinds: tuple = ("visual", "speech", "text")
    translated: bool = True        # use Whisper's English translations of non-English speech
    moment_gap_ms: int = 2500      # moments closer than this collapse into one
    max_moments: int = 6


DEFAULT = Params()


def with_(params: Params, **changes) -> Params:
    return replace(params, **changes)


def _rescale(sim: float, cut: float, top: float, lo: float, width: float) -> float:
    return lo + width * (sim - cut) / max(top - cut, 1e-6)


def score(hits: list[Hit], query: str, p: Params = DEFAULT) -> list[tuple]:
    """Thresholds + per-hit scores. Returns (hit, score) pairs, in the hits' order.

    Within a kind the score only orders hits; how kinds compare is fusion's job."""
    hits = [h for h in hits if h.kind in p.kinds and (p.translated or not h.translated)]
    out: list[tuple] = []

    visual = [h for h in hits if h.kind == "visual"]
    if visual:
        best: dict = {}
        for h in visual:
            best[h.asset] = max(best.get(h.asset, -1.0), h.sim)
        top = max(best.values())
        cut = max(p.visual_floor, top - p.visual_spread)
        # within a video, only frames close to its own best frame
        out += [(h, _rescale(h.sim, cut, top, 0.4, 0.6)) for h in visual
                if h.sim >= cut and h.sim >= best[h.asset] - 0.03]

    exact_speech = [h for h in hits if h.kind == "speech" and h.method == "exact"]
    seen = {(h.asset, h.ms) for h in exact_speech}
    out += [(h, 1.0) for h in exact_speech]
    meaning = [h for h in hits if h.kind == "speech" and h.method == "vec"]
    if meaning:
        floor = p.speech_floor if len(query.split()) > 2 else p.speech_floor_short
        top = max(h.sim for h in meaning)
        cut = max(floor, top - p.speech_spread)
        out += [(h, _rescale(h.sim, cut, top, 0.4, 0.45)) for h in meaning
                if h.sim >= cut and (h.asset, h.ms) not in seen]

    out += [(h, 1.0) for h in hits if h.kind == "text" and h.method == "exact"]
    out += [(h, p.fuzzy_score) for h in hits if h.kind == "text" and h.method == "fuzzy"]
    return out


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x)) if x > -60 else 0.0


def probability(hit: Hit, p: Params) -> float:
    a, b = p.calibration.get(f"{hit.kind}/{hit.method}", (0.0, 0.0))
    return _sigmoid(a * hit.sim + b)


def fuse(per_kind: dict[str, float], ranks: dict[str, int], p: Params) -> float:
    """One video's score from its best score per kind (and, for rrf, its rank per kind)."""
    if p.fusion == "rrf":
        return sum(1 / (p.rrf_k + r) for r in ranks.values())
    if p.fusion == "calibrated":
        miss = 1.0
        for prob in per_kind.values():
            miss *= 1 - prob
        return 1 - miss
    scores = sorted(per_kind.values(), reverse=True)
    return scores[0] + 0.1 * sum(scores[1:])  # matching several ways ranks higher


def group(scored: list[tuple], p: Params = DEFAULT, limit: int = 40) -> list[dict]:
    """Videos with their best moments, best first."""
    videos: dict = {}
    for h, s in scored:
        if p.fusion == "calibrated":
            s = probability(h, p)
        v = videos.setdefault(h.asset, {"assetId": str(h.asset), "kinds": {}, "moments": []})
        v["kinds"][h.kind] = max(v["kinds"].get(h.kind, 0.0), s)
        v["moments"].append({"ms": h.ms, "kind": h.kind, "score": round(s, 3), "text": _said(h),
                             "exact": h.method == "exact"})

    # rank of each video within each kind, for rrf
    ranks: dict = {asset: {} for asset in videos}
    for kind in p.kinds:
        order = sorted((a for a in videos if kind in videos[a]["kinds"]), key=lambda a: -videos[a]["kinds"][kind])
        for r, a in enumerate(order, 1):
            ranks[a][kind] = r

    out = []
    for asset, v in videos.items():
        # rrf scores are ~1/60 apart, so they need more digits than the original's 3
        v["score"] = round(fuse(v["kinds"], ranks[asset], p), 3 if p.fusion == "legacy" else 6)
        if p.fusion == "calibrated" and v["score"] < p.min_prob:
            continue
        picked: list[dict] = []
        for m in sorted(v["moments"], key=lambda m: -m["score"]):
            if all(m["kind"] != q["kind"] or abs(m["ms"] - q["ms"]) > p.moment_gap_ms for q in picked):
                picked.append(m)
            if len(picked) == p.max_moments:
                break
        v["best"] = picked[0]
        v["moments"] = sorted(picked, key=lambda m: m["ms"])
        v["matched"] = sorted(v.pop("kinds"))
        out.append(v)
    out.sort(key=lambda v: -v["score"])
    return out[:limit]


def _said(h: Hit) -> str | None:
    if h.kind == "speech" and h.translated and h.text is not None:
        return f"EN: {h.text}"
    return h.text


def rank(hits: list[Hit], query: str, p: Params = DEFAULT, limit: int = 40) -> list[dict]:
    return group(score(hits, query, p), p, limit)
