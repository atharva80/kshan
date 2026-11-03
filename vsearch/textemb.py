"""Multilingual text embeddings (bge-m3) so speech can be searched across languages:
an English query finds a Hindi or Marathi sentence that means the same thing."""
import logging
import threading

import numpy as np

log = logging.getLogger("vsearch.textemb")


class TextEmbedder:
    def __init__(self, model_name: str, threads: int):
        self.model_name = model_name
        self.threads = threads
        self.model = None
        self.lock = threading.Lock()

    def _load(self):
        if self.model is None:
            import torch
            from sentence_transformers import SentenceTransformer
            torch.set_num_threads(self.threads)
            log.info("loading text model %s", self.model_name)
            # use_safetensors=False: bge-m3 ships pytorch weights; otherwise transformers also
            # downloads a 2 GB safetensors copy from a conversion PR in the background.
            self.model = SentenceTransformer(self.model_name, device="cpu",
                                             model_kwargs={"use_safetensors": False})
            self.model.max_seq_length = 512

    def warm(self):
        with self.lock:
            self._load()

    def embed(self, texts: list[str]) -> np.ndarray:
        with self.lock:
            self._load()
            return self.model.encode(texts, batch_size=16, normalize_embeddings=True,
                                     convert_to_numpy=True).astype(np.float32)
