"""Charades-STA test moments: fetch annotations, pick 300 videos, range-request their clips.

Run with:  uv run --with remotezip python eval/data_charades.py
"""
import hashlib
import random
import re
import shutil
import sys
import urllib.request

from remotezip import RemoteZip

from common import CLIPS, DATA, RAW, SEED, download, duration, sha256_file, write_json

N_VIDEOS = 300

# Pinned to a commit so the annotation bytes cannot drift. A second, independent mirror is
# fetched only to cross-check the hash.
ANN_COMMIT = "e0e7a83ff991e74e07d67d9bcc1be94b1767e9a9"
ANN_BASE = f"https://raw.githubusercontent.com/Soldelli/2D-TAN/{ANN_COMMIT}/data/Charades-STA/"
MIRROR_BASE = "https://raw.githubusercontent.com/Alvin-Zeng/DRN/master/data/dataset/Charades/"
ZIP_URL = "https://ai2-public-datasets.s3-us-west-2.amazonaws.com/charades/Charades_v1_480.zip"

LINE = re.compile(r"^(\w+) (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)##(.+)$")


def parse(path, strict_windows=True):
    """-> list of (video_id, start, end, sentence). Fails loudly on any malformed line.

    The train file has a few inverted windows (start > end); they only matter for the test file."""
    rows = []
    for n, line in enumerate(open(path, encoding="utf-8").read().splitlines()):
        m = LINE.match(line)
        if not m or (strict_windows and float(m[2]) >= float(m[3])):
            sys.exit(f"{path}:{n + 1}: unexpected format: {line!r}")
        rows.append((m[1], float(m[2]), float(m[3]), m[4]))
    return rows


def fetch_annotations():
    sources = {}
    for split, mirror_name in (("test", "Charades_sta_test.txt"), ("train", "Charades_sta_train.txt")):
        name = f"charades_sta_{split}.txt"
        dest = download(ANN_BASE + name, RAW / "charades" / name)
        with urllib.request.urlopen(MIRROR_BASE + mirror_name) as r:
            mirror_sha = hashlib.sha256(r.read()).hexdigest()
        sha = sha256_file(dest)
        if sha != mirror_sha:
            sys.exit(f"{name}: mirrors disagree ({sha} vs {mirror_sha})")
        sources[name] = {"url": ANN_BASE + name, "sha256": sha, "queries": len(parse(dest, strict_windows=split == "test")),
                         "cross_checked_against": MIRROR_BASE + mirror_name,
                         "used_for": "selection and queries" if split == "test" else "vocabulary checks only"}
    return sources


def zip_identity():
    """The archive is too big to hash; record the S3 identity headers instead."""
    req = urllib.request.Request(ZIP_URL, method="HEAD")
    with urllib.request.urlopen(req) as r:
        return {k: r.headers[k] for k in ("ETag", "Content-Length", "Last-Modified")}


def extract_clips(video_ids):
    """Pull only the chosen mp4s out of the 16 GB archive via HTTP range requests."""
    out = CLIPS / "charades"
    out.mkdir(parents=True, exist_ok=True)
    todo = [v for v in video_ids if not (out / f"{v}.mp4").exists()]
    if not todo:
        return
    print(f"extracting {len(todo)} clips from remote zip")
    with RemoteZip(ZIP_URL) as z:
        for i, vid in enumerate(todo, 1):
            dest = out / f"{vid}.mp4"
            part = dest.with_name(dest.name + ".part")
            with z.open(f"Charades_v1_480/{vid}.mp4") as src, open(part, "wb") as dst:
                shutil.copyfileobj(src, dst)
            part.rename(dest)
            if i % 25 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}")


def main():
    sources = fetch_annotations()
    rows = parse(RAW / "charades" / "charades_sta_test.txt")
    all_ids = sorted({r[0] for r in rows})
    # Sort before sampling so the choice depends only on the set of ids, not file order.
    video_ids = sorted(random.Random(SEED).sample(all_ids, N_VIDEOS))
    print(f"test file: {len(rows)} queries over {len(all_ids)} videos; picked {len(video_ids)}")

    extract_clips(video_ids)

    clips = []
    for vid in video_ids:
        p = CLIPS / "charades" / f"{vid}.mp4"
        clips.append({"video_id": vid, "duration": duration(p), "bytes": p.stat().st_size,
                      "sha256": sha256_file(p)})
    write_json(DATA / "charades_meta.json", {
        "annotations": sources,
        "videos": {"url": ZIP_URL, "s3_headers": zip_identity(),
                   "access": "HTTP range requests via remotezip (no full download)",
                   "member_pattern": "Charades_v1_480/<VIDEO_ID>.mp4"},
        "seed": SEED, "n_videos": N_VIDEOS, "n_test_videos_available": len(all_ids),
        "clips": clips})
    print(f"wrote {len(clips)} clips, {sum(c['bytes'] for c in clips) / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
