# Stages: base (system packages + runtime deps) -> dev (adds dev deps + tests) and runtime (default).

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models \
    UPLOAD_DIR=/data/uploads

# ffmpeg provides ffprobe (media validation) and audio decoding.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Non-root user. Named volumes mounted at these paths inherit this ownership.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data/uploads /models /srv \
    && chown app:app /data/uploads /models /srv

WORKDIR /srv

# Install dependencies from pyproject.toml before copying the source, so code changes
# do not invalidate this layer. The project itself is not installed; it runs from /srv.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt


FROM base AS dev

RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['optional-dependencies']['dev']))" > /tmp/requirements-dev.txt \
    && pip install -r /tmp/requirements-dev.txt

COPY app ./app
COPY tests ./tests
USER app
CMD ["pytest"]


FROM base AS runtime

COPY app ./app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
