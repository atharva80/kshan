"""Raw search candidates for every benchmark query, before any threshold or fusion.

Runs INSIDE the test stack's vsearch container (the database, Immich's ML service and
bge-m3 are all there):

  docker compose -f test/docker-compose.vtest.yml exec -T -e PYTHONPATH=/app vtest-vsearch \
      python /eval/collect.py

It uses search.py's own candidate functions, so eval/run.py can re-rank the exact rows the
live search sees under any thresholds / fusion. Both lexical matchers ("substr", the
original ILIKE, and "word", whole-word) are fetched, for Fix A's before/after.

Writes eval/data/candidates.jsonl, assets.json (id -> file name) and indexing.json."""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, "/app")
from vsearch import db, search  # noqa: E402
from vsearch.services import services  # noqa: E402

DATA = Path("/eval/data")


class Precomputed:
    """Stands in for the ML client and text embedder with vectors computed up front in
    batches (one query at a time through bge-m3 would dominate the run)."""

    def __init__(self, clip: dict, text: dict):
        self.clip, self.textvec = clip, text

    def text(self, query, _model):
        return self.clip[query]

    def embed(self, texts):
        return [self.textvec[texts[0]]]


def compact(hits):
    return [[h.asset, h.ms, round(h.sim, 5), int(h.translated)] for h in hits]


def main(queries_file="queries.jsonl", out_file="candidates.jsonl"):
    qs = [json.loads(line) for line in open(DATA / queries_file)]
    texts = sorted({search.clean(q["query"]) for q in qs})
    with db.connect() as conn:
        owner = str(conn.execute('select id from "user" order by "createdAt" limit 1').fetchone()[0])
        model = db.immich_config(conn).clip_model
        names = {str(a): n for a, n in conn.execute('select id, "originalFileName" from asset')}
        videos = [dict(zip(("name", "status", "duration", "took", "frames", "segments", "language"), row))
                  for row in conn.execute("""
                      select a."originalFileName", v.status, v.duration, v.took_seconds, v.frame_count,
                             v.segment_count, v.language
                      from vsearch.video v join asset a on a.id = v.asset_id""")]
    (DATA / "assets.json").write_text(json.dumps(names))
    (DATA / "indexing.json").write_text(json.dumps(videos))

    t = time.time()
    with ThreadPoolExecutor(8) as pool:
        clip = dict(zip(texts, pool.map(lambda q: services.ml.text(q, model), texts)))
    textvec = dict(zip(texts, services.embedder.embed(texts)))
    print(f"embedded {len(texts)} queries in {time.time() - t:.0f}s", flush=True)
    vec = Precomputed(clip, textvec)

    def one(q):
        query = search.clean(q["query"])
        with db.connect() as conn:
            row = {"qid": q["qid"],
                   "visual": compact(search.visual_hits(conn, vec, owner, query)),
                   "speech_vec": compact(search.speech_vec_hits(conn, vec, owner, query))}
            for mode in ("substr", "word"):
                row[f"speech_exact_{mode}"] = compact(search.speech_exact_hits(conn, owner, query, mode))
                text = search.text_hits(conn, owner, query, mode)
                row[f"text_exact_{mode}"] = compact([h for h in text if h.method == "exact"])
                row[f"text_fuzzy_{mode}"] = compact([h for h in text if h.method == "fuzzy"])
        return row

    t = time.time()
    with ThreadPoolExecutor(6) as pool, open(DATA / out_file, "w") as out:
        for i, row in enumerate(pool.map(one, qs), 1):
            out.write(json.dumps(row) + "\n")
            if i % 250 == 0:
                print(f"  {i}/{len(qs)}", flush=True)
    print(f"collected {len(qs)} queries in {time.time() - t:.0f}s")


if __name__ == "__main__":
    main(*sys.argv[1:3])  # optional: a query subset and an output name, for smoke tests
