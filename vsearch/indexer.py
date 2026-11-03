"""Background worker: finds videos in Immich, indexes them, writes the results back, and
keeps them in place when Immich's own jobs overwrite them."""
import logging
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, db, media

log = logging.getLogger("vsearch.indexer")


class Indexer(threading.Thread):
    def __init__(self, ml, transcriber, embedder):
        super().__init__(name="indexer", daemon=True)
        self.ml = ml
        self.transcriber = transcriber
        self.embedder = embedder
        self.wake = threading.Event()
        self.state = {"phase": "starting", "current": None, "error": None}

    def run(self):
        try:
            self.embedder.warm()
        except Exception:
            log.exception("could not load the text model; speech search will retry later")
        while True:
            busy = False
            try:
                busy = self.tick()
                self.state["error"] = None
            except db.SchemaMismatch as e:
                log.error("%s; pausing until fixed", e)
                self.state.update(phase="paused", error=str(e))
            except Exception as e:
                log.exception("indexer pass failed")
                self.state["error"] = repr(e)
            self.transcriber.unload_if_idle()
            if not busy:
                if self.state["phase"] != "paused":
                    self.state.update(phase="idle", current=None)
                self.wake.wait(config.POLL_SECONDS)
                self.wake.clear()

    def tick(self) -> bool:
        with db.connect() as conn:
            db.check_schema(conn)
            cfg = db.immich_config(conn)
            clip_dim = db.smart_search_dim(conn)
            db.ensure_schema(conn, clip_dim)
            conn.commit()
            fixed = db.reconcile(conn, cfg, clip_dim)
            if any(fixed.values()):
                log.info("restored what Immich overwrote: %s", fixed)
            self.remove_orphan_thumbnails(conn)
            todo = db.pending_videos(conn, cfg.clip_model, limit=10)
        for item in todo:
            self.process(item, cfg, clip_dim)
        return bool(todo)

    def process(self, item: dict, cfg: db.ImmichConfig, clip_dim: int) -> None:
        asset_id, path = item["id"], item["path"]
        started = time.time()
        self.state.update(phase="indexing", current=item["name"])
        try:
            duration, has_video, has_audio = media.probe(path)
            frames = self.index_frames(asset_id, path, duration, cfg) if has_video else []

            # A model switch only invalidates the visuals; keep a still-valid transcript.
            keep_speech = item["status"] == "done" and item["pipeline"] == config.PIPELINE_VERSION
            segments, language = None, item["language"]
            if not keep_speech:
                segments, language = [], None
                if has_audio:
                    spoken, language, translated = self.transcriber.transcribe(path)
                    rows = [(s, e, text, False) for s, e, text in spoken] + \
                           [(s, e, text, True) for s, e, text in translated]
                    if rows:
                        vectors = self.embedder.embed([text for _, _, text, _ in rows])
                        segments = [(*row, v) for row, v in zip(rows, vectors)]
                    if not spoken:
                        language = None  # whisper still guesses a language for silence

            with db.connect() as conn:
                db.save_video(conn, asset_id, cfg, clip_dim, duration, language, frames, segments,
                              time.time() - started)
            log.info("indexed %s: %.0fs video, %d frames, %s speech segments (%s) in %.1fs",
                     item["name"], duration, len(frames),
                     "kept" if segments is None else len(segments), language, time.time() - started)
        except Exception as e:
            log.warning("failed to index %s: %s", item["name"], e)
            with db.connect() as conn:
                db.mark_failed(conn, asset_id, str(e))

    def remove_orphan_thumbnails(self, conn) -> None:
        """Videos deleted in Immich lose their rows via foreign keys; drop their thumbnails too."""
        frames_dir = Path(config.STATE_DIR) / "frames"
        if not frames_dir.is_dir():
            return
        on_disk = {p.name for p in frames_dir.iterdir() if p.is_dir()}
        if not on_disk:
            return
        known = {str(r[0]) for r in conn.execute("select asset_id from vsearch.video where asset_id::text = any(%s)",
                                                 (list(on_disk),))}
        for name in on_disk - known:
            shutil.rmtree(frames_dir / name, ignore_errors=True)

    def index_frames(self, asset_id, path: str, duration: float, cfg: db.ImmichConfig):
        state = Path(config.STATE_DIR)
        (state / "tmp").mkdir(parents=True, exist_ok=True)
        thumbs_dir = state / "frames" / str(asset_id)
        with tempfile.TemporaryDirectory(dir=state / "tmp") as tmp:
            tmp = Path(tmp)
            sampled = media.sample_frames(path, tmp, duration, config.MAX_FRAMES)

            def embed(frame):
                ms, jpg = frame
                embedding, texts = self.ml.frame(jpg.read_bytes(), cfg)
                return ms, embedding, texts

            with ThreadPoolExecutor(config.ML_CONCURRENCY) as pool:
                results = list(pool.map(embed, sampled))

            new_thumbs = tmp / "thumbs"
            new_thumbs.mkdir()
            for ms, jpg in sampled:
                media.thumbnail(jpg, new_thumbs / f"{ms}.webp")
            shutil.rmtree(thumbs_dir, ignore_errors=True)
            thumbs_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(new_thumbs), thumbs_dir)
        return results
