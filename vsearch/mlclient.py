"""Client for Immich's own machine-learning service. Using it (instead of loading our own
CLIP) keeps video-frame embeddings in exactly the same space as Immich's photo embeddings."""
import json

import httpx
import numpy as np

from .db import ImmichConfig


class ImmichML:
    def __init__(self, url: str):
        self.http = httpx.Client(base_url=url, timeout=httpx.Timeout(600, connect=10))

    def _predict(self, entries: dict, *, image: bytes | None = None, text: str | None = None) -> dict:
        data = {"entries": json.dumps(entries)}
        files = None
        if image is not None:
            files = {"image": ("frame.jpg", image, "image/jpeg")}
        else:
            data["text"] = text
        resp = self.http.post("/predict", data=data, files=files)
        resp.raise_for_status()
        return resp.json()

    def frame(self, jpeg: bytes, cfg: ImmichConfig) -> tuple[np.ndarray, list[str]]:
        """CLIP embedding plus any text Immich's OCR model reads in the frame."""
        entries: dict = {"clip": {"visual": {"modelName": cfg.clip_model}}}
        if cfg.ocr_enabled:
            entries["ocr"] = {
                "detection": {"modelName": cfg.ocr_model,
                              "options": {"minScore": cfg.ocr_min_detection, "maxResolution": cfg.ocr_max_resolution}},
                "recognition": {"modelName": cfg.ocr_model, "options": {"minScore": cfg.ocr_min_recognition}},
            }
        out = self._predict(entries, image=jpeg)
        embedding = np.asarray(json.loads(out["clip"]), dtype=np.float32)
        texts = [t.strip() for t in out.get("ocr", {}).get("text", []) if t and t.strip()]
        return embedding, texts

    def text(self, query: str, model: str) -> np.ndarray:
        out = self._predict({"clip": {"textual": {"modelName": model}}}, text=query)
        return np.asarray(json.loads(out["clip"]), dtype=np.float32)
