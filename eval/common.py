"""Shared paths and helpers for the eval data scripts (stdlib only)."""
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path

SEED = 2026
EVAL = Path(__file__).resolve().parent
DATA = EVAL / "data"
RAW = DATA / "raw"  # downloaded annotation files / archives
CLIPS = DATA / "clips"  # clips that end up in the manifest


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(url, dest):
    """Fetch url to dest unless it exists. Writes via .part so a killed run never leaves a truncated file."""
    dest = Path(dest)
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    print(f"  downloading {url}")
    with urllib.request.urlopen(url) as r, open(part, "wb") as f:
        while block := r.read(1 << 20):
            f.write(block)
    part.rename(dest)
    return dest


def http_json(url):
    with urllib.request.urlopen(url) as r:
        return json.load(r)


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True).stdout
    return round(float(out), 3)


def ffmpeg_version():
    return subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.split("\n")[0]


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
