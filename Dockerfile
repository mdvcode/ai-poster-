FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev && useradd --uid 10001 --create-home poster && mkdir /app/data && chown poster:poster /app/data
USER poster
ENV PYTHONUNBUFFERED=1
VOLUME ["/app/data"]
CMD ["/app/.venv/bin/ai-poster", "run"]
