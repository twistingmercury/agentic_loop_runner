FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

ARG BUILD_VER

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .

RUN uv run ruff format --check
RUN uv run ruff check
RUN uv run pytest
RUN uv version ${BUILD_VER}
RUN uv build

#---------------------------------------------------------------------
FROM scratch AS export

COPY --from=build /workspace/dist/ .