"""Paths and file helpers for the eval runner, metrics and live scripts (stdlib only)."""
import json
import random
import subprocess
from pathlib import Path

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
DATA = EVAL / "data"
LIVE = DATA / "live"
RESULTS = EVAL / "results"
SEED = 2026


def read_jsonl(path: Path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def queries() -> list[dict]:
    return read_jsonl(DATA / "queries.jsonl")


def latency_sample(qs: list[dict], n: int = 200) -> list[dict]:
    """A fixed sample for timing: latency is measured one query at a time, which is too
    slow to do for every query against every system."""
    rng = random.Random(SEED)
    return sorted(rng.sample(qs, min(n, len(qs))), key=lambda q: q["qid"])


def git(*args) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout.strip()
