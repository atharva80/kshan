# Decisions

The forks in the road, in the order they came up. Each entry gives what was considered, what
was picked, and why.

---

### 1. Where the library lives: on the devices, or on a server?

**Options:** organize on the phone/iPad · a server the devices talk to.

**Picked:** a server.

**Why:** the actual problem is **storage**. Both devices were nearly full, and a 9th-gen iPad
has 3 GB of RAM. Even a perfect on-device classifier leaves the library with nowhere to live.
A server holds everything, and the phone only streams thumbnails.

### 2. What runs the library

**Options:** Immich · PhotoPrism · LibrePhotos · Ente (self-hosted) · a custom app.

**Picked:** Immich.

**Why:**
- Native iOS and Android apps with **automatic backup** (which frees the devices).
- A timeline, faces and CLIP smart search, plus **exact-duplicate skipping by checksum**, which
  matters when the same photo sits on three pendrives.
- Very active development.

It falls short only on videos (one frame, no audio), and that gap is what this project fills.

### 3. Which machine hosts it

**Options:** a free cloud VPS · a spare laptop · the main laptop with the GPU.

**Picked:** the main laptop, which has the RTX 2000 Ada.

**Why:**
- The GPU makes Whisper large-v3 and SigLIP2 practical.
- Pendrive-scale libraries (≤ 200 GB) don't fit a 100 GB free VPS.
- Private family photos are better off on hardware you own.

**Cost:** the machine has to stay awake for remote access.

### 4. How someone 700 km away reaches it

| Option | Problem |
|---|---|
| Port forwarding + DuckDNS | Indian ISPs often use carrier-grade NAT; needs router access; exposes the home IP; laptop must stay home |
| Cloudflare Tunnel | free plan caps request bodies at **100 MB**, so phone videos fail to upload |
| Tailscale Funnel | puts Immich on the **public internet** |
| Public reverse proxy on a VPS | same exposure, plus a server to run |
| Sharing the owner's Tailscale login | no |
| **Tailscale machine sharing** | ✅ |

**Picked:** Tailscale machine sharing.

**Why:**
- She uses her own free account.
- She sees only that one machine, and access can be revoked any time.
- It works through any NAT.
- Immich gets a valid HTTPS certificate via `tailscale serve`.

**Catch, found and fixed:** under the default allow-all policy, a shared user can reach *every
port* on the machine. A two-rule grant limits shared users to Immich's port
([`deploy/tailscale-policy.hujson`](../deploy/tailscale-policy.hujson)).

### 5. Storage layout

**Picked:** a dedicated btrfs subvolume `/srv/immich`, with the Postgres directory set to `chattr +C` (NOCOW).

**Why:**
- The subvolume can be snapshotted and sent as one unit, and stays out of system snapshots.
- Copy-on-write fragments database files badly, so Postgres is NOCOW.
- The media keeps CoW and checksums, so `btrfs scrub` detects silent corruption in a photo archive.

### 6. How to add video search: fork Immich, use its API, or work beside it

**Options:**
- Fork Immich: heavy, and it diverges.
- Push results through Immich's public API: there's no API to add vectors or OCR text to an asset.
- A sidecar that **writes into Immich's search tables** and keeps its own data next to them.

**Picked:** the sidecar.

**Why:** it's the only way the *regular Immich app* gets better without changing Immich.

**Guarded by:**
- a schema check before every pass (pause, never guess)
- merging instead of replacing (her own descriptions and Immich's OCR text survive)
- foreign keys with cascade
- reconcile, which re-applies results if Immich overwrites them

### 7. Where frame embeddings come from

**Options:** load our own CLIP in vsearch · call **Immich's ML container**.

**Picked:** Immich's ML.

**Why:**
- The vectors are identical in model and preprocessing, so video frames and photos rank on
  the same scale.
- No second copy of a 4 GB model in VRAM.
- Changing the model in Immich's settings carries over automatically.

### 8. The vision model

**Picked:** `ViT-SO400M-16-SigLIP2-384__webli`.

**Why:**
- Best English recall in Immich's benchmark (85.99%).
- Immich recommends SigLIP2/XLM for mixed-language queries.
- NLLB is far better on Hindi, but it expects one fixed query language and is weaker in English.

Details in [models.md](models.md).

### 9. Whisper large-v3 vs turbo

**Picked:** large-v3, `int8_float16`.

**Why:** with a GPU available, accuracy on **Marathi and Hindi** is worth more than turbo's speed,
since turbo's 4-layer decoder loses the most on lower-resource languages. int8 weights keep it
at ~1.8 GB VRAM. It also translates, which turbo isn't tuned for.

### 10. Searching Indian-language speech from English

**Options:**
- Multilingual embeddings only.
- Transliterate Devanagari to Latin.
- Hinglish-specific Whisper fine-tunes.
- **A Whisper translation pass.**

**Picked:** embeddings (bge-m3) **plus** a translation pass for non-English speech.

**Why:**
- Measured, embeddings alone couldn't separate "Goa" vs the Marathi Goa sentence (0.401, a
  true match) from "a cat" vs the Hindi dog sentence (0.576, false).
- Transliteration produces spellings nobody types ("govyaalaa").
- Hinglish fine-tunes hurt Marathi and English.
- The translation makes English words exact matches. In the Immich description it also makes
  the regular app find these videos.

### 11. One vector per video

**Options:** first frame (Immich's way) · mean of frames · **scene-balanced mean**.

**Picked:** scene-balanced.

**Why:** a plain mean let 10 seconds of lake drown out a 6-second cat, and the cat video ranked
#4 of 5 for "a cat". Counting each scene once ranked it #2, behind only an actual cat photo,
with no other query getting worse. It held across thresholds 0.75–0.9
([architecture](architecture.md#scene-balanced-pooling)).

### 12. Where the search page lives and how it logs in

**Picked:** the same origin as Immich (`/vsearch` via `tailscale serve`), authenticated
**by Immich itself**.

**Why:**
- It sees Immich's session cookie, and its own login form sets the same cookies, so one
  account and one login cover both.
- No new user database, no new secrets.
- Per-user isolation comes for free (`ownerId`).
- Video playback goes through Immich's own endpoint with the user's token, so transcodes and
  seeking just work.

### 13. bge-m3 on CPU

**Why:** 8 GB of VRAM is better spent on SigLIP2 and Whisper. bge-m3 on CPU embeds a query in
~100 ms and a clip's segments in well under a second, and it stays loaded for search.

### 14. Making Immich's own search bar search inside videos

**Options:**
- Only the separate page.
- Inject a script into Immich's web UI (doesn't reach the native apps, and breaks easily).
- Patch the apps.
- **Route the one search endpoint through vsearch.**

**Picked:** `tailscale serve` sends `POST /api/search/smart` to vsearch, which calls Immich
unchanged and blends in video moments.

**Why:**
- It works in the **native phone apps and the web** at once, with zero changes to Immich.
- Responses keep Immich's exact shape, and anything unusual passes through byte for byte.
- On the test library it took "make a wish" (spoken) from #7 to #1 and "a cat" (at 0:20) from #9 to #2.

**Cost:**
- It only applies through the tailnet address.
- Search depends on the vsearch container being up.
- The apps still open videos at 0:00. The companion page covers jumping to the moment.

### 15. Pendrives with only a phone

**Constraint:** the person has a phone and an iPad, no computer. The only computer is the
server, 700 km away. The Immich app backs up the phone's gallery but can't read a USB drive,
and immich-go needs a computer.

**Picked:** a **Pendrives** tab on the companion page. The phone browser reads the files
through the OTG adapter, hashes them locally (streaming SHA-1), asks Immich which it already
has (`bulk-upload-check`), uploads the rest straight to Immich's API, and files them into a
per-pendrive album. Progress is tracked server-side.

**Why:**
- No app to install.
- Resuming is just picking the same files again (checksums, not bookkeeping).
- Duplicates across pendrives are stored once.
- Each pendrive is an Immich album, so sorting and later **downloading albums back onto
  pendrives** use Immich's own features.

### 16. How to test

**Picked:** a throwaway Immich (own database and volumes) fed with generated videos whose
contents are known per second. 50 automated checks cover the page, Immich's own search and search bar,
precision, cross-language speech and resilience.

**Why:** "it indexed without errors" proves nothing about search quality. Known ground truth
does, and it caught a string of real bugs ([testing.md](testing.md#bugs-testing-caught-and-how-they-were-fixed)).
Test photos come from Wikimedia Commons with attribution and are downloaded on demand, never
committed.

### 17. A public benchmark instead of four generated clips

**Picked:** Charades-STA (visual moments with time windows) plus FLEURS Hindi, Marathi and
English (read speech with shared sentence ids), searched across one whole library in a
throwaway Immich, with 30/70 dev/test splits and fixed seeds. See [evaluation.md](evaluation.md).

**Considered:** more generated clips (no ground truth beyond what we generated; it can't
expose blind spots); YouTube-sourced datasets (licences, links rot); MSR-VTT and similar
captioning sets (whole-video captions, no moments, English only).

**Why:** Charades-STA has human-written queries with start/end times, so "the right second"
can be measured. FLEURS has the same sentence recorded in many languages, so an English query
with a Hindi or Marathi gold clip comes for free, with no translation of our own. Both are
public and pinned by sha256. Charades is non-commercial, so only the download script is
committed; clips are fetched one by one with HTTP range requests instead of the 16 GB archive.
Searching the whole library, not one given video, matches how the app is used, so the
numbers are deliberately not compared with the moment-retrieval papers.

**Number discipline:** every number in the docs comes from a results file that records the
commit and dataset hashes; fixes are tuned on dev and reported on test; a fix that didn't help
stays in the table.

### 18. Whole-word matching for "the exact words"

**Picked:** Postgres regex phrase matching on word boundaries (`vsearch/lexical.py`), with
Devanagari vowel signs and the virama counted as word characters, and optional separators
between letters and digits for OCR ("goa 2023" ↔ "GOA2023").

**Considered:** `ILIKE '%query%'` (what shipped; also matches "cat" inside "vacation" and
"LOCATION", and scored those hits as perfect matches); a `tsvector` column with the `simple`
config (needs a schema change and re-index, and its tokenizer doesn't keep merged OCR words
apart from real ones).

**Why:** the regex runs on the existing pg_trgm GIN indexes, so it works on an already-
indexed database with no migration. POSIX `[:alnum:]` and Python's `\w` both treat Devanagari
combining marks as non-letters, which would put a "boundary" inside कुत्ते; listing the
Unicode ranges explicitly fixes that (tested against Postgres: `tests/test_lexical_sql.py`).
