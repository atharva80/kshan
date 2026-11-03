FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch first (only used for the small text-embedding model), so pip
# doesn't pull the multi-GB CUDA build as a sentence-transformers dependency.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# faster-whisper (CTranslate2) loads cuBLAS/cuDNN from the nvidia pip wheels
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib \
    HF_HOME=/models \
    PYTHONUNBUFFERED=1 \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,video

COPY vsearch ./vsearch

EXPOSE 2284
CMD ["python", "-m", "vsearch"]
