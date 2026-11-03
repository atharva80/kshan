"""End-to-end test against the throwaway Immich in docker-compose.vtest.yml.

  python3 e2e.py setup <dummy library dir>   # test admin, settings, upload, baseline searches
  python3 e2e.py check                       # after the indexer ran: everything it should do

Stdlib only. Test credentials are generated here and kept in .vtest-credentials (mode 600).
"""
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

IMMICH = "http://127.0.0.1:2293/api"
VSEARCH = "http://127.0.0.1:2294"
HERE = Path(__file__).parent
CREDS = HERE / ".vtest-credentials"
BASELINE = HERE / ".vtest-baseline.json"
CLI_DIR = Path(os.environ.get("VTEST_CLI_DIR", "/tmp/vtest-immich-cli"))
CLIP_MODEL = "ViT-SO400M-16-SigLIP2-384__webli"

# query -> (file it should find, where in the video in ms, which kind of match)
EXPECT = {
    "a cat": ("trip_cat_mid.mp4", (20000, 26000), "visual"),
    "birthday cake with candles": ("bday_party.mp4", (0, 12000), "visual"),
    # the same puppy is in park_hindi (whole clip) and bday_party (12-20s): either is right
    "black puppy": ({"park_hindi.mp4": (0, 16000), "bday_party.mp4": (12000, 20000)}, None, "visual"),
    "fireworks at night": ("goa_text.mp4", (8000, 16000), "visual"),
    "happy birthday": ("bday_party.mp4", (0, 7000), "speech"),
    "walk the dog in the park this evening": ("park_hindi.mp4", (0, 7000), "speech"),   # Hindi speech
    "holiday trip to Goa next month": ("goa_text.mp4", (0, 6000), "speech"),           # Marathi speech
    "dog": ({"park_hindi.mp4": (0, 7000), "bday_party.mp4": (12000, 20000)}, None, "speech-or-visual"),
    "diwali": ("goa_text.mp4", (8000, 16000), "text"),
    "goa 2023": ("goa_text.mp4", (0, 8000), "text"),
}


def call(method, url, body=None, headers=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else json.loads(payload or b"null")), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace"), dict(e.headers)


def key_headers():
    return {"x-api-key": json.loads(CREDS.read_text())["apiKey"]}


def wait_idle(timeout=900):
    """Until no Immich queue has active or waiting jobs."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, queues, _ = call("GET", f"{IMMICH}/queues", headers=key_headers())
        busy = {}
        for q in queues if isinstance(queues, list) else []:
            stats = q.get("statistics") or q.get("jobCounts") or {}
            n = stats.get("active", 0) + stats.get("waiting", 0) + stats.get("delayed", 0)
            if n:
                busy[q.get("name")] = n
        if not busy:
            return
        print("  waiting on Immich jobs:", busy)
        time.sleep(5)
    raise SystemExit("Immich jobs did not finish in time")


def assets_by_name():
    status, body, _ = call("POST", f"{IMMICH}/search/metadata", {"size": 100}, key_headers())
    return {a["originalFileName"]: a["id"] for a in body["assets"]["items"]}


def smart_rank(query, names):
    """Rank (1-based) of each file among Immich's own smart-search results."""
    _, body, _ = call("POST", f"{IMMICH}/search/smart", {"query": query, "size": 50}, key_headers())
    order = [a["originalFileName"] for a in body["assets"]["items"]]
    return {n: (order.index(n) + 1 if n in order else None) for n in names}


def setup(library: Path):
    if not CREDS.exists():
        password = secrets.token_urlsafe(18)
        creds = {"email": "admin@vtest.local", "password": password}
        status, body, _ = call("POST", f"{IMMICH}/auth/admin-sign-up",
                               {"email": creds["email"], "password": password, "name": "Test Admin"})
        assert status in (200, 201), (status, body)
        status, body, _ = call("POST", f"{IMMICH}/auth/login", {"email": creds["email"], "password": password})
        assert status in (200, 201), (status, body)
        token = body["accessToken"]
        status, body, _ = call("POST", f"{IMMICH}/api-keys", {"name": "e2e", "permissions": ["all"]},
                               {"Authorization": f"Bearer {token}"})
        assert status in (200, 201), (status, body)
        creds["apiKey"] = body["secret"]
        CREDS.write_text(json.dumps(creds))
        CREDS.chmod(0o600)
        print("created test admin (credentials in .vtest-credentials)")

    _, config, _ = call("GET", f"{IMMICH}/system-config", headers=key_headers())
    if config["machineLearning"]["clip"]["modelName"] != CLIP_MODEL:
        config["machineLearning"]["clip"]["modelName"] = CLIP_MODEL
        status, body, _ = call("PUT", f"{IMMICH}/system-config", config, key_headers())
        assert status == 200, (status, body)
        print("test Immich now uses", CLIP_MODEL)

    CLI_DIR.mkdir(parents=True, exist_ok=True)
    cli = ["npx", "--yes", "@immich/cli", "-d", str(CLI_DIR)]
    subprocess.run(cli + ["login", IMMICH, key_headers()["x-api-key"]], check=True, capture_output=True)
    out = subprocess.run(cli + ["upload", "--recursive", str(library)], check=True, capture_output=True, text=True)
    print("upload:", out.stdout.strip().splitlines()[-1] if out.stdout.strip() else "done")
    time.sleep(5)
    wait_idle()

    names = sorted(assets_by_name())
    print("library:", names)
    baseline = {q: smart_rank(q, names) for q, (_, _, kind) in EXPECT.items() if kind == "visual"}
    BASELINE.write_text(json.dumps(baseline, indent=1))
    print("baseline Immich smart-search ranks (before the indexer):")
    for q, ranks in baseline.items():
        print(f"  {q!r}: {EXPECT[q][0]} at #{ranks[EXPECT[q][0]]}")


class Report:
    def __init__(self):
        self.rows = []

    def add(self, ok, name, detail=""):
        self.rows.append((ok, name, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))

    @property
    def failed(self):
        return [r for r in self.rows if not r[0]]


def vsearch(path, headers=None, raw=False):
    return call("GET", f"{VSEARCH}{path}", headers={**key_headers(), **(headers or {})}, raw=raw)


def wait_indexed(timeout=1800):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body, _ = vsearch("/api/status")
        if status == 200 and body["videos"] and body["indexed"] + body["failed"] == body["videos"]:
            return body
        print("  indexer:", body if status == 200 else status)
        time.sleep(10)
    raise SystemExit("indexer did not finish in time")


def check():
    r = Report()
    print("waiting for the indexer…")
    st = wait_indexed()
    r.add(st["failed"] == 0 and st["indexed"] == 4, "all 4 test videos indexed", json.dumps(st))
    names = assets_by_name()

    print("search page API (moments inside videos):")
    for query, (name, window, kind) in EXPECT.items():
        allowed = name if isinstance(name, dict) else {name: window}
        status, body, _ = vsearch(f"/api/search?q={urllib.request.quote(query)}")
        results = body["results"] if status == 200 else []
        top = results[0] if results else None
        lo, hi = allowed.get(top["fileName"], (0, -1)) if top else (0, -1)
        kinds = kind.split("-or-")
        moments = [m for m in (top or {}).get("moments", []) if m["kind"] in kinds and lo <= m["ms"] <= hi]
        detail = (f"top={top['fileName']} {top['matched']} best={top['best']['kind']}@{top['best']['ms']}ms"
                  + (f", {len(results)} results" if len(results) > 1 else "")
                  if top else f"no results (HTTP {status})")
        want = " or ".join(f"{n} {kind} {a // 1000}-{b // 1000}s" for n, (a, b) in allowed.items())
        r.add(bool(moments), f"{query!r} -> {want}", detail)

    silent_hits = []
    for q in ["hello", "thank you for watching", "music", "a cat"]:
        _, body, _ = vsearch("/api/search?q=" + urllib.request.quote(q) + "&kind=speech")
        silent_hits += [q for x in body["results"] if x["fileName"] == "trip_cat_mid.mp4"]
    r.add(not silent_hits, "silent video produced no made-up speech", f"hits: {silent_hits}")
    for q in ["a cat", "the meeting is cancelled", "football match", "I am going to the office", "good morning"]:
        _, body, _ = vsearch("/api/search?q=" + urllib.request.quote(q) + "&kind=speech")
        r.add(not body["results"], f"no speech matches for unrelated {q!r}",
              ", ".join(f"{x['fileName']}: {x['moments'][0]['text']}" for x in body["results"]))
    # English words find Hindi / Marathi speech through Whisper's translation pass
    for q, name in [("dog", "park_hindi.mp4"), ("park", "park_hindi.mp4"), ("Goa", "goa_text.mp4"),
                    ("holiday", "goa_text.mp4"), ("blow out the candles", "bday_party.mp4"),
                    ("going to the park", "park_hindi.mp4")]:
        _, body, _ = vsearch("/api/search?q=" + urllib.request.quote(q) + "&kind=speech")
        found = [x["fileName"] for x in body["results"]]
        snippet = next((m["text"] for x in body["results"] if x["fileName"] == name for m in x["moments"]), "")
        r.add(found[:1] == [name], f"speech {q!r} finds {name} (non-English speech)", f"{found} {snippet[:70]}")

    print("Immich's own search (what she gets in the normal Immich app):")
    baseline = json.loads(BASELINE.read_text())
    for query, before in baseline.items():
        name = EXPECT[query][0]
        name = next(iter(name)) if isinstance(name, dict) else name
        after = smart_rank(query, list(names))[name]
        r.add(after is not None and (before[name] is None or after <= before[name]),
              f"smart search {query!r}: {name}", f"rank #{before[name]} -> #{after}")
    cat_rank = smart_rank("a cat", list(names))
    videos_ranked = sorted((v, n) for n, v in cat_rank.items() if n.endswith(".mp4") and v)
    r.add(bool(videos_ranked) and videos_ranked[0][1] == "trip_cat_mid.mp4",
          "smart search 'a cat': the mid-clip cat video is the top video", str(videos_ranked))

    _, body, _ = call("POST", f"{IMMICH}/search/metadata", {"ocr": "diwali"}, key_headers())
    found = [a["originalFileName"] for a in body["assets"]["items"]]
    r.add("goa_text.mp4" in found, "Immich OCR search 'diwali' finds goa_text.mp4", str(found))
    _, body, _ = call("POST", f"{IMMICH}/search/metadata", {"description": "birthday"}, key_headers())
    found = [a["originalFileName"] for a in body["assets"]["items"]]
    r.add("bday_party.mp4" in found, "Immich description search 'birthday' finds the transcript", str(found))

    _, body, _ = call("POST", f"{IMMICH}/search/metadata", {"description": "park"}, key_headers())
    found = [a["originalFileName"] for a in body["assets"]["items"]]
    r.add("park_hindi.mp4" in found, "Immich description search 'park' finds the Hindi video (translation)", str(found))
    _, asset, _ = call("GET", f"{IMMICH}/assets/{names['bday_party.mp4']}", headers=key_headers())
    desc = (asset.get("exifInfo") or {}).get("description") or ""
    r.add(desc.startswith("🎙") and "birthday" in desc.lower(), "transcript shows in the video's info panel",
          desc[:80])
    _, asset, _ = call("GET", f"{IMMICH}/assets/{names['park_hindi.mp4']}", headers=key_headers())
    desc = (asset.get("exifInfo") or {}).get("description") or ""
    r.add("🌐" in desc, "Hindi video's info panel shows transcript + English translation", desc.replace("\n", " | ")[:120])
    _, asset, _ = call("GET", f"{IMMICH}/assets/{names['cat_photo.jpg']}", headers=key_headers())
    r.add(not ((asset.get("exifInfo") or {}).get("description") or ""), "plain photo left untouched")

    print("page features:")
    status, body, _ = vsearch("/api/search?q=" + urllib.request.quote("a cat"))
    top = body["results"][0]
    status, img, headers = vsearch(f"/api/frame/{top['assetId']}/{top['best']['frameMs']}.webp", raw=True)
    r.add(status == 200 and img[:4] == b"RIFF", "moment thumbnail served", f"HTTP {status}, {len(img)} bytes")
    status, chunk, headers = vsearch(f"/api/play/{top['assetId']}", {"Range": "bytes=0-1023"}, raw=True)
    r.add(status == 206 and len(chunk) == 1024, "video streams with seeking (HTTP 206)",
          f"HTTP {status}, {headers.get('Content-Range')}")
    status, _, _ = call("GET", f"{VSEARCH}/api/search?q=cat")
    r.add(status == 401, "search refuses requests without an Immich login", f"HTTP {status}")
    status, page, _ = call("GET", f"{VSEARCH}/vsearch/", raw=True)
    r.add(status == 200 and b"Video Search" in page, "page loads under the /vsearch prefix", f"HTTP {status}")
    status, body, _ = call("POST", f"{VSEARCH}/api/album",
                           {"name": "Search: a cat", "assetIds": [top["assetId"]]}, key_headers())
    r.add(status == 200 and body.get("id"), "save results as an Immich album", str(body)[:80])

    print("survives Immich overwriting a video's entries:")
    # What Immich's own jobs write when they re-run on a video: the thumbnail frame's vector,
    # the thumbnail's OCR text only, and the file's own (empty) description.
    sql = f"""
        update smart_search s set embedding = (select embedding from vsearch.frame f
            where f.asset_id = s."assetId" order by ms limit 1) where "assetId" = '{names["trip_cat_mid.mp4"]}';
        update ocr_search set text = 'GOA2023' where "assetId" = '{names["goa_text.mp4"]}';
        update asset_exif set description = '' where "assetId" = '{names["bday_party.mp4"]}';
    """
    subprocess.run(["docker", "compose", "-f", str(HERE / "docker-compose.vtest.yml"), "exec", "-T", "vtest-database",
                    "psql", "-q", "-U", "postgres", "-d", "immich", "-c", sql], check=True)
    overwritten = smart_rank("a cat", list(names))["trip_cat_mid.mp4"]
    time.sleep(25)  # one indexer pass (poll interval 10s in the test stack)
    restored = smart_rank("a cat", list(names))["trip_cat_mid.mp4"]
    _, body, _ = call("POST", f"{IMMICH}/search/metadata", {"ocr": "diwali"}, key_headers())
    ocr_ok = "goa_text.mp4" in [a["originalFileName"] for a in body["assets"]["items"]]
    _, asset, _ = call("GET", f"{IMMICH}/assets/{names['bday_party.mp4']}", headers=key_headers())
    desc_ok = ((asset.get("exifInfo") or {}).get("description") or "").startswith("🎙")
    logs = subprocess.run(["docker", "compose", "-f", str(HERE / "docker-compose.vtest.yml"), "logs", "vtest-vsearch"],
                          capture_output=True, text=True).stdout
    restores = [line.split("overwrote: ", 1)[1] for line in logs.splitlines() if "restored what Immich overwrote" in line]
    r.add(bool(restores) and restored is not None and restored < (overwritten or 99) and ocr_ok and desc_ok,
          "vector, OCR text and transcript all put back within one pass",
          f"cat video #{overwritten} when overwritten -> #{restored}; OCR {'ok' if ocr_ok else 'missing'}; "
          f"description {'ok' if desc_ok else 'missing'}; log: {restores[-1] if restores else 'none'}")
    # And Immich's real forced re-run of its smart-search job must not break anything.
    call("PUT", f"{IMMICH}/queues/smartSearch", {"command": "start", "force": True}, key_headers())
    time.sleep(5)
    wait_idle()
    time.sleep(15)
    final = smart_rank("a cat", list(names))["trip_cat_mid.mp4"]
    r.add(final == restored, "still correct after Immich's own forced smart-search re-run", f"cat video #{final}")

    print(f"\n{len(r.rows) - len(r.failed)}/{len(r.rows)} checks passed")
    sys.exit(1 if r.failed else 0)


if __name__ == "__main__":
    if sys.argv[1:2] == ["setup"]:
        setup(Path(sys.argv[2]))
    elif sys.argv[1:2] == ["check"]:
        check()
    else:
        raise SystemExit(__doc__)
