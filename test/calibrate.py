"""Prints raw similarity scores per query and video, to set the thresholds in search.py.
Run inside the indexer container:  docker exec -i <container> python - < calibrate.py"""
from vsearch import db
from vsearch.services import services

VISUAL = ["a cat", "birthday cake with candles", "black puppy", "fireworks at night", "a mountain lake",
          "a beach", "a car", "a wedding", "snow"]
SPEECH = ["happy birthday", "walk the dog in the park this evening", "holiday trip to Goa next month",
          "make a wish", "the meeting is cancelled", "football match"]

with db.connect() as conn:
    names = dict(conn.execute('select id, "originalFileName" from asset').fetchall())
    model = db.immich_config(conn).clip_model
    print("VISUAL: best frame cosine per video")
    for q in VISUAL:
        v = services.ml.text(q, model)
        rows = conn.execute("""select asset_id, max(1 - (embedding <=> %s)) from vsearch.frame
                               group by asset_id order by 2 desc""", (v,)).fetchall()
        print(f"  {q!r:32}", "  ".join(f"{names[a][:10]}={s:.3f}" for a, s in rows))
    print("SPEECH: best segment cosine per video")
    for q in SPEECH:
        v = services.embedder.embed([q])[0]
        rows = conn.execute("""select asset_id, max(1 - (embedding <=> %s)) from vsearch.segment
                               group by asset_id order by 2 desc""", (v,)).fetchall()
        print(f"  {q!r:40}", "  ".join(f"{names[a][:10]}={s:.3f}" for a, s in rows))
    print("TRANSCRIPTS")
    for a, lang, text, en in conn.execute("""
            select v.asset_id, v.language,
                   string_agg(s.text, ' ' order by s.start_ms) filter (where not s.translated),
                   string_agg(s.text, ' ' order by s.start_ms) filter (where s.translated)
            from vsearch.video v left join vsearch.segment s using (asset_id)
            group by v.asset_id, v.language"""):
        print(f"  {names[a]}: [{lang}] {text}" + (f"\n      EN: {en}" if en else ""))
    print("OCR")
    for a, text in conn.execute("select asset_id, ocr_text from vsearch.video"):
        print(f"  {names[a]}: {text}")
