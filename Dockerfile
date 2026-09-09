FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# Install dependencies first (better layer caching) using the lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Now bring in the rest of the project and install it.
COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
# Unbuffered stdout/stderr — otherwise Python block-buffers output when it
# isn't attached to a TTY (as under Docker), and `docker compose logs`
# shows nothing until a large buffer fills or the process exits.
ENV PYTHONUNBUFFERED=1

CMD ["uv", "run", "--no-dev", "nani-pix-bot"]
