# Pinned by digest, so a rebuild starts from the image CI tested. Dependabot or a manual bump
# moves it; docs/reproducible-builds.md says how.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS craft

# Craft app CLIs pinned in craft_apps.json; each tarball's sha256 is checked
# before only its bin/<app>-cli is kept.
COPY craft_apps.json /build/craft_apps.json
COPY scripts/install_craft_apps.py /build/install_craft_apps.py
RUN python /build/install_craft_apps.py /build/craft_apps.json /opt/craft

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# FFmpeg for video/audio editing; DejaVu fonts for image and video captions
# (fontconfig lets the Craft apps find them too).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fontconfig \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 studio
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY --from=craft /opt/craft /opt/craft
COPY --chown=studio:studio . .
USER studio

ENV PYTHONUNBUFFERED=1 \
    CRAFT_APPS_DIR=/opt/craft
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"10000\")}/health', timeout=4)"
CMD ["sh", "-c", "uvicorn app:asgi_app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers"]
