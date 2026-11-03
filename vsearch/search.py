"""Query side: finds the moments inside a user's videos that match a search, by what's on
screen (CLIP frames), what's said (transcripts), and what's written (OCR)."""
import logging
from bisect import bisect_right

from . import db

log = logging.getLogger("vsearch.search")

# Calibrated with test/calibrate.py. SigLIP2 image-text cosines: true matches 0.095-0.145,
# non-matches <= 0.06. bge-m3 against transcripts + English translations: true paraphrases
# >= 0.607, unrelated sentences <= 0.590 ("I am going to the office" vs "let's go to the park").
VISUAL_FLOOR = 0.07      # below this a frame doesn't match at all
VISUAL_SPREAD = 0.06     # keep videos within this much of the best match
SPEECH_FLOOR = 0.60
# 1-2 word queries embed vaguely ("a cat" scored 0.606 against a dog sentence); every true
# short query in the tests was an exact word in the transcript/translation anyway.
SPEECH_FLOOR_SHORT = 0.65
SPEECH_SPREAD = 0.15
MOMENT_GAP_MS = 2500     # moments closer than this collapse into one
MAX_MOMENTS = 6

KINDS = ("visual", "speech", "text")

OWNED = """a.id = {col} and a."ownerId" = %(owner)s and a."deletedAt" is null
           and not a."isOffline" and a.visibility in ('timeline', 'archive')"""


def _like(query: str) -> str:
    return "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def run(pool, ml, embedder, owner_id: str, query: str, kinds=KINDS, limit: int = 40) -> list[dict]:
    query = " ".join(query.split())[:200]
    if not query:
        return []
    params = {"owner": owner_id, "q": query, "like": _like(query)}
    hits: list[tuple] = []
    with pool.connection() as conn:
        if "visual" in kinds:
            hits += _visual(conn, ml, db.immich_config(conn), params)
        if "speech" in kinds:
            hits += _speech(conn, embedder, params)
        if "text" in kinds:
            hits += _text(conn, params)
        results = _group(hits, limit)
        _decorate(conn, results)
    return results


def _visual(conn, ml, cfg, params) -> list[tuple]:
    vector = ml.text(params["q"], cfg.clip_model)
    try:
        with conn.transaction():
            conn.execute("set local hnsw.ef_search = 400")
            conn.execute("set local hnsw.iterative_scan = relaxed_order")
            rows = conn.execute(f"""
                select f.asset_id, f.ms, 1 - (f.embedding <=> %(v)s) as sim
                from vsearch.frame f join asset a on {OWNED.format(col='f.asset_id')}
                order by f.embedding <=> %(v)s
                limit 400
            """, {**params, "v": vector}).fetchall()
    except Exception as e:  # e.g. Immich's model changed and frames aren't re-indexed yet
        log.warning("visual search unavailable: %s", e)
        return []
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


def _said(text: str, translated: bool) -> str:
    return f"EN: {text}" if translated else text


def _speech(conn, embedder, params) -> list[tuple]:
    hits = []
    seen = set()
    owned = OWNED.format(col="s.asset_id")
    # The exact words first (in the transcript or its English translation), then the meaning.
    # bge-m3 also covers paraphrases; trigram fuzziness matched "a cat" to "a wish ... candles".
    for asset_id, ms, text, translated in conn.execute(f"""
        select s.asset_id, s.start_ms, s.text, s.translated from vsearch.segment s join asset a on {owned}
        where s.text ilike %(like)s limit 200
    """, params):
        seen.add((asset_id, ms))
        hits.append((asset_id, ms, "speech", 1.0, _said(text, translated)))
    vector = embedder.embed([params["q"]])[0]
    rows = conn.execute(f"""
        select s.asset_id, s.start_ms, s.text, s.translated, 1 - (s.embedding <=> %(v)s) as sim
        from vsearch.segment s join asset a on {owned}
        order by s.embedding <=> %(v)s
        limit 100
    """, {**params, "v": vector}).fetchall()
    if rows:
        floor = SPEECH_FLOOR if len(params["q"].split()) > 2 else SPEECH_FLOOR_SHORT
        top = max(r[4] for r in rows)
        cut = max(floor, top - SPEECH_SPREAD)
        for asset_id, ms, text, translated, sim in rows:
            if sim >= cut and (asset_id, ms) not in seen:
                score = 0.4 + 0.45 * (sim - cut) / max(top - cut, 1e-6)
                hits.append((asset_id, ms, "speech", score, _said(text, translated)))
    return hits


def _text(conn, params) -> list[tuple]:
    owned = OWNED.format(col="f.asset_id")
    # OCR often merges or splits words ("GOA2023"), so exact matching ignores spaces.
    squashed = {**params, "squashed": _like("".join(params["q"].split()))}
    exact = """(f.ocr ilike %(like)s or replace(f.ocr, ' ', '') ilike %(squashed)s)"""
    hits = [(asset_id, ms, "text", 1.0, ocr) for asset_id, ms, ocr in conn.execute(f"""
        select f.asset_id, f.ms, f.ocr from vsearch.frame f join asset a on {owned}
        where {exact} limit 300
    """, squashed)]
    # Misread letters ("DIWAL1"): strict word similarity, only for queries long enough
    # that trigrams mean something.
    if len("".join(params["q"].split())) >= 5:
        hits += [(asset_id, ms, "text", 0.8, ocr) for asset_id, ms, ocr in conn.execute(f"""
            select f.asset_id, f.ms, f.ocr from vsearch.frame f join asset a on {owned}
            where %(q)s <<%% f.ocr and not {exact} limit 300
        """, squashed)]
    return hits


def _group(hits: list[tuple], limit: int) -> list[dict]:
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


def _decorate(conn, results: list[dict]) -> None:
    if not results:
        return
    ids = [r["assetId"] for r in results]
    meta = {str(row[0]): row for row in conn.execute("""
        select a.id, a."originalFileName", a."localDateTime", v.duration
        from asset a left join vsearch.video v on v.asset_id = a.id
        where a.id = any(%s::uuid[])
    """, (ids,))}
    frames = {str(asset_id): ms_list for asset_id, ms_list in conn.execute(
        "select asset_id, array_agg(ms order by ms) from vsearch.frame where asset_id = any(%s::uuid[]) group by asset_id",
        (ids,))}
    for r in results:
        _, name, taken, duration = meta[r["assetId"]]
        r.update(fileName=name, takenAt=taken.isoformat() if taken else None, duration=duration)
        ms_list = frames.get(r["assetId"], [])
        for m in r["moments"]:
            # Thumbnail: the last sampled frame at or just after the moment starts.
            i = bisect_right(ms_list, m["ms"] + 500)
            m["frameMs"] = ms_list[max(i - 1, 0)] if ms_list else None
