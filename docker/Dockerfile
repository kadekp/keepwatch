# keepwatch detector container.
# Single-stage build because the runtime needs ffmpeg + opencv + numpy +
# pytorch anyway — there's nothing meaningful to strip in a second stage.

FROM python:3.12-slim AS runtime

# System dependencies for opencv (libGL, libglib) and ffmpeg.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        ca-certificates \
        curl \
 && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy only what's needed to install dependencies first (cache-friendly).
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev

# Copy source.
COPY detector/ ./detector/
COPY run_detector.py ./
COPY dashboard/ ./dashboard/

# Configuration is mounted at runtime via volume — not baked into the image.
# Default working layout:
#   /app/detector.yaml   (mounted)
#   /app/.env            (mounted, optional)
#   /app/data            (mounted, persistent)
#   /app/logs            (mounted, persistent)
#   /app/media           (mounted, persistent)

VOLUME ["/app/data", "/app/logs", "/app/media"]

EXPOSE 8000

ENV PYTHONUNBUFFERED=1 \
    FFMPEG_PATH=/usr/bin/ffmpeg

ENTRYPOINT ["uv", "run", "python", "run_detector.py"]
CMD ["--config", "/app/detector.yaml"]
