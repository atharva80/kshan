# Testing

Everything here was tested end to end against a **real, separate Immich instance** with
generated videos whose contents are known down to the second. Nothing touches your real library.

## The test library

`test/fetch_media.py` downloads 7 freely licensed photos from Wikimedia Commons and writes
`ATTRIBUTION.md` next to them. It also generates 3 speech clips with Microsoft neural voices
(`edge-tts`). `test/make_dummy.py` then assembles them into videos:

| File | What's in it | What it tests |
|---|---|---|
| `trip_cat_mid.mp4` (30 s, silent) | lake 0–10 s, food 10–20 s, **cat 20–26 s**, lake 26–30 s | a subject that's **not in the thumbnail**; silence must not produce speech |
| `bday_party.mp4` (20 s) | birthday cake 0–12 s, black puppy 12–20 s; English: *"Happy birthday Priya! Make a wish and blow out all the candles."* at 1 s | English speech, exact words and paraphrase |
| `park_hindi.mp4` (16 s) | black puppy; **Hindi**: *"चलो आज शाम को कुत्ते के साथ पार्क में घूमने चलते हैं"* at 1 s | Hindi speech found by English words |
| `goa_text.mp4` (16 s) | beach with **"GOA 2023"** drawn on 0–8 s, fireworks with **"HAPPY DIWALI"** 8–16 s; **Marathi**: *"आपण पुढच्या महिन्यात गोव्याला सुट्टीसाठी जाऊया"* | OCR, Marathi speech |
| `cat_photo.jpg` | a plain photo | photos must be left alone; ranking against a "real" cat |
| `distractors/` (35 photos) | crops of every source photo | a realistic crowd of similar-looking photos for the videos to compete with in search |

## The harness

`test/docker-compose.vtest.yml` starts a **throwaway Immich**: its own server, Postgres and
Valkey, with its own volumes, on `127.0.0.1:2293`. It borrows the real stack's GPU ML container
over the `immich_default` network, so models aren't loaded twice. The indexer under test runs on
`127.0.0.1:2294` with a 10 s poll.

```bash
cd test
pip install edge-tts                       # for the speech clips
python3 fetch_media.py media/              # photos + ATTRIBUTION.md + speech
python3 make_dummy.py media/ library/      # the 4 videos + 1 photo

docker compose -f docker-compose.vtest.yml up -d
python3 e2e.py setup library/              # test admin, SigLIP2 model, upload, baseline search
docker compose -f docker-compose.vtest.yml --profile indexer up -d
python3 e2e.py check                       # the 50 checks
docker compose -f docker-compose.vtest.yml --profile indexer down -v   # wipe it all
```

`e2e.py setup` creates a test admin with a random password (kept in `.vtest-credentials`, mode
600, gitignored) and an API key. It switches the test Immich to the same CLIP model, uploads
with the official `@immich/cli`, waits until every Immich queue is idle, and records **how plain
Immich ranks each video** as the baseline.

## The 50 checks

**Indexing**
1. All 4 videos indexed, 0 failed

**Search page: right video, right second, right kind**
2. "a cat" → `trip_cat_mid` *Seen* 20–26 s
3. "birthday cake with candles" → `bday_party` *Seen* 0–12 s
4. "black puppy" → `park_hindi` 0–16 s **or** `bday_party` 12–20 s (both contain the puppy)
5. "fireworks at night" → `goa_text` *Seen* 8–16 s
6. "happy birthday" → `bday_party` *Said* 0–7 s
7. "walk the dog in the park this evening" → `park_hindi` *Said* (Hindi speech)
8. "holiday trip to Goa next month" → `goa_text` *Said* (Marathi speech)
9. "dog" → `park_hindi` *Said*/*Seen* or `bday_party` 12–20 s
10. "diwali" → `goa_text` *Written* 8–16 s
11. "goa 2023" → `goa_text` *Written* 0–8 s (OCR read it as `GOA2023`)

**Precision**

12. Silent video gets no speech hits for "hello", "thank you for watching", "music" or "a cat"
13–17. No *Said* matches at all for "a cat", "the meeting is cancelled", "football match",
"I am going to the office", "good morning"

**English words → Hindi / Marathi speech**

18–23. "dog", "park", "going to the park" → `park_hindi`; "Goa", "holiday" → `goa_text`;
"blow out the candles" → `bday_party` (a paraphrase of "blow out *all* the candles")

**Immich's own search (the regular app)**

24–27. Smart search rank for each visual query is the same or better than plain Immich
(with the write-back alone, in a 40-item library: "a cat" #34 → #9, "birthday cake" #26 → #6)
28. "a cat": the mid-clip cat video is now the **top video** among videos
29. OCR search "diwali" → `goa_text`
30. Description search "birthday" → `bday_party` (transcript)
31. Description search "park" → `park_hindi` (English translation of Hindi speech)
32. The transcript shows in the video's info panel
33. The Hindi video shows transcript **and** 🌐 translation
34. The plain photo's description is untouched

**Immich's own search bar, merged** (the request the apps send, through the hook)

35–40. Rank of the right video, plain Immich → merged: "make a wish" #7 → **#1**,
"happy birthday Priya" #5 → **#1**, "park with the dog this evening" (Hindi speech) #2 → **#1**,
"holiday next month" #1 → #1, "a cat" #9 → **#2**, "fireworks at night" #6 → **#2**
41. A filtered search (photos only) comes back byte-identical to Immich's
42. Merged items have exactly Immich's asset fields
43. Without a login, Immich's 401 comes back through the hook

**Page features**

44. Moment thumbnails are served (WebP)
45. Playback supports seeking (HTTP 206 on a `Range` request)
46. Requests without an Immich login get 401
47. The page loads under the `/vsearch` prefix
48. "Save as album" creates an Immich album

**Resilience**

49. Simulates Immich re-running its jobs: overwrites the video's vector with its first frame,
replaces the OCR text with the thumbnail's, and blanks the description. All three come back
within one pass, the cat video returns from #5 to #2, and the indexer logs
`restored what Immich overwrote: {'smart_search': 1, 'ocr': 1, 'description': 1}`.
50. Still correct after triggering Immich's real forced Smart Search re-run

Final result: **50/50 passed.** The UI was also checked in a browser at phone size: login, search,
moment chips, playback starting at the moment, and the Open-in-Immich link.

## Pendrive uploads (browser test)

These run in a real browser against the test Immich, with files injected into the picker the
way Android's picker would hand them over:

| Run | Picked | Result |
|---|---|---|
| "Test Pendrive", first time | 12 files: 11 images with WhatsApp/Pixel/screenshot/camera names (one picked twice) + a `.txt` | 11 picked (the `.txt` dropped, the double counted once), **11 uploaded**, album has 11 |
| "Test Pendrive", same files again | 11 | **0 uploaded, 11 already in Immich**, album still 11, nothing duplicated |
| "Red 32GB" | 5 of the same + 3 new | **3 new, 5 already in Immich**, its album has 8, the first album still 11 |

| "Green 16GB": 15 files, then the page reloaded (app killed) and the same files picked | 15, then 15 | first run 15 uploaded; after the reload **0 bytes re-read from the files**, 15 recognised in 0.6 s |
| "test": 3 files; page reloaded; ` Test ` typed with 3 old + 2 new; then **Add more files** tapped | 3, then 5 | the name matched the existing pendrive, so **2 new, 3 already in Immich** went into the **same** album (5), one pendrive in the list; tapping *Add more files* or its chip fills the name |
| "PD32": a whole folder *Trip 2019* (3 photos + `Day2/`), the top level *USB/* holding *Fest/* and *Goa/*, 2 photos picked one by one | 10 | albums **PD32 - Trip 2019** (4), **PD32 - Fest** (2), **PD32 - Goa** (2), **PD32 - Individual - Random** (2); Start refuses until the Individual name is typed |
| "PD32" continued: *trip 2019* again + 1 new photo, after a reload | 5 | **1 new, 4 already in Immich**, added to the existing *Trip 2019* album (5); Immich still has exactly 4 PD32 albums |
| "Mixed drive": 3 good + 2 unreadable files | 5 | 3 uploaded, 2 failed with *Retry failed*, the run finishes |
| "Unplugged drive": 8 unreadable files in a row | 8 | stops after 5 with "pendrive not readable", doesn't fail all 8 |

Dates stored by Immich came from the filenames where there was one: `IMG-20190512-WA0001` →
2019-05-12, `PXL_20210630_101112345` → 2021-06-30 10:11, `Screenshot_2022-03-04-10-11-12` →
2022-03-04 10:11. Others kept their file time. The SHA-1 matched Node's crypto on 70
vectors (sizes 0–100003 bytes, odd chunk splits) and on a 50 MB file.

## Bugs testing caught (and how they were fixed)

| Symptom | Cause | Fix |
|---|---|---|
| Every transcription failed: `open() got an unexpected keyword argument 'metadata_errors'` | faster-whisper 1.2.1 decodes audio with PyAV and passes an argument newer PyAV removed | decode audio with ffmpeg ourselves and hand Whisper a float32 array |
| In Immich's own search, "a cat" ranked the cat video #4 of 5 | plain frame averaging: 2 cat frames out of 12 | scene-balanced pooling, which made it #2 ([architecture](architecture.md#scene-balanced-pooling)) |
| "a cat" matched the birthday clip's speech | trigram word similarity: "**a** wish … **ca**ndles" | dropped fuzzy matching for speech; meaning matching covers paraphrases |
| "a cat" matched the Hindi *dog* sentence (0.576) | short queries embed vaguely, and cross-language margins are thin | Whisper translation pass + a stricter floor for 1–2 word queries |
| "goa 2023" didn't match the on-screen text | OCR returned `GOA2023` | text matching also ignores spaces |
| Silent video tagged with language "nn" | Whisper guesses a language even for silence | language left empty when nothing was said |
| The Whisper download hung halfway, twice | Hugging Face Xet transfer stalled; newer hub clients don't resume across processes | `HF_HUB_DISABLE_XET=1` (plain resumable HTTP) |
| bge-m3 pulled an extra 2 GB in the background | transformers fetches a safetensors conversion PR when a repo only has `.bin` | `model_kwargs={"use_safetensors": False}` |
| Upload progress read "0.2 MB of 0.1 MB" | XHR progress counts multipart overhead | scale progress to the file's own size |
| The pendrive list showed stale numbers right after a run | the list reloaded before the final progress save landed | await the save; show the album's real count plus the last run's numbers |
| A stalled connection could hang a pendrive run forever (review) | uploads and API calls had no time limit | time limits on every request and upload |
| One failed duplicate check aborted the whole run and left workers spinning (review) | no retry; the hashing loop could throw without releasing the workers | retry until the server is back; `finally` always releases them |
| One unreadable file (or a loose pendrive) aborted the whole run (review) | read errors weren't caught | the file is marked failed; 5 in a row stops the run with a clear message |
| A failed album add was silently ignored (review) | the response wasn't checked | retry; re-picking adds it later |
| Immich's search bar could hang if our side was slow (review) | the merge had no time limit | 8 s limit, then plain Immich results |
| Every pendrive run made a new album with the same name (found on the real library: 4 × `Pendrive - <name>`) | the "album exists?" check compared `ownerId`, which Immich v3 doesn't send (the owner is in `albumUsers`) | read the owner from `albumUsers`; the test now counts albums in Immich |
| The page was blank when opened as `/vsearch`, or when a blocker refused `upload.js` | relative script URL; separate script file | redirect to `/vsearch/`; scripts inlined |
| The "survives Immich's re-run" test passed without exercising anything | in v3 a forced Smart Search run didn't rewrite existing video rows | the test now overwrites the rows directly, exactly as Immich's jobs would, then checks the repair |

## Calibrating thresholds on your own library

```bash
docker exec -i immich_vsearch python - < test/calibrate.py
```

This prints the best similarity per video for a list of visual and speech queries (edit the
lists to fit your library), plus every transcript, translation and OCR text. It's how the
numbers in [models.md](models.md#calibration) were measured. Adjust `VISUAL_FLOOR`,
`SPEECH_FLOOR` and `SPEECH_FLOOR_SHORT` in `vsearch/search.py` if you see misses or false hits.
