# Evaluation

How well vsearch finds the right video, and the right second in it, measured on public data
with a reproducible pipeline. Every number here comes from `eval/results/<date>-<commit>.json`,
written by `eval/run.py`; the tables are in [eval/results.md](../eval/results.md).

<!-- RESULTS -->

## The task

One library holds every benchmark clip plus 35 distractor photos, in one throwaway Immich
(`test/docker-compose.vtest.yml`). Each query is searched across the **whole library**, the way
someone uses the app, and the system returns a ranked list. That differs from the
moment-retrieval papers on Charades-STA, which give the model the right video and ask only
for the time span, so **these numbers are not comparable to published Charades-STA results**.

## Datasets

All sources, sha256 hashes, licences, selection rules and counts are recorded in
[eval/datasets.json](../eval/datasets.json). Nothing but scripts is committed.

| Source | What it tests | Licence | Used |
|---|---|---|---|
| [Charades](https://prior.allenai.org/projects/charades) videos + Charades-STA test annotations (TALL, Gao et al. 2017) | Finding what's **on screen**: indoor everyday-activity clips (~30 s) with sentence queries and start/end times | Charades: non-commercial use only. Clips are fetched one by one from AI2's archive with HTTP range requests (`eval/data_charades.py`), never redistributed. | 300 test-set videos picked with seed 2026 (all their queries); **80 of them indexed** (see below) |
| [FLEURS](https://huggingface.co/datasets/google/fleurs) (Conneau et al. 2022), `hi_in`, `mr_in`, `en_us` test splits | Finding what's **said**, in Hindi and Marathi, from **English** queries; English is the in-language control | CC BY 4.0 | Disjoint sets of FLoRes sentence ids per language; each recording wrapped in an mp4 with a plain grey frame so it goes through the real pipeline and gives the visual model and OCR nothing to match |
| 35 distractor photos (`test/make_dummy.py`, Wikimedia Commons crops) | Photos competing in the same search, as in a real library | CC licences, see `test/fetch_media.py` | all |

**Subset for indexing time.** To keep indexing near 30 minutes on one 8 GB laptop GPU,
`eval/subset.py` keeps a seeded random 80 of the 300 Charades videos and 50 of the 88 FLEURS
clips per language (seed 2026; every kept item keeps its split), plus all 100 no-answer
queries: 230 clips and 606 queries (452 test). `make eval-data` still builds the full set;
`eval/data/subset.json` lists what was kept.

## Queries

| Type | Built from | Gold |
|---|---|---|
| `charades_sentence` | Charades-STA sentences as given | the video and its time window |
| `fleurs_sentence` | the full English (en_us) sentence for the clip's FLoRes id | the clip |
| `fleurs_keyword` | 1–3 words picked by IDF over the selected FLEURS English sentences (not by an LLM); see `eval/build_queries.py` | the clip |
| `no_answer` | ~100 things absent from both datasets and the distractors; every content word was checked against the Charades-STA and FLEURS English vocabularies | none |

**Splits.** 30% dev / 70% test, seed 2026: by video for Charades (all of a video's queries
stay together) and by sentence id for FLEURS. Thresholds and fusion are chosen on dev only
(`eval/tune.py`, rules fixed before looking at test, see [eval/tuning.md](../eval/tuning.md));
every headline number is test.

## Metrics

- **R@k** (k = 1, 5, 10): the gold video is in the top k results. Ranks count every asset
  the system returns (Immich also returns photos).
- **MRR**: mean of 1/rank of the gold video (0 when it's not returned).
- **moment@1** (Charades only): the top result is the gold video **and** the player's jump
  time, the top result's best moment (`best.ms`, exactly what the companion page seeks to),
  falls inside the gold window with ±1 s tolerance:
  `start − 1 ≤ best.ms / 1000 ≤ end + 1`. A FLEURS clip is one utterance, so moments
  aren't scored there.
- **no-answer FP**: the share of no-answer queries for which the system returns anything.
  Plain Immich has no relevance threshold, so it always returns results.
- **Latency p50 / p95**: wall time of one HTTP request, one query at a time, on a fixed
  200-query sample (`eval/store.latency_sample`), from the host to the service on
  127.0.0.1 (includes Immich's auth check).
- **Indexing throughput**: seconds of indexer processing per minute of video
  (`vsearch.video.took_seconds` over `duration`), plus wall time to index the whole set.

## Systems

| Name | What it is |
|---|---|
| `immich_before` | plain Immich smart search before vsearch indexed anything (Immich embeds only a video's thumbnail) |
| `immich_after` | plain Immich after vsearch's write-back: what the unmodified phone app sees |
| `vsearch` | the companion search page (`/api/search`) |
| `proxy` | Immich's own search bar routed through vsearch (`/hook/smart-search`, `immich_search.blend`) |

`eval/collect.py` saves every query's raw candidates (all frame and segment similarities,
both lexical matchers) from inside the test container, using `search.py`'s own functions.
`eval/run.py` then re-ranks them with `ranking.py` under each configuration, so ablations
and fixes are compared on exactly the same candidates. The live and offline rankings are
checked against each other ("Consistency" in results.md).

## Reproduce

```bash
make eval-data                                    # ~15 min; downloads under eval/data/ (gitignored)
docker compose -f test/docker-compose.vtest.yml up -d
python3 eval/load.py upload                       # test admin, upload clips + distractors
python3 eval/live.py immich_before                # plain Immich BEFORE indexing
python3 eval/load.py index                        # start the indexer, wait, time it
python3 eval/live.py immich_after
python3 eval/live.py vsearch
python3 eval/live.py proxy
docker compose -f test/docker-compose.vtest.yml exec -T -e PYTHONPATH=/app vtest-vsearch python /eval/collect.py
uv run --with matplotlib python eval/tune.py      # dev split only
python3 eval/run.py                               # results JSON + eval/results.md
docker compose -f test/docker-compose.vtest.yml --profile indexer down -v
```

The test stack borrows the real stack's ML container over the `immich_default` network, so
`immich_machine_learning` must be running.

## Limitations

- **Charades is staged indoor video**, scripted by crowd workers in their homes. It isn't
  family video: no weddings, travel or festivals, and its sentences are generic ("person opens
  a door"), so many videos legitimately match a query that has one gold answer. That pulls
  Charades recall down for every system and makes it a strict test.
- **FLEURS is read speech**: one speaker, a clean microphone, Wikipedia-style sentences. Real
  family video has overlapping, code-mixed Hindi/Marathi/English conversation and noise, which
  will be harder for Whisper.
- **The keyword queries are mechanical** (highest-IDF words); people type differently.
- **One library size.** Results are for ~1,000 assets; larger libraries make ranking harder.
- **The original four functional-test clips** (README, `test/`) are text-to-speech and
  generated video; they check that features work, not search quality.
