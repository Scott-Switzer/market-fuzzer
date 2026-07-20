FROM docker:27-cli AS docker-cli
FROM python:3.12-slim

ARG GIT_COMMIT_SHA=unavailable
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GIT_COMMIT_SHA=${GIT_COMMIT_SHA} \
    PORT=8000 \
    ARENA_DB_PATH=/data/arena.sqlite3 \
    MARKET_FUZZER_ARTIFACT_ROOT=/data/artifacts/market_fuzzer \
    MARKET_FUZZER_EXPERIMENT_ROOT=/data/artifacts

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY configs ./configs
COPY scripts ./scripts
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
RUN pip install --no-cache-dir .
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/artifacts \
    && chown -R appuser:appuser /app /data
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=6 \
  CMD python -c "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/api/ready',timeout=2); d=json.load(r); assert r.status==200 and d['status']=='ready' and d['database']=='ok' and d['artifact_store']=='ok'"
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
