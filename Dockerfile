FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# ffmpeg renders the animated reveal video (issue #295). It's pinned in
# mise.toml and installed by mise, the same binary local dev and CI use.
# mise itself is a single pinned, checksum-verified binary; the bot reaches
# ffmpeg/ffprobe through mise's shims, which read /app/mise.toml (made the
# global config too, so it resolves whatever the working directory).
# Bump MISE_VERSION and the sha256 together (from the release's SHASUMS256.txt).
ARG MISE_VERSION=2026.10.4
ADD --chmod=755 \
    --checksum=sha256:2b8ce21f550872807bcaabf45b6bc5c64bfbd6dc3bf49dd4e67de700ef3ceb75 \
    https://github.com/jdx/mise/releases/download/v${MISE_VERSION}/mise-v${MISE_VERSION}-linux-x64 \
    /usr/local/bin/mise
ENV MISE_DATA_DIR=/opt/mise \
    MISE_GLOBAL_CONFIG_FILE=/app/mise.toml \
    MISE_TRUSTED_CONFIG_PATHS=/app \
    PATH="/opt/mise/shims:$PATH"
COPY mise.toml ./
RUN mise install && rm -rf /root/.cache/mise

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

CMD ["sh", "-c", "uv run --no-dev alembic upgrade head && exec uv run --no-dev nani-pix-bot"]
