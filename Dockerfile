FROM python:3.12-slim

# FFmpeg for video/audio editing; DejaVu fonts for image and video captions.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 studio
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=studio:studio . .
USER studio

ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "uvicorn app:asgi_app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers"]
