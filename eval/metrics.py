"""Metric definitions (stdlib only; unit-tested in tests/test_metrics.py).

A system answers a query with a ranked list of file names. The query's gold is one file
(or None for no-answer queries) and, for Charades, a time window in seconds."""
import statistics

MOMENT_TOLERANCE_S = 1.0


def rank_of(gold: str, ranked: list[str]) -> int | None:
    """1-based rank of the gold file, or None if it isn't in the list."""
    try:
        return ranked.index(gold) + 1
    except ValueError:
        return None


def recall_at(ranks: list[int | None], k: int) -> float:
    return sum(r is not None and r <= k for r in ranks) / len(ranks) if ranks else 0.0


def mrr(ranks: list[int | None]) -> float:
    return sum(1 / r for r in ranks if r) / len(ranks) if ranks else 0.0


def moment_hit(gold: str, window, top_file: str | None, jump_ms: int | None,
               tolerance_s: float = MOMENT_TOLERANCE_S) -> bool:
    """The top result is the gold video and the player's jump time (its best moment)
    lands inside the gold window, give or take the tolerance."""
    if top_file != gold or jump_ms is None or window is None:
        return False
    start, end = window
    return start - tolerance_s <= jump_ms / 1000 <= end + tolerance_s


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    # same as numpy's default (linear interpolation)
    pos = (len(values) - 1) * p / 100
    lo, hi = int(pos), min(int(pos) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def summary(answerable: list[dict], no_answer: list[dict]) -> dict:
    """answerable rows: {rank, moment (bool or None)}; no_answer rows: {returned (int)}."""
    ranks = [row["rank"] for row in answerable]
    moments = [row["moment"] for row in answerable if row.get("moment") is not None]
    out = {
        "n": len(answerable),
        "R@1": recall_at(ranks, 1), "R@5": recall_at(ranks, 5), "R@10": recall_at(ranks, 10),
        "MRR": mrr(ranks),
    }
    if moments:
        out["moment@1"] = sum(moments) / len(moments)
        out["n_moment"] = len(moments)
    if no_answer:
        out["noans_FP"] = sum(row["returned"] > 0 for row in no_answer) / len(no_answer)
        out["n_noans"] = len(no_answer)
    return out


def mean(values):
    return statistics.fmean(values) if values else None
