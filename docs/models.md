# Models: what, why, and the numbers

The library this was built for is Indian family media. People speak Hindi, Marathi and English,
often mixed in one sentence, and search queries get typed in English or romanized Hinglish.
Everything runs on one RTX 2000 Ada laptop GPU with **8 GB VRAM**, shared with Immich's own
models. Each choice below was made under those constraints.

| Job | Model | Runs on | VRAM / RAM |
|---|---|---|---|
| Frames ↔ text (Immich's smart search and ours) | `ViT-SO400M-16-SigLIP2-384__webli` | Immich ML container, CUDA | ~3.9 GB VRAM |
| On-screen text | `PP-OCRv5_mobile` (Immich's default) | Immich ML container | small |
| Speech → text | Whisper **large-v3** (faster-whisper, `int8_float16`) | vsearch, CUDA | ~1.8 GB VRAM, unloads when idle |
| Speech → English | same Whisper, `task="translate"` | vsearch, CUDA | (same) |
| Speech search by meaning | `BAAI/bge-m3` | vsearch, CPU | ~2.3 GB RAM |

## Vision: SigLIP2 SO400M-384

Immich publishes recall benchmarks per language for every supported CLIP model. These are the
relevant rows (from [Immich's searching docs](https://docs.immich.app/features/searching/):
memory MiB / ms per image / recall %):

| Model | English | Hindi | Notes |
|---|---|---|---|
| `ViT-B-32__openai` (Immich default) | 1004 / 2.26 / **69.9** | n/a | English only |
| `ViT-B-16-SigLIP2__webli` | 3038 / 5.81 / 84.86 | 27.87 | fast |
| `ViT-L-16-SigLIP2-384__webli` | 3057 / 51.7 / 85.47 | 35.76 | |
| **`ViT-SO400M-16-SigLIP2-384__webli`** | 3854 / 56.57 / **85.99** | 34.94 | best English, Pareto-optimal |
| `nllb-clip-large-siglip__v1` | 4226 / 75.05 / 83.24 | **61.67** | best Hindi, but... |

Why SO400M rather than the NLLB model that's much better at Hindi:
- **NLLB models expect the query in the language set in the user's settings.** Immich's docs
  recommend SigLIP2/XLM instead for *mixed-language* search, where the query language varies.
  That's exactly this case.
- **Queries get typed in English or romanized Hindi, not Devanagari.** The Hindi column measures
  Devanagari queries, so it matters much less here.
- **It has the best English recall of any model on the list,** and fits in VRAM next to Whisper.
- Speech, the place Hindi and Marathi matter most, is handled by Whisper and its translation
  instead. The vision model only has to match what's *seen*.

Because vsearch embeds frames **through Immich's own ML service**, it automatically uses
whatever model Immich is set to. Frame vectors and photo vectors are always comparable.

## Speech: Whisper large-v3, not turbo

`large-v3-turbo` cuts the decoder from 32 layers to 4 (1.55 B to 809 M parameters) for a big
speed-up, with "minor" quality loss overall. That loss isn't spread evenly. Lower-resource
languages like Marathi suffer most, and Marathi is a big part of this library. With a GPU to
spare, the full model is the better trade:
- `int8_float16` weights keep it around **1.8 GB VRAM**, so it fits next to SigLIP2.
- It still runs in **~3–8 s per short clip** including the translation pass.
- It **unloads after 5 idle minutes**, giving the VRAM back.
- **Per-segment language detection** (`multilingual=True`) copes with Hindi/English switching
  inside one clip.
- large-v3 can also **translate** (turbo was tuned for transcription only), which made the
  next feature possible.

What came out of the tests (generated with Microsoft neural TTS voices):

| Clip | Whisper transcript | Translation pass |
|---|---|---|
| English | Happy birthday Priya. Make a wish and blow out all the candles. *(exact)* | not run |
| Hindi | चलो आज शाम को कुत्ते के साथ पार्क में घूमने चलते हैं *(exact)* | Let's go to the park with the dog this evening. |
| Marathi | आपन पुढचा महिन्याद गोव्याला सुट्टी साथी जाओया *(a few spelling slips)* | Let's go to Goa for a holiday next month. *(exact meaning)* |
| Silent | *(nothing, and no hallucinated text)* | not run |

### Why a translation pass

A meaning-based model alone isn't enough for short queries across languages. bge-m3 scored the
English query **"Goa"** against the Marathi Goa sentence at only **0.401**. Meanwhile **"a cat"**
against the Hindi *dog* sentence scored **0.576**. No threshold separates those.

With Whisper's English translation stored alongside, "Goa", "dog", "park" and "holiday" become
**exact word matches**. The translation also goes into the Immich description (🌐), so the
regular Immich app's description search finds Hindi and Marathi videos by English words too.

## Speech search: bge-m3

Multilingual (100+ languages) and strong on retrieval. It matches paraphrases ("blow out the
candles" vs "blow out *all* the candles") and meaning across languages. It runs on CPU because
the GPU is better spent on Whisper and SigLIP2, and query-time embedding takes ~100 ms on CPU.
Loading uses `use_safetensors=False`. bge-m3 ships PyTorch weights, and otherwise transformers
also pulls a 2 GB safetensors copy from a conversion PR.

## Calibration

The thresholds in `vsearch/search.py` come from `test/calibrate.py` run over the test library.
These are raw cosine similarities.

**Vision (SigLIP2):** true matches 0.095–0.145, non-matches ≤ 0.06.

| Query | best frame per video |
|---|---|
| a cat | **cat video 0.115** · others 0.018–0.031 |
| birthday cake with candles | **birthday video 0.144** · others ≤ 0.029 |
| fireworks at night | **Goa/Diwali video 0.105** · others ≤ −0.007 |
| a car *(in none)* | all ≤ 0.019 |

So `VISUAL_FLOOR = 0.07` and `VISUAL_SPREAD = 0.06`. A video is kept if it's within 0.06 of the
best match and above 0.07.

**Speech (bge-m3, over transcripts + translations):**

| True queries | best | Unrelated queries | best |
|---|---|---|---|
| walk the dog in the park this evening | 0.918 | I am going to the office | 0.590 |
| holiday trip to Goa next month | 0.917 | good morning | 0.592 |
| going to the park | 0.805 | the car broke down | 0.544 |
| happy birthday | 0.662 | the meeting is cancelled | 0.524 |
| blow out the candles | 0.607 | a cat *(2 words)* | 0.606 |

So `SPEECH_FLOOR = 0.60` for 3+ word queries, and `SPEECH_FLOOR_SHORT = 0.65` for 1–2 words.
Every true short query in the tests ("dog", "park", "Goa", "birthday", "holiday") was an exact
word in the transcript or translation, which matches regardless. **The margin for longer
paraphrases is thin** (0.590 vs 0.607). Re-run `calibrate.py` on your own library if you see
false "Said" hits.

## VRAM budget (8 GB card)

| | VRAM |
|---|---|
| desktop / other apps | ~0.6–0.9 GB |
| Immich ML: SigLIP2 SO400M (vision + text) + OCR + faces | ~3.7–4.5 GB |
| Whisper large-v3 int8_float16 (only while transcribing) | ~1.8 GB |
| **peak** | **~7 GB** |

Immich unloads idle ML models after 5 minutes (`MACHINE_LEARNING_MODEL_TTL`), and vsearch does
the same with Whisper. On a 6 GB card, set `VSEARCH_WHISPER_MODEL=large-v3-turbo` or pick a
smaller CLIP model in Immich.
