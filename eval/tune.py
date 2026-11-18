"""Chooses the fixes' settings on the DEV split only, and writes eval/tuned.json (which
run.py then scores on test) plus eval/tuning.md and the threshold curves in docs/img/.

  uv run --with matplotlib python eval/tune.py

Selection rules, fixed before looking at any test number:
  Fix A  whole-word lexical matching; no parameters.
  Fix B  fusion: legacy vs reciprocal rank fusion vs per-kind logistic calibration; keep the
         highest dev macro-MRR (mean of the three query types' MRR, so the larger Charades
         set doesn't outvote FLEURS). If none beats legacy, keep legacy.
  Fix C  floors (visual, speech, short-query speech): lowest dev no-answer false-positive
         rate whose dev macro-R@10 is within 1 point of the untuned setting's."""
import json
import math
import sys

import metrics
from run import TYPES, clean, load_hits
from store import DATA, EVAL, ROOT, queries, read_jsonl

sys.path.insert(0, str(ROOT))
from vsearch import ranking  # noqa: E402

RECALL_TOLERANCE = 0.01


def prepare(mode):
    qs = [q for q in queries() if q["split"] == "dev"]
    cands = {r["qid"]: r for r in read_jsonl(DATA / "candidates.jsonl")}
    assets = json.loads((DATA / "assets.json").read_text())
    return [(q, load_hits(cands[q["qid"]], mode)) for q in qs], assets


def score_dev(prepared, assets, params) -> dict:
    by_type = {t: [] for t in TYPES}
    noans = []
    for q, hits in prepared:
        results = ranking.rank(hits, clean(q["query"]), params, 40)
        if q["type"] == "no_answer":
            noans.append({"returned": len(results)})
            continue
        ranked = [assets[r["assetId"]] for r in results]
        by_type[q["type"]].append(metrics.rank_of(q["gold"], ranked))
    return {
        "macro_MRR": sum(metrics.mrr(r) for r in by_type.values()) / len(by_type),
        "macro_R@1": sum(metrics.recall_at(r, 1) for r in by_type.values()) / len(by_type),
        "macro_R@10": sum(metrics.recall_at(r, 10) for r in by_type.values()) / len(by_type),
        "noans_FP": sum(n["returned"] > 0 for n in noans) / len(noans) if noans else None,
        **{f"{t} MRR": metrics.mrr(r) for t, r in by_type.items()},
    }


def fit_logistic(xs, ys, iters=50):
    """1-D logistic regression by Newton's method; P(y=1|x) = sigmoid(a*x + b). A constant
    feature (exact matches all have sim 1.0) just gets its base rate."""
    pos = sum(ys)
    if not xs:
        return 0.0, -5.0
    if max(xs) - min(xs) < 1e-9 or pos in (0, len(ys)):
        rate = min(max(pos / len(ys), 1e-3), 1 - 1e-3)
        return 0.0, math.log(rate / (1 - rate))
    a, b = 0.0, 0.0
    for _ in range(iters):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, ys):
            p = 1 / (1 + math.exp(-(a * x + b)))
            ga += (p - y) * x
            gb += p - y
            w = p * (1 - p) + 1e-9
            haa += w * x * x
            hab += w * x
            hbb += w
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) + abs(db) < 1e-7:
            break
    return a, b


def calibrate(prepared, assets, params) -> dict:
    """Per kind/method: does a video's best similarity predict that it's the gold video?
    One sample per (query, video) that has hits of that kind/method, on dev answerable queries."""
    samples: dict = {}
    for q, hits in prepared:
        if q["type"] == "no_answer":
            continue
        best: dict = {}
        for h, _ in ranking.score(hits, clean(q["query"]), ranking.with_(params, fusion="legacy")):
            key = (f"{h.kind}/{h.method}", h.asset)
            best[key] = max(best.get(key, -1.0), h.sim)
        for (km, asset), sim in best.items():
            samples.setdefault(km, ([], []))
            samples[km][0].append(sim)
            samples[km][1].append(int(assets[asset] == q["gold"]))
    return {km: fit_logistic(xs, ys) for km, (xs, ys) in samples.items()}


def main():
    prepared, assets = prepare("substr")
    prepared_word, _ = prepare("word")
    base = ranking.LEGACY  # the shipped defaults may change; the baseline must not
    report = {"baseline": score_dev(prepared, assets, base), "fix A": score_dev(prepared_word, assets, base)}
    print("dev baseline:", report["baseline"])
    print("dev fix A:   ", report["fix A"])

    # Fix B, on top of Fix A
    candidates = {"legacy": base}
    for k in (10, 30, 60):
        candidates[f"rrf k={k}"] = ranking.with_(base, fusion="rrf", rrf_k=k)
    calib = calibrate(prepared_word, assets, base)
    # The benchmark has no on-screen-text queries, so OCR gets no fit of its own: an exact OCR
    # match borrows the spoken exact match's (both mean "the query's words are there"), and a
    # fuzzy one stays at even odds.
    calib.setdefault("text/exact", calib.get("speech/exact", (0.0, 0.0)))
    calib.setdefault("text/fuzzy", (0.0, 0.0))
    candidates["calibrated"] = ranking.with_(base, fusion="calibrated", calibration=calib)
    fusion = {name: score_dev(prepared_word, assets, p) for name, p in candidates.items()}
    for name, s in fusion.items():
        print(f"dev fusion {name:12}: macro-MRR {s['macro_MRR']:.4f} R@1 {s['macro_R@1']:.4f} FP {s['noans_FP']}")
    winner = max(fusion, key=lambda n: (fusion[n]["macro_MRR"], n == "legacy"))
    if fusion[winner]["macro_MRR"] <= fusion["legacy"]["macro_MRR"]:
        winner = "legacy"
    report["fix B candidates"] = fusion
    report["fix B winner"] = winner
    b = candidates[winner]

    # Fix C, on top of A + B: one floor at a time
    untuned = score_dev(prepared_word, assets, b)
    target = untuned["macro_R@10"] - RECALL_TOLERANCE
    curves = {}
    c = b
    grids = {
        "visual_floor": [round(0.05 + 0.005 * i, 3) for i in range(25)],
        "speech_floor": [round(0.50 + 0.01 * i, 2) for i in range(26)],
        "speech_floor_short": [round(0.50 + 0.01 * i, 2) for i in range(31)],
    }
    free_short = None
    for field, grid in grids.items():
        if field == "speech_floor_short":
            # Every no-answer query has 3+ words, so the benchmark can't see what a looser floor
            # for 1-2 word queries costs (it exists because "a cat" embedded close to a dog
            # sentence). Never let it go below the normal floor; the unconstrained pick is
            # reported as a variant.
            free = [{"value": v, **score_dev(prepared_word, assets, ranking.with_(c, **{field: v}))} for v in grid]
            ok_free = [pt for pt in free if pt["macro_R@10"] >= target]
            free_short = min(ok_free, key=lambda pt: (pt["noans_FP"], -pt["macro_R@10"]))["value"] if ok_free else None
            grid = [v for v in grid if v >= c.speech_floor]
        points = []
        for value in grid:
            s = score_dev(prepared_word, assets, ranking.with_(c, **{field: value}))
            points.append({"value": value, **s})
        curves[field] = points
        ok = [pt for pt in points if pt["macro_R@10"] >= target]
        best = min(ok, key=lambda pt: (pt["noans_FP"], -pt["macro_R@10"])) if ok else None
        if best:
            c = ranking.with_(c, **{field: best["value"]})
        print(f"dev {field}: chose {getattr(c, field)}")
    report["fix C"] = report.get("fix C", {}) | {"untuned": untuned, "chosen": {f: getattr(c, f) for f in grids},
                       "tuned": score_dev(prepared_word, assets, c), "curves": curves}

    def spec(p, lex, desc):
        d = {k: v for k, v in p.__dict__.items() if v != getattr(base, k)}
        if "calibration" in d:
            d["calibration"] = {k: list(v) for k, v in d["calibration"].items()}
        if "kinds" in d:
            d["kinds"] = list(d["kinds"])
        return {"params": d, "lexical": lex, "description": desc}

    tuned = {
        "fix A: whole-word match": spec(base, "word", "speech and OCR exact matches only on word boundaries "
                                        "(vsearch/lexical.py) instead of ILIKE substring"),
        f"fix A+B: {winner} fusion": spec(b, "word", f"A, plus score fusion = {winner} (chosen on dev)"),
        "fix A+B+C: tuned floors": spec(c, "word", "A+B, plus visual/speech floors chosen on dev "
                                        + json.dumps({f: getattr(c, f) for f in grids})),
    }
    if free_short is not None and free_short != c.speech_floor_short:
        tuned["variant: C with a free short-query floor (not shipped)"] = spec(
            ranking.with_(c, speech_floor_short=free_short), "word",
            f"short-query floor {free_short}: unconstrained dev pick, whose false-positive cost the "
            "benchmark can't measure (no 1-2 word no-answer queries)")
        report["fix C"]["free_short_floor"] = free_short
    (EVAL / "tuned.json").write_text(json.dumps(tuned, indent=1))
    (DATA / "tune_report.json").write_text(json.dumps(report, indent=1))
    (EVAL / "tuning.md").write_text(tuning_md(report))
    plot(curves, report["fix C"]["chosen"])
    print("wrote eval/tuned.json, eval/tuning.md")


def tuning_md(report) -> str:
    def row(name, s):
        fp = "–" if s["noans_FP"] is None else f"{100 * s['noans_FP']:.1f}"
        return (f"| {name} | {s['macro_MRR']:.3f} | {100 * s['macro_R@1']:.1f} | {100 * s['macro_R@10']:.1f} | "
                f"{fp} |")
    lines = ["# Tuning on the dev split", "", "Generated by `eval/tune.py`. Only dev queries are used here; "
             "the chosen settings are then scored on test in [results.md](results.md).", "",
             "| Setting | macro MRR | macro R@1 | macro R@10 | no-answer FP |", "|---|---:|---:|---:|---:|",
             row("baseline (as shipped)", report["baseline"]), row("fix A: whole-word match", report["fix A"])]
    lines += [row(f"fix A + fusion: {n}", s) for n, s in report["fix B candidates"].items()]
    lines += [row("fix A + B, untuned floors", report["fix C"]["untuned"]),
              row("fix A + B + C: tuned floors", report["fix C"]["tuned"]), "",
              f"Fusion kept: **{report['fix B winner']}**. Floors chosen: `{json.dumps(report['fix C']['chosen'])}` "
              f"(lowest no-answer FP with macro R@10 within {100 * RECALL_TOLERANCE:.0f} point of untuned).", "",
              "![threshold curves](../docs/img/threshold-curves.png)", ""]
    return "\n".join(lines)


def plot(curves, chosen):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed: skipping docs/img/threshold-curves.png")
        return
    fig, axes = plt.subplots(1, len(curves), figsize=(5 * len(curves), 3.6))
    for ax, (field, pts) in zip(axes, curves.items()):
        xs = [p["value"] for p in pts]
        ax.plot(xs, [100 * p["macro_R@10"] for p in pts], label="macro R@10 (answerable)")
        ax.plot(xs, [100 * p["macro_R@1"] for p in pts], label="macro R@1")
        ax.plot(xs, [100 * (p["noans_FP"] or 0) for p in pts], label="no-answer FP", linestyle="--")
        ax.axvline(chosen[field], color="grey", linewidth=0.8)
        ax.set_xlabel(field)
        ax.set_ylabel("% (dev)")
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    out = ROOT / "docs" / "img" / "threshold-curves.png"
    fig.savefig(out, dpi=120)
    print("wrote", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
