"""Query side: finds the moments inside a user's videos that match a search, by what's on
screen (CLIP frames), what's said (transcripts), and what's written (OCR).

This module only fetches candidates (SQL); thresholds, scoring and fusion live in
ranking.py, so the offline evaluation in eval/ ranks exactly what the live search ranks."""
import logging
from bisect import bisect_right

from . import db, lexical, ranking
from .ranking import Hit

log = logging.getLogger("vsearch.search")

KINDS = ("visual", "speech", "text")

OWNED = """a.id = {col} and a."ownerId" = %(owner)s and a."deletedAt" is null
           and not a."isOffline" and a.visibility in ('timeline', 'archive')"""

# How "the exact words" are matched: "substr" is the original ILIKE '%query%', "word" is
# lexical.py's word-boundary phrase match.
LEXICAL = "substr"


def _like(query: str) -> str:
    return "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def clean(query: str) -> str:
    return " ".join(query.split())[:200]


def run(pool, ml, embedder, owner_id: str, query: str, kinds=KINDS, limit: int = 40,
        params: ranking.Params = ranking.DEFAULT, lexical_mode: str | None = None) -> list[dict]:
    query = clean(query)
    if not query:
        return []
    params = ranking.with_(params, kinds=tuple(k for k in params.kinds if k in kinds))
    with pool.connection() as conn:
        hits = candidates(conn, ml, embedder, owner_id, query, params.kinds, lexical_mode or LEXICAL)
        results = ranking.rank(hits, query, params, limit)
        _decorate(conn, results)
    return results


def candidates(conn, ml, embedder, owner_id: str, query: str, kinds=KINDS, mode: str = LEXICAL) -> list[Hit]:
    hits: list[Hit] = []
    if "visual" in kinds:
        hits += visual_hits(conn, ml, owner_id, query)
    if "speech" in kinds:
        hits += speech_exact_hits(conn, owner_id, query, mode)
        hits += speech_vec_hits(conn, embedder, owner_id, query)
    if "text" in kinds:
        hits += text_hits(conn, owner_id, query, mode)
    return hits


def visual_hits(conn, ml, owner_id: str, query: str) -> list[Hit]:
    vector = ml.text(query, db.immich_config(conn).clip_model)
    try:
        with conn.transaction():
            conn.execute("set local hnsw.ef_search = 400")
            conn.execute("set local hnsw.iterative_scan = relaxed_order")
            rows = conn.execute(f"""
                select f.asset_id, f.ms, 1 - (f.embedding <=> %(v)s) as sim
                from vsearch.frame f join asset a on {OWNED.format(col='f.asset_id')}
                order by f.embedding <=> %(v)s
                limit 400
            """, {"owner": owner_id, "v": vector}).fetchall()
    except Exception as e:  # e.g. Immich's model changed and frames aren't re-indexed yet
        log.warning("visual search unavailable: %s", e)
        return []
    return [Hit(str(a), ms, "visual", "vec", float(sim)) for a, ms, sim in rows]


def speech_exact_hits(conn, owner_id: str, query: str, mode: str = LEXICAL) -> list[Hit]:
    """The query's words in the transcript or its English translation."""
    if mode == "word":
        rx = lexical.pattern(query)
        if rx is None:
            return []
        where, args = "s.text ~* %(rx)s", {"rx": rx}
    else:
        where, args = "s.text ilike %(like)s", {"like": _like(query)}
    rows = conn.execute(f"""
        select s.asset_id, s.start_ms, s.text, s.translated from vsearch.segment s
        join asset a on {OWNED.format(col='s.asset_id')}
        where {where}
        order by s.asset_id, s.translated, s.start_ms
        limit 200
    """, {"owner": owner_id, **args}).fetchall()
    return [Hit(str(a), ms, "speech", "exact", 1.0, text, translated) for a, ms, text, translated in rows]


def speech_vec_hits(conn, embedder, owner_id: str, query: str) -> list[Hit]:
    """By meaning (bge-m3): paraphrases, and other languages."""
    vector = embedder.embed([query])[0]
    rows = conn.execute(f"""
        select s.asset_id, s.start_ms, s.text, s.translated, 1 - (s.embedding <=> %(v)s) as sim
        from vsearch.segment s join asset a on {OWNED.format(col='s.asset_id')}
        order by s.embedding <=> %(v)s
        limit 100
    """, {"owner": owner_id, "v": vector}).fetchall()
    return [Hit(str(a), ms, "speech", "vec", float(sim), text, translated) for a, ms, text, translated, sim in rows]


def text_hits(conn, owner_id: str, query: str, mode: str = LEXICAL) -> list[Hit]:
    owned = OWNED.format(col="f.asset_id")
    if mode == "word":
        rx = lexical.pattern(query, ocr=True)
        exact, args = ("f.ocr ~* %(rx)s", {"rx": rx}) if rx else ("false", {})
    else:
        # OCR often merges or splits words ("GOA2023"), so exact matching ignores spaces.
        exact = "(f.ocr ilike %(like)s or replace(f.ocr, ' ', '') ilike %(squashed)s)"
        args = {"like": _like(query), "squashed": _like("".join(query.split()))}
    hits = [Hit(str(a), ms, "text", "exact", 1.0, ocr) for a, ms, ocr in conn.execute(f"""
        select f.asset_id, f.ms, f.ocr from vsearch.frame f join asset a on {owned}
        where {exact}
        order by f.asset_id, f.ms
        limit 300
    """, {"owner": owner_id, **args})]
    # Misread letters ("DIWAL1"): strict word similarity, only for queries long enough
    # that trigrams mean something.
    if len("".join(query.split())) >= 5:
        hits += [Hit(str(a), ms, "text", "fuzzy", float(sim), ocr) for a, ms, ocr, sim in conn.execute(f"""
            select f.asset_id, f.ms, f.ocr, strict_word_similarity(%(q)s, f.ocr) from vsearch.frame f
            join asset a on {owned}
            where %(q)s <<%% f.ocr and not {exact}
            order by f.asset_id, f.ms
            limit 300
        """, {"owner": owner_id, "q": query, **args})]
    return hits


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
