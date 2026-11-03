"""Settings, all from environment variables. DB_* are shared with Immich's own .env."""
import os


def env(name: str, default: str) -> str:
    value = os.environ.get(name, "")
    return value if value else default


DB = {
    "host": env("DB_HOSTNAME", "database"),
    "port": int(env("DB_PORT", "5432")),
    "user": env("DB_USERNAME", "postgres"),
    "password": env("DB_PASSWORD", "postgres"),
    "dbname": env("DB_DATABASE_NAME", "immich"),
}

ML_URL = env("VSEARCH_ML_URL", "http://immich-machine-learning:3003")
IMMICH_URL = env("VSEARCH_IMMICH_URL", "http://immich-server:2283")
# Base for "Open in Immich" links. Empty = same origin as the page, which is the
# case when the page is served under Immich's own address (/vsearch).
IMMICH_PUBLIC_URL = env("VSEARCH_IMMICH_PUBLIC_URL", "")

STATE_DIR = env("VSEARCH_STATE_DIR", "/state")
PORT = int(env("VSEARCH_PORT", "2284"))

# Full large-v3 rather than turbo: turbo's 4-layer decoder loses the most on lower-resource
# languages (Marathi, Hindi). int8 weights keep it at ~1.8 GB VRAM next to SigLIP2.
WHISPER_MODEL = env("VSEARCH_WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = env("VSEARCH_WHISPER_DEVICE", "auto")  # auto | cuda | cpu
WHISPER_GPU_COMPUTE = env("VSEARCH_WHISPER_GPU_COMPUTE", "int8_float16")
TEXT_MODEL = env("VSEARCH_TEXT_MODEL", "BAAI/bge-m3")
TEXT_DIM = int(env("VSEARCH_TEXT_DIM", "1024"))
TORCH_THREADS = int(env("VSEARCH_TORCH_THREADS", "8"))

POLL_SECONDS = int(env("VSEARCH_POLL_SECONDS", "30"))
MAX_FRAMES = int(env("VSEARCH_MAX_FRAMES", "300"))
ML_CONCURRENCY = int(env("VSEARCH_ML_CONCURRENCY", "3"))
# Wait this long after upload so Immich finishes its own jobs (metadata, storage template move)
SETTLE_SECONDS = int(env("VSEARCH_SETTLE_SECONDS", "60"))

# Bump when the pipeline changes so every video gets reprocessed.
PIPELINE_VERSION = 3

# Immich's defaults, used when its system config doesn't override them.
DEFAULT_CLIP_MODEL = "ViT-B-32__openai"
DEFAULT_OCR_MODEL = "PP-OCRv5_mobile"
DEFAULT_OCR_MIN_DETECTION = 0.5
DEFAULT_OCR_MIN_RECOGNITION = 0.8
DEFAULT_OCR_MAX_RESOLUTION = 736
