"""Loads the benchmark into the throwaway test Immich (test/docker-compose.vtest.yml).

  python3 eval/load.py upload    # test admin + config, upload every clip and the 35 distractor photos
  python3 eval/load.py index     # start the indexer, wait for it, record how long it took

Never points at the real Immich: everything goes to 127.0.0.1:2293 / :2294."""
import json
import subprocess
import sys
import time

from store import DATA, ROOT

sys.path.insert(0, str(ROOT / "test"))
import e2e  # noqa: E402  (reuses the e2e harness' API helpers)

COMPOSE = ["docker", "compose", "-f", str(ROOT / "test" / "docker-compose.vtest.yml")]


def bench_config(config: dict) -> None:
    """Jobs the benchmark doesn't need, off in the TEST Immich only: transcoding ~900 clips
    and face/duplicate detection would roughly double the load time and VRAM, and none of
    them affect smart search, OCR or the files vsearch reads (the originals)."""
    config["ffmpeg"]["transcode"] = "disabled"
    config["machineLearning"]["facialRecognition"]["enabled"] = False
    config["machineLearning"]["duplicateDetection"]["enabled"] = False


def upload():
    e2e.ensure_admin(bench_config)
    started = time.time()
    e2e.upload(DATA / "clips", ROOT / "test" / "library" / "distractors", timeout=4 * 3600)
    names = e2e.all_assets()
    print(f"uploaded and processed by Immich in {time.time() - started:.0f}s: {len(names)} assets")


def index():
    started = time.time()
    subprocess.run(COMPOSE + ["--profile", "indexer", "up", "-d"], check=True)
    status = e2e.wait_indexed(timeout=6 * 3600)
    took = time.time() - started
    print(f"indexed in {took:.0f}s:", status)
    (DATA / "indexing_wall.json").write_text(json.dumps({"wall_seconds": took, "status": status,
                                                         "finished": time.strftime("%Y-%m-%dT%H:%M:%S%z")}))


if __name__ == "__main__":
    {"upload": upload, "index": index}[sys.argv[1]]()
