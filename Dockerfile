FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    MARKET_FUZZER_ARTIFACT_ROOT=/data/artifacts/market_fuzzer \
    MARKET_FUZZER_EXPERIMENT_ROOT=/data/artifacts

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY configs ./configs
COPY scripts ./scripts
RUN pip install --no-cache-dir .
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/artifacts \
    && chown -R appuser:appuser /app /data
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
