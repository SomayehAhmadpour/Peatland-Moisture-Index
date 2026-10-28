FROM python:3.12.8-slim-bookworm

# uv installs exactly what uv.lock pins (no resolution at build time).
COPY --from=ghcr.io/astral-sh/uv:0.6.3 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# rasterio's manylinux wheel bundles GDAL but links the system expat library
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --group dev --group ui

COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
COPY config ./config
COPY data ./data
COPY scripts ./scripts
COPY tests ./tests
COPY ui ./ui
COPY .streamlit ./.streamlit
COPY examples ./examples
RUN uv sync --frozen --group dev --group ui

RUN useradd --create-home iris && mkdir -p /app/output && chown iris /app/output
USER iris

ENTRYPOINT ["iris-ndwi"]
CMD ["--help"]
