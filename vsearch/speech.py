"""Speech-to-text with faster-whisper. The model loads on first use and unloads after a few
idle minutes, so it only holds GPU memory while there are videos to transcribe."""
import gc
import logging
import subprocess
import threading
import time

import numpy as np

log = logging.getLogger("vsearch.speech")
SAMPLE_RATE = 16000


def load_audio(path: str) -> np.ndarray:
    """16 kHz mono float32, decoded by ffmpeg (faster-whisper's own PyAV decoding breaks
    with newer PyAV releases)."""
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "f32le", "-"],
        capture_output=True, timeout=1800,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"audio decode failed: {proc.stderr.decode(errors='replace').strip()[-300:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)

IDLE_UNLOAD_SECONDS = 300
# Whisper's classic hallucinations on silence/music; only dropped when confidence is low.
HALLUCINATIONS = {
    "thank you for watching", "thanks for watching", "subscribe", "like and subscribe",
    "subtitles by the amara.org community", "please subscribe", "you", "bye", "thank you",
}


def _needs_translation(language: str | None, spoken: list[tuple[int, int, str]]) -> bool:
    if language and language != "en":
        return True
    # Detected as English but written in another script (Hinglish clips, mixed speakers)
    return any(not ch.isascii() and ch.isalpha() for _, _, text in spoken for ch in text)


def _keep(seg, text: str) -> bool:
    if not any(ch.isalnum() for ch in text):
        return False
    if seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.8:
        return False
    if seg.compression_ratio > 2.6:  # looping repetition
        return False
    if text.casefold().strip(" .!?,") in HALLUCINATIONS and seg.avg_logprob < -0.5:
        return False
    return True


class Transcriber:
    def __init__(self, model_name: str, device: str, gpu_compute: str):
        self.model_name = model_name
        self.device = device
        self.gpu_compute = gpu_compute
        self.model = None
        self.active_device = None
        self.last_used = 0.0
        self.lock = threading.Lock()

    def _load(self, device: str):
        from faster_whisper import WhisperModel
        compute = self.gpu_compute if device == "cuda" else "int8"
        log.info("loading whisper %s on %s (%s)", self.model_name, device, compute)
        self.model = WhisperModel(self.model_name, device=device, compute_type=compute)
        self.active_device = device

    def _decode(self, audio: np.ndarray, **options) -> tuple[list[tuple[int, int, str]], object]:
        kwargs = dict(vad_filter=True, vad_parameters={"min_silence_duration_ms": 500}, beam_size=5,
                      condition_on_previous_text=False, **options)
        segments, info = self.model.transcribe(audio, **kwargs)
        out = []
        for seg in segments:  # a generator: decoding actually happens here
            text = seg.text.strip()
            if _keep(seg, text):
                out.append((round(seg.start * 1000), round(seg.end * 1000), text))
        return out, info

    def _run(self, audio: np.ndarray):
        try:
            # Per-segment language detection helps with Hindi/English/Marathi mixing.
            spoken, info = self._decode(audio, multilingual=True)
        except TypeError:
            spoken, info = self._decode(audio)
        translated = []
        if spoken and _needs_translation(info.language, spoken):
            # Second pass in English, so English words ("dog", "Goa") find Hindi/Marathi speech
            # by plain keyword and in Immich's own description search.
            translated, _ = self._decode(audio, task="translate", language=info.language)
        return spoken, info.language, translated

    def transcribe(self, path: str) -> tuple[list[tuple[int, int, str]], str | None, list[tuple[int, int, str]]]:
        """([(start_ms, end_ms, text)], detected language, [(start_ms, end_ms, english)] for
        non-English speech)"""
        audio = load_audio(path)
        if audio.size < SAMPLE_RATE // 2:  # under half a second of sound
            return [], None, []
        with self.lock:
            devices = ["cuda", "cpu"] if self.device == "auto" else [self.device]
            if self.active_device in devices:
                devices.remove(self.active_device)
                devices.insert(0, self.active_device)
            last_error = None
            for device in devices:
                try:
                    if self.model is None or self.active_device != device:
                        self._unload()
                        self._load(device)
                    result = self._run(audio)
                    self.last_used = time.monotonic()
                    return result
                except Exception as e:  # missing CUDA libs, out of GPU memory, ...
                    if device != "cpu":
                        log.warning("whisper on %s failed (%s), trying next device", device, e)
                        self._unload()
                    last_error = e
            raise RuntimeError(f"transcription failed: {last_error}")

    def _unload(self):
        if self.model is not None:
            log.info("unloading whisper")
            self.model = None
            self.active_device = None
            gc.collect()

    def unload_if_idle(self):
        with self.lock:
            if self.model is not None and time.monotonic() - self.last_used > IDLE_UNLOAD_SECONDS:
                self._unload()
