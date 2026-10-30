FROM python:3.12-slim AS craft

# Craft app CLIs pinned in craft_apps.json; each tarball's sha256 is checked
# before only its bin/<app>-cli is kept.
COPY craft_apps.json /build/craft_apps.json
COPY scripts/install_craft_apps.py /build/install_craft_apps.py
RUN python /build/install_craft_apps.py /build/craft_apps.json /opt/craft

FROM python:3.12-slim

# FFmpeg for video/audio editing; DejaVu fonts for image and video captions
# (fontconfig lets the Craft apps find them too).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fontconfig \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 studio
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=craft /opt/craft /opt/craft
COPY --chown=studio:studio . .
USER studio

ENV PYTHONUNBUFFERED=1 \
    CRAFT_APPS_DIR=/opt/craft
CMD ["sh", "-c", "uvicorn app:asgi_app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers"]
