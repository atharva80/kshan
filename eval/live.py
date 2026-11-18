"""Asks the running systems every query and saves what each one returns.

  python3 eval/live.py immich_before   # plain Immich, before the indexer has written anything
  python3 eval/live.py immich_after    # plain Immich after vsearch's write-back (the phone app as-is)
  python3 eval/live.py vsearch         # the companion search page's API
  python3 eval/live.py proxy           # Immich's search bar routed through vsearch (POST /hook/smart-search)
  python3 eval/live.py vsearch_fixed   # vsearch / proxy again, after the fixes became the defaults

Rankings run 4 queries at a time; latency is then timed separately, one query at a time,
on a fixed sample (store.latency_sample), so concurrency doesn't distort it."""
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from store import LIVE, ROOT, latency_sample, queries, write_jsonl

sys.path.insert(0, str(ROOT / "test"))
import e2e  # noqa: E402

SIZE = 100


def ask(system: str, query: str) -> dict:
    system = system.removesuffix("_fixed")  # same endpoints, re-run after the fixes shipped
    if system in ("immich_before", "immich_after", "proxy"):
        url = f"{e2e.IMMICH}/search/smart" if system != "proxy" else "http://127.0.0.1:2294/hook/smart-search"
        status, body, _ = e2e.call("POST", url, {"query": query, "size": SIZE}, e2e.key_headers())
        assert status == 200, (system, query, status, str(body)[:200])
        items = body["assets"]["items"]
        return {"ranked": [a["originalFileName"] for a in items]}
    status, body, _ = e2e.call("GET", f"{e2e.VSEARCH}/api/search?q={urllib.parse.quote(query)}",
                               headers=e2e.key_headers())
    assert status == 200, (system, query, status, str(body)[:200])
    results = body["results"]
    return {"ranked": [r["fileName"] for r in results], "best_ms": [r["best"]["ms"] for r in results]}


def main(system: str):
    qs = queries()

    def one(q):
        return {"qid": q["qid"], **ask(system, q["query"])}

    started = time.time()
    with ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(one, qs))
    write_jsonl(LIVE / f"{system}.jsonl", rows)
    print(f"{system}: {len(rows)} queries in {time.time() - started:.0f}s")

    timed = []
    for q in latency_sample(qs):
        t = time.perf_counter()
        ask(system, q["query"])
        timed.append({"qid": q["qid"], "ms": (time.perf_counter() - t) * 1000})
    write_jsonl(LIVE / f"{system}_latency.jsonl", timed)
    ms = sorted(r["ms"] for r in timed)
    print(f"{system}: latency p50 {ms[len(ms) // 2]:.0f} ms, p95 {ms[int(len(ms) * 0.95)]:.0f} ms")


if __name__ == "__main__":
    main(sys.argv[1])
