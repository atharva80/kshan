"""Scores every system and configuration from what live.py and collect.py saved, and writes

  eval/results/<date>-<shortsha>.json   raw numbers, config, commit, dataset hashes
  eval/results.md                       the readable tables (test split first, dev in an appendix)

  python3 eval/run.py

Systems:
  immich_before   plain Immich smart search before vsearch indexed anything (thumbnail only)
  immich_after    plain Immich after vsearch's write-back (the unmodified phone app)
  vsearch         the companion search (vsearch/search.py ranked by vsearch/ranking.py)
  proxy           Immich's search bar routed through vsearch (immich_search.blend)
plus ablations and the fixes, which re-rank the saved candidates under other settings.
Thresholds and fusion were chosen on dev (eval/tune.py); the headline numbers are test."""
import datetime
import json
import sys

import metrics
from store import DATA, EVAL, LIVE, RESULTS, ROOT, git, queries, read_jsonl

sys.path.insert(0, str(ROOT))
from vsearch import immich_search, ranking  # noqa: E402
from vsearch.ranking import Hit, Params  # noqa: E402

TYPES = ("charades_sentence", "fleurs_sentence", "fleurs_keyword")
LANGS = ("hi_in", "mr_in", "en_us")


def load_hits(cand: dict, mode: str) -> list[Hit]:
    """Rebuild search.candidates()'s Hits, in the same order, from collect.py's rows."""
    def h(rows, kind, method):
        return [Hit(a, ms, kind, method, sim, None, bool(tr)) for a, ms, sim, tr in rows]
    return (h(cand["visual"], "visual", "vec")
            + h(cand[f"speech_exact_{mode}"], "speech", "exact")
            + h(cand["speech_vec"], "speech", "vec")
            + h(cand[f"text_exact_{mode}"], "text", "exact")
            + h(cand[f"text_fuzzy_{mode}"], "text", "fuzzy"))


def configs() -> dict:
    """name -> (Params, lexical mode, description). The fixes' tuned settings come from tune.py."""
    base = ranking.LEGACY  # the shipped defaults may change; the baseline must not
    out = {
        "vsearch": (base, "substr", "as shipped: substring lexical match, rescaled scores, max+0.1*rest"),
        "ablation: visual only": (ranking.with_(base, kinds=("visual",)), "substr", ""),
        "ablation: speech only": (ranking.with_(base, kinds=("speech",)), "substr", ""),
        "ablation: OCR only": (ranking.with_(base, kinds=("text",)), "substr", ""),
        "ablation: speech only, no translation": (
            ranking.with_(base, kinds=("speech",), translated=False), "substr", ""),
        "ablation: all, no translation": (ranking.with_(base, translated=False), "substr", ""),
    }
    tuned = EVAL / "tuned.json"
    if tuned.exists():
        for name, spec in json.loads(tuned.read_text()).items():
            params = spec["params"]
            if "calibration" in params:
                params["calibration"] = {k: tuple(v) for k, v in params["calibration"].items()}
            if "kinds" in params:
                params["kinds"] = tuple(params["kinds"])
            out[name] = (ranking.with_(base, **params), spec["lexical"], spec.get("description", ""))
    return out


def clean(query: str) -> str:
    return " ".join(query.split())[:200]


def evaluate(qs, cands, assets, params, mode, immich_after=None, limit=40):
    """Per-query rows for a vsearch configuration (and, with immich_after, the simulated
    search-bar proxy built on it)."""
    rows, proxy_rows = [], []
    for q in qs:
        results = ranking.rank(load_hits(cands[q["qid"]], mode), clean(q["query"]), params,
                               100 if immich_after is not None else limit)
        ours = results[:limit]
        ranked = [assets[r["assetId"]] for r in ours]
        rows.append(row(q, ranked, ours[0]["best"]["ms"] if ours else None))
        if immich_after is not None:
            named = [{"assetId": assets[r["assetId"]], "moments": r["moments"]} for r in results]
            order = immich_search.blend([{"id": n} for n in immich_after[q["qid"]]], named)
            proxy_rows.append(row(q, order, None))
    return rows, proxy_rows


def row(q, ranked, jump_ms):
    gold = q.get("gold")
    return {
        "qid": q["qid"],
        "rank": metrics.rank_of(gold, ranked) if gold else None,
        # moments are only scored where the gold window is a real moment (Charades); a
        # FLEURS clip is one utterance, so its "window" is the whole clip
        "moment": (metrics.moment_hit(gold, q.get("window"), ranked[0] if ranked else None, jump_ms)
                   if q["type"] == "charades_sentence" else None),
        "returned": len(ranked),
    }


def summarise(qs, rows, with_moments: bool) -> dict:
    by = {q["qid"]: q for q in qs}
    out = {}
    for split in ("test", "dev"):
        sel = [r for r in rows if by[r["qid"]]["split"] == split]
        ans = [r for r in sel if by[r["qid"]]["type"] != "no_answer"]
        noans = [r for r in sel if by[r["qid"]]["type"] == "no_answer"]
        groups = {"all": (ans, noans)}
        for t in TYPES:
            groups[t] = ([r for r in ans if by[r["qid"]]["type"] == t], [])
        for t in ("fleurs_sentence", "fleurs_keyword"):
            for lang in LANGS:
                groups[f"{t}:{lang}"] = ([r for r in ans if by[r["qid"]]["type"] == t
                                          and by[r["qid"]].get("lang") == lang], [])
        res = {}
        for name, (a, n) in groups.items():
            if not with_moments:
                a = [{**r, "moment": None} for r in a]
            res[name] = metrics.summary(a, n)
        out[split] = res
    return out


def live_rows(qs, system):
    path = LIVE / f"{system}.jsonl"
    if not path.exists():
        return None
    saved = {r["qid"]: r for r in read_jsonl(path)}
    rows = []
    for q in qs:
        s = saved[q["qid"]]
        best = s.get("best_ms") or []
        rows.append(row(q, s["ranked"], best[0] if best else None))
    return rows


def latency(system):
    path = LIVE / f"{system}_latency.jsonl"
    if not path.exists():
        return None
    ms = [r["ms"] for r in read_jsonl(path)]
    return {"p50_ms": metrics.percentile(ms, 50), "p95_ms": metrics.percentile(ms, 95), "n": len(ms)}


def indexing():
    try:
        videos = json.loads((DATA / "indexing.json").read_text())
        wall = json.loads((DATA / "indexing_wall.json").read_text())
    except FileNotFoundError:
        return None
    done = [v for v in videos if v["status"] == "done"]
    video_s = sum(v["duration"] or 0 for v in done)
    took = sum(v["took"] or 0 for v in done)
    # Per-minute rates from each video's own processing time: the wall time also covers
    # restarts while Whisper was being moved off the CPU, so it overstates the real cost.
    rates = [v["took"] / (v["duration"] / 60) for v in done if v["took"] and v["duration"]]
    return {"videos": len(videos), "done": len(done), "failed": len(videos) - len(done),
            "video_hours": video_s / 3600, "processing_seconds": took,
            "seconds_per_video_minute": took / (video_s / 60) if video_s else None,
            "median_seconds_per_video": metrics.percentile([v["took"] for v in done if v["took"]], 50),
            "median_seconds_per_video_minute": metrics.percentile(rates, 50),
            "wall_seconds": wall["wall_seconds"]}


def main():
    qs = queries()
    cands = {r["qid"]: r for r in read_jsonl(DATA / "candidates.jsonl")}
    assets = json.loads((DATA / "assets.json").read_text())
    immich_after = {r["qid"]: r["ranked"] for r in read_jsonl(LIVE / "immich_after.jsonl")}

    systems, simulated = {}, {}
    for name in ("immich_before", "immich_after"):
        rows = live_rows(qs, name)
        if rows:
            systems[name] = {"summary": summarise(qs, rows, False), "latency": latency(name), "source": "live"}
    for name in ("vsearch", "proxy"):
        rows = live_rows(qs, name)
        if rows:
            systems[f"{name} (live)"] = {"summary": summarise(qs, rows, name == "vsearch"),
                                         "latency": latency(name), "source": "live"}

    for name, (params, mode, desc) in configs().items():
        rows, proxy = evaluate(qs, cands, assets, params, mode, immich_after)
        systems[name] = {"summary": summarise(qs, rows, True), "source": "offline", "lexical": mode,
                         "params": _params(params), "description": desc}
        if not name.startswith("ablation"):
            simulated[name] = {"summary": summarise(qs, proxy, False), "source": "offline (blend)"}

    out = {
        "commit": git("rev-parse", "--short", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--", "vsearch", "eval/*.py")),
        "date": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "datasets": json.loads((EVAL / "datasets.json").read_text()),
        "n_queries": len(qs),
        "systems": systems,
        "proxy_simulated": simulated,
        "consistency": consistency(qs, cands, assets),
        "indexing": indexing(),
    }
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{datetime.date.today().isoformat()}-{out['commit']}.json"
    path.write_text(json.dumps(out, indent=1))
    import report
    (EVAL / "results.md").write_text(report.markdown(out, path.name))
    print(f"wrote {path.relative_to(ROOT)} and eval/results.md")


def consistency(qs, cands, assets):
    """The offline re-ranking must reproduce the live search exactly, or the fix numbers
    mean nothing: share of queries whose top-10 matches the live API's."""
    path = LIVE / "vsearch.jsonl"
    if not path.exists():
        return None
    live = {r["qid"]: r["ranked"][:10] for r in read_jsonl(path)}
    params, mode, _ = configs()["vsearch"]
    same = 0
    for q in qs:
        results = ranking.rank(load_hits(cands[q["qid"]], mode), clean(q["query"]), params, 40)
        same += [assets[r["assetId"]] for r in results][:10] == live[q["qid"]]
    return {"top10_identical": same / len(qs), "n": len(qs)}


def _params(p: Params) -> dict:
    d = dict(p.__dict__)
    d["kinds"] = list(d["kinds"])
    d["calibration"] = {k: list(v) for k, v in d["calibration"].items()}
    return d


if __name__ == "__main__":
    main()
