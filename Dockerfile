# Clipper hosted site (D-NEW). CPU only: the hosted site transcribes through Groq.
# Debian's ffmpeg is built with libass (captions burn in); `clipper doctor` checks it.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw \
    CLIPPER_HOSTED_ROOT=/srv/clipper \
    PORT=8080 \
    CLIPPER_NO_UPDATE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Editable install on purpose: paths.REPO_ROOT is found from the source tree, and the
# fonts and models live in /app/assets. The built page (studio/static) is committed.
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY assets ./assets
COPY config ./config
COPY campaigns ./campaigns
COPY scripts/ops ./scripts/ops
RUN pip install -e ".[anthropic]" \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Fonts and the face model (git-ignored, so fetched here). Caveat is not in assets.py;
# Google's repo names it Caveat[wght].ttf.
RUN python -c "from clipper import assets; d, e = assets.download_missing(); assert not e, e" \
    && python -c "import urllib.parse as u, urllib.request as r; d = r.urlopen('https://raw.githubusercontent.com/google/fonts/main/ofl/caveat/' + u.quote('Caveat[wght].ttf'), timeout=120).read(); assert len(d) > 100000; open('/app/assets/fonts/Caveat.ttf', 'wb').write(d)"

RUN useradd --create-home --uid 1000 clipper \
    && mkdir -p /srv/clipper \
    && chown -R clipper:clipper /srv/clipper /app
USER clipper
VOLUME /srv/clipper
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'), timeout=4)"

CMD ["python", "-m", "clipper.hosted"]
