"""Shared, lazily created service objects (DB pool, ML client, models, indexer)."""
from . import config


class Services:
    def __init__(self):
        self._pool = None
        self.indexer = None
        from .mlclient import ImmichML
        from .speech import Transcriber
        from .textemb import TextEmbedder
        self.ml = ImmichML(config.ML_URL)
        self.transcriber = Transcriber(config.WHISPER_MODEL, config.WHISPER_DEVICE, config.WHISPER_GPU_COMPUTE)
        self.embedder = TextEmbedder(config.TEXT_MODEL, config.TORCH_THREADS)

    @property
    def pool(self):
        if self._pool is None:
            from .db import make_pool
            self._pool = make_pool()
        return self._pool

    def start_indexer(self):
        from .indexer import Indexer
        self.indexer = Indexer(self.ml, self.transcriber, self.embedder)
        self.indexer.start()


services = Services()
