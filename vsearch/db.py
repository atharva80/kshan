"""Postgres access. Our data lives in a `vsearch` schema inside Immich's database, so
it can join against Immich's tables and gets included in Immich's database backups."""
import logging
from dataclasses import dataclass

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import config

log = logging.getLogger("vsearch.db")

# Immich tables/columns this service reads or writes. If an Immich upgrade changes any
# of them, the indexer pauses instead of writing into something it doesn't understand.
REQUIRED = {
    "asset": {"id", "ownerId", "type", "originalPath", "originalFileName", "deletedAt",
              "visibility", "createdAt", "localDateTime", "isOffline"},
    "asset_exif": {"assetId", "description"},
    "asset_job_status": {"assetId", "metadataExtractedAt"},
    "smart_search": {"assetId", "embedding"},
    "ocr_search": {"assetId", "text"},
    "system_metadata": {"key", "value"},
}

DESC_PREFIX = "🎙 "
DESC_TRANSLATION_PREFIX = "🌐 "
MAX_DESC_CHARS = 1200  # each for the transcript and its translation
SEGMENT_SCHEMA = 2
MAX_OCR_CHARS = 4000


class SchemaMismatch(RuntimeError):
    pass


@dataclass(frozen=True)
class ImmichConfig:
    ml_enabled: bool
    clip_enabled: bool
    clip_model: str
    ocr_enabled: bool
    ocr_model: str
    ocr_min_detection: float
    ocr_min_recognition: float
    ocr_max_resolution: int

    @property
    def smart_search_on(self) -> bool:
        return self.ml_enabled and self.clip_enabled


def conninfo() -> str:
    return psycopg.conninfo.make_conninfo(**config.DB)


def connect() -> psycopg.Connection:
    conn = psycopg.connect(conninfo())
    register_vector(conn)
    return conn


def _configure(conn: psycopg.Connection) -> None:
    register_vector(conn)
    conn.commit()


def make_pool() -> ConnectionPool:
    return ConnectionPool(conninfo(), min_size=1, max_size=4, configure=_configure, open=True)


def check_schema(conn: psycopg.Connection) -> None:
    rows = conn.execute(
        "select table_name, column_name from information_schema.columns "
        "where table_schema = 'public' and table_name = any(%s)",
        (list(REQUIRED),),
    ).fetchall()
    found: dict[str, set[str]] = {}
    for table, column in rows:
        found.setdefault(table, set()).add(column)
    missing = sorted(f"{t}.{c}" for t, cols in REQUIRED.items() for c in cols if c not in found.get(t, set()))
    if missing:
        raise SchemaMismatch("Immich's database layout changed, missing: " + ", ".join(missing))


def immich_config(conn: psycopg.Connection) -> ImmichConfig:
    row = conn.execute("select value from system_metadata where key = 'system-config'").fetchone()
    ml = (row[0] if row else {}).get("machineLearning", {})
    clip, ocr = ml.get("clip", {}), ml.get("ocr", {})
    return ImmichConfig(
        ml_enabled=ml.get("enabled", True),
        clip_enabled=clip.get("enabled", True),
        clip_model=clip.get("modelName", config.DEFAULT_CLIP_MODEL),
        ocr_enabled=ml.get("enabled", True) and ocr.get("enabled", True),
        ocr_model=ocr.get("modelName", config.DEFAULT_OCR_MODEL),
        ocr_min_detection=ocr.get("minDetectionScore", config.DEFAULT_OCR_MIN_DETECTION),
        ocr_min_recognition=ocr.get("minRecognitionScore", config.DEFAULT_OCR_MIN_RECOGNITION),
        ocr_max_resolution=ocr.get("maxResolution", config.DEFAULT_OCR_MAX_RESOLUTION),
    )


def smart_search_dim(conn: psycopg.Connection) -> int:
    return conn.execute(
        "select atttypmod from pg_attribute where attrelid = 'public.smart_search'::regclass and attname = 'embedding'"
    ).fetchone()[0]


BASE_SQL = """
create schema if not exists vsearch;
create table if not exists vsearch.meta (key text primary key, value jsonb not null);
create table if not exists vsearch.video (
    asset_id uuid primary key references public.asset(id) on delete cascade,
    status text not null,
    pipeline int not null,
    clip_model text,
    duration real,
    language text,
    frame_count int not null default 0,
    segment_count int not null default 0,
    pooled vector,
    ocr_text text,
    desc_block text,
    error text,
    attempts int not null default 0,
    took_seconds real,
    processed_at timestamptz not null default now()
);
"""


def _meta(conn: psycopg.Connection, key: str):
    row = conn.execute("select value from vsearch.meta where key = %s", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn: psycopg.Connection, key: str, value) -> None:
    conn.execute(
        "insert into vsearch.meta (key, value) values (%s, %s) on conflict (key) do update set value = excluded.value",
        (key, Jsonb(value)),
    )


def ensure_schema(conn: psycopg.Connection, clip_dim: int) -> None:
    conn.execute(BASE_SQL)
    # Vector columns need a fixed size to be indexed, and the size depends on the model.
    if _meta(conn, "frame_dim") != clip_dim:
        log.info("creating frame table for %d-dim embeddings", clip_dim)
        conn.execute("drop table if exists vsearch.frame")
        conn.execute(f"""
            create table vsearch.frame (
                asset_id uuid not null references public.asset(id) on delete cascade,
                ms int not null,
                embedding vector({clip_dim}) not null,
                ocr text,
                primary key (asset_id, ms)
            )""")
        conn.execute("create index frame_embedding_hnsw on vsearch.frame using hnsw (embedding vector_cosine_ops)")
        conn.execute("create index frame_ocr_trgm on vsearch.frame using gin (ocr gin_trgm_ops)")
        conn.execute("update vsearch.video set clip_model = null")  # visuals need redoing
        _set_meta(conn, "frame_dim", clip_dim)
    if _meta(conn, "text_dim") != config.TEXT_DIM or _meta(conn, "segment_schema") != SEGMENT_SCHEMA:
        conn.execute("drop table if exists vsearch.segment")
        conn.execute(f"""
            create table vsearch.segment (
                asset_id uuid not null references public.asset(id) on delete cascade,
                translated boolean not null default false,  -- Whisper's English translation pass
                start_ms int not null,
                end_ms int not null,
                text text not null,
                embedding vector({config.TEXT_DIM}),
                primary key (asset_id, translated, start_ms)
            )""")
        conn.execute("create index segment_text_trgm on vsearch.segment using gin (text gin_trgm_ops)")
        conn.execute("create index segment_embedding_hnsw on vsearch.segment using hnsw (embedding vector_cosine_ops)")
        conn.execute("update vsearch.video set pipeline = 0")  # speech needs redoing
        _set_meta(conn, "text_dim", config.TEXT_DIM)
        _set_meta(conn, "segment_schema", SEGMENT_SCHEMA)


# Videos worth indexing: live, visible to the owner (not hidden live-photo clips or the
# locked folder), with Immich's metadata pass done so it won't overwrite our description.
ELIGIBLE = """
    a.type = 'VIDEO' and a."deletedAt" is null and not a."isOffline"
    and a.visibility in ('timeline', 'archive')
"""


def pending_videos(conn: psycopg.Connection, clip_model: str, limit: int) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(f"""
            select a.id, a."originalPath" as path, a."originalFileName" as name,
                   v.status, v.pipeline, v.language
            from asset a
            join asset_job_status j on j."assetId" = a.id and j."metadataExtractedAt" is not null
            left join vsearch.video v on v.asset_id = a.id
            where {ELIGIBLE}
              and a."createdAt" < now() - make_interval(secs => %(settle)s)
              and (v.asset_id is null
                   or v.pipeline < %(pipeline)s
                   or (v.status = 'done' and v.clip_model is distinct from %(model)s)
                   or (v.status = 'failed' and v.attempts < 3
                       and v.processed_at < now() - v.attempts * interval '10 minutes'))
            order by a."createdAt" desc
            limit %(limit)s
        """, {"settle": config.SETTLE_SECONDS, "pipeline": config.PIPELINE_VERSION,
              "model": clip_model, "limit": limit}).fetchall()


def mark_failed(conn: psycopg.Connection, asset_id, error: str) -> None:
    conn.execute("""
        insert into vsearch.video (asset_id, status, pipeline, error, attempts, processed_at)
        values (%s, 'failed', %s, %s, 1, now())
        on conflict (asset_id) do update set
            status = 'failed', pipeline = excluded.pipeline, error = excluded.error,
            attempts = vsearch.video.attempts + 1, processed_at = now()
    """, (asset_id, config.PIPELINE_VERSION, error[:1000]))
    conn.commit()


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


SCENE_SIMILARITY = 0.82


def pooled_embedding(embeddings: list[np.ndarray]) -> np.ndarray | None:
    """One vector for the whole video (Immich stores one per asset). Frames are grouped into
    scenes first and every scene counts once, so a cat that's on screen for 5 seconds of a
    30-second lake video still weighs a third, not a sixth. (Tested in test/: a plain frame
    average left that video below two dog videos for "a cat"; scene-balanced ranks it first.)"""
    if not embeddings:
        return None
    scenes: list[np.ndarray] = []
    for e in map(_unit, embeddings):
        sims = [float(_unit(s) @ e) for s in scenes]
        if sims and max(sims) >= SCENE_SIMILARITY:
            scenes[int(np.argmax(sims))] += e
        else:
            scenes.append(e.copy())
    return _unit(np.mean([_unit(s) for s in scenes], axis=0)).astype(np.float32)


def unique_texts(texts: list[str]) -> str:
    seen, out = set(), []
    for t in texts:
        key = " ".join(t.casefold().split())
        if key and key not in seen:
            seen.add(key)
            out.append(" ".join(t.split()))
    return " ".join(out)[:MAX_OCR_CHARS]


def _clip(text: str) -> str:
    text = " ".join(text.split())
    if len(text) > MAX_DESC_CHARS:
        text = text[:MAX_DESC_CHARS].rsplit(" ", 1)[0] + " …"
    return text


def description_block(transcript: str, translation: str) -> str:
    """🎙 what was said, plus 🌐 its English translation for non-English speech."""
    transcript, translation = _clip(transcript), _clip(translation)
    if not transcript:
        return ""
    block = DESC_PREFIX + transcript
    if translation and translation.casefold() != transcript.casefold():
        block += "\n" + DESC_TRANSLATION_PREFIX + translation
    return block


def merge_ocr(conn: psycopg.Connection, asset_id, old: str | None, new: str | None) -> bool:
    """Put our on-screen text into Immich's OCR search text, keeping Immich's own."""
    row = conn.execute('select text from ocr_search where "assetId" = %s', (asset_id,)).fetchone()
    current = row[0] if row else ""
    base = " ".join(current.replace(old, " ").split()) if old and old in current else current
    merged = f"{base} {new}".strip() if new and new not in base else base
    if merged == current:
        return False
    if merged:
        conn.execute(
            'insert into ocr_search ("assetId", text) values (%s, %s) '
            'on conflict ("assetId") do update set text = excluded.text',
            (asset_id, merged),
        )
    else:
        conn.execute('delete from ocr_search where "assetId" = %s', (asset_id,))
    return True


def merge_description(conn: psycopg.Connection, asset_id, old: str | None, new: str | None) -> bool:
    """Append the transcript block to the description, replacing our previous one."""
    row = conn.execute('select description from asset_exif where "assetId" = %s', (asset_id,)).fetchone()
    if row is None:
        return False
    current = row[0] or ""
    base = current.replace(old, "").strip() if old and old in current else current.strip()
    merged = (f"{base}\n\n{new}" if base else new) if new and new not in base else base
    if merged == current:
        return False
    conn.execute('update asset_exif set description = %s where "assetId" = %s', (merged, asset_id))
    return True


def save_video(conn, asset_id, cfg: ImmichConfig, clip_dim: int, duration: float, language: str | None,
               frames: list[tuple[int, np.ndarray, list[str]]],
               segments: list[tuple[int, int, str, bool, np.ndarray]] | None, took: float) -> None:
    """Store one video's results and write the Immich-visible parts, in one transaction.
    `segments=None` keeps the existing transcript (only the visuals were redone)."""
    prev = conn.execute("select ocr_text, desc_block from vsearch.video where asset_id = %s", (asset_id,)).fetchone()
    old_ocr, old_block = prev if prev else (None, None)
    pooled = pooled_embedding([e for _, e, _ in frames])
    ocr_text = unique_texts([t for _, _, texts in frames for t in texts])

    with conn.transaction(), conn.cursor() as cur:
        cur.execute("delete from vsearch.frame where asset_id = %s", (asset_id,))
        cur.executemany(
            "insert into vsearch.frame (asset_id, ms, embedding, ocr) values (%s, %s, %s, %s) on conflict do nothing",
            [(asset_id, ms, emb, unique_texts(texts) or None) for ms, emb, texts in frames],
        )
        if segments is not None:
            cur.execute("delete from vsearch.segment where asset_id = %s", (asset_id,))
            cur.executemany(
                "insert into vsearch.segment (asset_id, translated, start_ms, end_ms, text, embedding) "
                "values (%s, %s, %s, %s, %s, %s) on conflict do nothing",
                [(asset_id, translated, s, e, text, emb) for s, e, text, translated, emb in segments],
            )
        transcript, translation = cur.execute("""
            select coalesce(string_agg(text, ' ' order by start_ms) filter (where not translated), ''),
                   coalesce(string_agg(text, ' ' order by start_ms) filter (where translated), '')
            from vsearch.segment where asset_id = %s
        """, (asset_id,)).fetchone()
        block = description_block(transcript, translation)
        cur.execute("""
            insert into vsearch.video (asset_id, status, pipeline, clip_model, duration, language, frame_count,
                                       segment_count, pooled, ocr_text, desc_block, error, attempts, took_seconds,
                                       processed_at)
            values (%(id)s, 'done', %(pipeline)s, %(model)s, %(duration)s, %(language)s, %(frames)s,
                    (select count(*) from vsearch.segment where asset_id = %(id)s and not translated),
                    %(pooled)s, %(ocr)s, %(block)s,
                    null, 0, %(took)s, now())
            on conflict (asset_id) do update set
                status = 'done', pipeline = excluded.pipeline, clip_model = excluded.clip_model,
                duration = excluded.duration, language = coalesce(excluded.language, vsearch.video.language),
                frame_count = excluded.frame_count, segment_count = excluded.segment_count,
                pooled = excluded.pooled, ocr_text = excluded.ocr_text, desc_block = excluded.desc_block,
                error = null, attempts = 0, took_seconds = excluded.took_seconds, processed_at = now()
        """, {"id": asset_id, "pipeline": config.PIPELINE_VERSION, "model": cfg.clip_model, "duration": duration,
              "language": language if segments is not None else None, "frames": len(frames), "pooled": pooled,
              "ocr": ocr_text or None, "block": block or None, "took": took})

        # The parts Immich's own search sees.
        if pooled is not None and cfg.smart_search_on and len(pooled) == clip_dim:
            cur.execute(
                'insert into smart_search ("assetId", embedding) values (%s, %s) '
                'on conflict ("assetId") do update set embedding = excluded.embedding',
                (asset_id, pooled),
            )
        merge_ocr(conn, asset_id, old_ocr, ocr_text)
        merge_description(conn, asset_id, old_block, block)


def reconcile(conn: psycopg.Connection, cfg: ImmichConfig, clip_dim: int) -> dict[str, int]:
    """Immich re-running its own jobs (or a metadata refresh) overwrites what we wrote
    for a video. Put it back."""
    fixed = {"smart_search": 0, "ocr": 0, "description": 0}
    if cfg.smart_search_on:
        fixed["smart_search"] = conn.execute("""
            insert into smart_search ("assetId", embedding)
            select v.asset_id, v.pooled from vsearch.video v
            where v.status = 'done' and v.clip_model = %s and v.pooled is not null and vector_dims(v.pooled) = %s
            on conflict ("assetId") do update set embedding = excluded.embedding
            where smart_search.embedding <> excluded.embedding
        """, (cfg.clip_model, clip_dim)).rowcount
    for asset_id, text in conn.execute("""
        select v.asset_id, v.ocr_text from vsearch.video v
        left join ocr_search o on o."assetId" = v.asset_id
        where v.status = 'done' and coalesce(v.ocr_text, '') <> ''
          and (o.text is null or strpos(o.text, v.ocr_text) = 0)
    """).fetchall():
        fixed["ocr"] += merge_ocr(conn, asset_id, text, text)
    for asset_id, block in conn.execute("""
        select v.asset_id, v.desc_block from vsearch.video v
        join asset_exif e on e."assetId" = v.asset_id
        where v.status = 'done' and coalesce(v.desc_block, '') <> ''
          and strpos(coalesce(e.description, ''), v.desc_block) = 0
    """).fetchall():
        fixed["description"] += merge_description(conn, asset_id, block, block)
    conn.commit()
    return fixed


PENDRIVE_SQL = """
create table if not exists vsearch.pendrive (
    owner_id uuid not null,
    name text not null,
    album_id uuid,
    files_total int not null default 0,
    uploaded int not null default 0,
    skipped int not null default 0,        -- already in Immich (resumed runs, cross-pendrive duplicates)
    failed int not null default 0,
    bytes_total bigint not null default 0,
    bytes_done bigint not null default 0,
    status text not null default 'in progress',
    last_error text,
    started_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (owner_id, name)
);
"""

PENDRIVE_FIELDS = ("album_id", "files_total", "uploaded", "skipped", "failed", "bytes_total", "bytes_done",
                   "status", "last_error")


def pendrives(conn: psycopg.Connection, owner_id) -> list[dict]:
    conn.execute(PENDRIVE_SQL)
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute("""
            select p.*, al."albumName" as album_name,
                   (select count(*) from album_asset aa where aa."albumId" = p.album_id) as in_album
            from vsearch.pendrive p left join album al on al.id = p.album_id
            where p.owner_id = %s order by p.updated_at desc
        """, (owner_id,)).fetchall()
    for r in rows:
        r["album_id"] = str(r["album_id"]) if r["album_id"] else None
        r["owner_id"] = str(r["owner_id"])
        for k in ("started_at", "updated_at"):
            r[k] = r[k].isoformat()
    return rows


def save_pendrive(conn: psycopg.Connection, owner_id, name: str, fields: dict) -> None:
    conn.execute(PENDRIVE_SQL)
    values = {k: fields[k] for k in PENDRIVE_FIELDS if k in fields}
    cols = ", ".join(values)
    conn.execute(
        f"insert into vsearch.pendrive (owner_id, name{', ' + cols if cols else ''}) "
        f"values (%s, %s{', %s' * len(values)}) on conflict (owner_id, name) do update set "
        + ", ".join([f"{k} = excluded.{k}" for k in values] + ["updated_at = now()"]),
        (owner_id, name, *values.values()),
    )
    conn.commit()


def stats(conn: psycopg.Connection, owner_id) -> dict:
    """Numbers for the Stats tab: the library, uploads per day, video search, pendrives."""
    lib = conn.execute("""
        select count(*) filter (where a.type = 'IMAGE'), count(*) filter (where a.type = 'VIDEO'),
               coalesce(sum(e."fileSizeInByte"), 0),
               min(a."localDateTime"), max(a."localDateTime")
        from asset a left join asset_exif e on e."assetId" = a.id
        where a."ownerId" = %s and a."deletedAt" is null and a.visibility <> 'hidden'
    """, (owner_id,)).fetchone()
    days = conn.execute("""
        select to_char(date_trunc('day', a."createdAt" at time zone 'Asia/Kolkata'), 'YYYY-MM-DD'),
               count(*), coalesce(sum(e."fileSizeInByte"), 0)
        from asset a left join asset_exif e on e."assetId" = a.id
        where a."ownerId" = %s and a."deletedAt" is null and a."createdAt" > now() - interval '30 days'
        group by 1 order by 1
    """, (owner_id,)).fetchall()
    out = {
        "library": {"photos": lib[0], "videos": lib[1], "bytes": int(lib[2]),
                    "oldest": lib[3].isoformat() if lib[3] else None, "newest": lib[4].isoformat() if lib[4] else None},
        "uploads_by_day": [{"day": d, "count": c, "bytes": int(b)} for d, c, b in days],
        "videos": status_counts(conn, owner_id),
        "pendrives": pendrives(conn, owner_id),
    }
    try:
        row = conn.execute(f"""
            select coalesce(sum(v.duration), 0), coalesce(sum(v.segment_count), 0), coalesce(avg(v.took_seconds), 0),
                   coalesce(sum(v.frame_count), 0)
            from vsearch.video v join asset a on a.id = v.asset_id where {ELIGIBLE} and a."ownerId" = %s and v.status = 'done'
        """, (owner_id,)).fetchone()
        langs = conn.execute(f"""
            select coalesce(v.language, 'none'), count(*) from vsearch.video v join asset a on a.id = v.asset_id
            where {ELIGIBLE} and a."ownerId" = %s and v.status = 'done' group by 1 order by 2 desc
        """, (owner_id,)).fetchall()
        failed = conn.execute(f"""
            select a."originalFileName", left(v.error, 160) from vsearch.video v join asset a on a.id = v.asset_id
            where {ELIGIBLE} and a."ownerId" = %s and v.status = 'failed' order by v.processed_at desc limit 20
        """, (owner_id,)).fetchall()
        out["videos"].update(hours=round(float(row[0]) / 3600, 2), spoken_segments=row[1], frames=row[3],
                             avg_seconds=round(float(row[2]), 1), languages=dict(langs),
                             failures=[{"file": f, "error": e} for f, e in failed])
    except psycopg.Error:
        conn.rollback()
    return out


def status_counts(conn: psycopg.Connection, owner_id) -> dict:
    row = conn.execute(f"""
        select count(*),
               count(*) filter (where v.status = 'done'),
               count(*) filter (where v.status = 'failed' and v.attempts >= 3)
        from asset a left join vsearch.video v on v.asset_id = a.id
        where {ELIGIBLE} and a."ownerId" = %s
    """, (owner_id,)).fetchone()
    return {"videos": row[0], "indexed": row[1], "failed": row[2], "pending": row[0] - row[1] - row[2]}
