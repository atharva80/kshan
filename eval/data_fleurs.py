"""FLEURS test split: Hindi/Marathi/English clips wrapped as grey-frame mp4s.

Every FLEURS utterance carries a FLoRes sentence id shared across languages, so the English
sentence for id N is the reference translation of the Hindi/Marathi utterance with the same id.
"""
import csv
import random
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor

from common import CLIPS, DATA, RAW, SEED, download, duration, ffmpeg_version, http_json, sha256_file, write_json

REPO = "google/fleurs"
# Pinned revision: file contents (and so every hash below) are tied to this commit.
REVISION = "70bb2e84b976b7e960aa89f1c648e09c59f894dd"
LANGS = ("hi_in", "mr_in", "en_us")
MAX_PER_LANG = 200
JOBS = 8
BASE = f"https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/"
FFMPEG = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=1",
          "-i", "{wav}", "-shortest", "-c:v", "libx264", "-tune", "stillimage", "-c:a", "aac", "{out}"]


def read_tsv(path):
    """-> rows [id, file, raw_transcription, transcription, characters, num_samples, gender].

    The files have no header row; assert that instead of assuming it."""
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE))
    assert all(len(r) == 7 and r[0].isdigit() for r in rows), f"{path}: unexpected layout/header"
    return rows


def fetch_sources():
    """Download tsv + audio archive per language; verify archives against the hub's LFS sha256."""
    info = http_json(f"https://huggingface.co/api/datasets/{REPO}/revision/{REVISION}?blobs=true")
    lfs = {s["rfilename"]: s["lfs"]["sha256"] for s in info["siblings"] if s.get("lfs")}
    sources = {"license": "CC BY 4.0", "repo": f"https://huggingface.co/datasets/{REPO}",
               "revision": REVISION, "files": {}}
    for lang in LANGS:
        for rel in (f"data/{lang}/test.tsv", f"data/{lang}/audio/test.tar.gz"):
            dest = download(BASE + rel, RAW / "fleurs" / rel)
            sha = sha256_file(dest)
            if rel in lfs and sha != lfs[rel]:
                sys.exit(f"{rel}: sha256 mismatch against the hub ({sha} vs {lfs[rel]})")
            sources["files"][rel] = {"url": BASE + rel, "sha256": sha, "bytes": dest.stat().st_size}
    return sources


def first_recording(rows):
    """One recording per sentence id: the first in tsv order (later ones are other speakers)."""
    first = {}
    for r in rows:
        first.setdefault(r[0], r)
    return first


def extract_wavs(lang, wanted):
    """Stream the tar.gz once, keeping only the wanted wav files."""
    out = RAW / "fleurs" / "wav" / lang
    out.mkdir(parents=True, exist_ok=True)
    missing = {w for w in wanted if not (out / w).exists()}
    if not missing:
        return out
    with tarfile.open(RAW / "fleurs" / "data" / lang / "audio" / "test.tar.gz", "r:gz") as tar:
        for m in tar:
            name = m.name.rsplit("/", 1)[-1]
            if m.isfile() and name in missing:
                (out / name).write_bytes(tar.extractfile(m).read())
                missing.discard(name)
                if not missing:
                    break
    if missing:
        sys.exit(f"{lang}: {len(missing)} wavs not found in archive")
    return out


def wrap(job):
    wav, out = job
    if out.exists():
        return
    tmp = out.with_name("partial_" + out.name)  # keeps the .mp4 suffix so ffmpeg picks the muxer
    cmd = [a.format(wav=wav, out=tmp) for a in FFMPEG]
    subprocess.run(cmd, check=True)
    tmp.rename(out)


def main():
    sources = fetch_sources()
    tsv = {lang: read_tsv(RAW / "fleurs" / "data" / lang / "test.tsv") for lang in LANGS}
    first = {lang: first_recording(rows) for lang, rows in tsv.items()}

    common_ids = set.intersection(*(set(first[lang]) for lang in LANGS))
    ids = sorted(common_ids, key=int)  # sort first so the shuffle is order-independent
    random.Random(SEED).shuffle(ids)
    # Equal, disjoint thirds. The common pool is small (see datasets.json), so the 200 cap rarely binds.
    per_lang = min(MAX_PER_LANG, len(ids) // len(LANGS))
    chosen = {lang: ids[i * per_lang:(i + 1) * per_lang] for i, lang in enumerate(LANGS)}
    assert len(set().union(*map(set, chosen.values()))) == per_lang * len(LANGS), "language sets overlap"
    print(f"ids in all 3 languages: {len(common_ids)}; {per_lang} per language")

    out_dir = CLIPS / "fleurs"
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for lang in LANGS:
        wavs = extract_wavs(lang, {first[lang][i][1] for i in chosen[lang]})
        jobs += [(wavs / first[lang][i][1], out_dir / f"{lang}_{i}.mp4") for i in chosen[lang]]
    todo = [j for j in jobs if not j[1].exists()]
    print(f"wrapping {len(todo)} clips with ffmpeg ({JOBS} parallel)")
    with ThreadPoolExecutor(JOBS) as ex:
        list(ex.map(wrap, jobs))

    clips = []
    for lang in LANGS:
        for i in chosen[lang]:
            p = out_dir / f"{lang}_{i}.mp4"
            r = first[lang][i]
            clips.append({"lang": lang, "sentence_id": i, "wav": r[1], "duration": duration(p),
                          "sha256": sha256_file(p),
                          # raw_transcription keeps punctuation/casing; it is what a reader would expect to see
                          "native": r[2], "english": first["en_us"][i][2]})
    write_json(DATA / "fleurs_meta.json", {
        "sources": sources, "seed": SEED, "ffmpeg": ffmpeg_version(),
        "ffmpeg_cmd": " ".join(FFMPEG),
        "ids_in_all_languages": len(common_ids),
        "n_per_lang": per_lang, "clips": clips})
    print(f"wrote {len(clips)} clips")


if __name__ == "__main__":
    main()
