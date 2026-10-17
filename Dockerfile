FROM python:3.12-slim AS builder
WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.1
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HIVEMIND_MEMORY_PATH=/app/data/chroma
RUN groupadd --gid 10001 hivemind \
    && useradd --uid 10001 --gid hivemind --no-create-home hivemind \
    && mkdir -p /app/data \
    && chown hivemind:hivemind /app/data
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn", "hivemind.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
