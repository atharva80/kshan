"""Shrinks the benchmark to a seeded random subset so indexing fits in ~30 minutes on one
laptop GPU: 80 of the 300 Charades videos and 50 of the 88 FLEURS clips per language. All
no-answer queries stay. Each kept item keeps its dev/test split.

  python3 eval/subset.py          # rewrites eval/data/{manifest,queries}.jsonl (originals: *_full.jsonl),
                                  # deletes the dropped clips from the TEST Immich, filters immich_before

Plain Immich's smart search ranks every asset independently by its own similarity, so
dropping assets from a saved ranking is exactly what Immich returns without them; the
"before" baseline doesn't need re-running."""
import json
import random
import shutil
import sys

from store import DATA, LIVE, ROOT, SEED, read_jsonl, write_jsonl

sys.path.insert(0, str(ROOT / "test"))
import e2e  # noqa: E402

CHARADES_KEEP = 80
FLEURS_KEEP_PER_LANG = 50


def main():
    for name in ("manifest", "queries"):
        full = DATA / f"{name}_full.jsonl"
        if not full.exists():
            shutil.copy(DATA / f"{name}.jsonl", full)
    manifest = read_jsonl(DATA / "manifest_full.jsonl")
    qs = read_jsonl(DATA / "queries_full.jsonl")

    rng = random.Random(SEED)
    keep = set()
    charades = sorted(m["name"] for m in manifest if m["source"] == "charades")
    keep |= set(rng.sample(charades, CHARADES_KEEP))
    for lang in ("hi_in", "mr_in", "en_us"):
        clips = sorted(m["name"] for m in manifest if m["source"] == "fleurs" and m["lang"] == lang)
        keep |= set(rng.sample(clips, FLEURS_KEEP_PER_LANG))
    dropped = {m["name"] for m in manifest} - keep

    write_jsonl(DATA / "manifest.jsonl", [m for m in manifest if m["name"] in keep])
    kept_qs = [q for q in qs if q["gold"] is None or q["gold"] in keep]
    write_jsonl(DATA / "queries.jsonl", kept_qs)
    (DATA / "subset.json").write_text(json.dumps({
        "seed": SEED, "charades_keep": CHARADES_KEEP, "fleurs_keep_per_lang": FLEURS_KEEP_PER_LANG,
        "kept_clips": sorted(keep), "queries": len(kept_qs)}, indent=1))

    names = e2e.all_assets()
    ids = [names[n] for n in dropped if n in names]
    if ids:
        status, body, _ = e2e.call("DELETE", f"{e2e.IMMICH}/assets", {"ids": ids, "force": True}, e2e.key_headers())
        assert status in (200, 204), (status, body)
    print(f"kept {len(keep)} clips / {len(kept_qs)} queries; deleted {len(ids)} clips from the test Immich")

    before = LIVE / "immich_before.jsonl"
    full_before = LIVE / "immich_before_full.jsonl"
    if before.exists() and not full_before.exists():
        shutil.copy(before, full_before)
    if full_before.exists():
        kept_qids = {q["qid"] for q in kept_qs}
        write_jsonl(before, [{**r, "ranked": [n for n in r["ranked"] if n not in dropped]}
                             for r in read_jsonl(full_before) if r["qid"] in kept_qids])


if __name__ == "__main__":
    main()
