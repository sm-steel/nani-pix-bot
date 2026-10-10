# ffmpeg renders the animated reveal video (issue #295). mise.toml pins
# BtbN's static ffmpeg 7.1.1 build and mise.lock its sha256, the same binary
# local dev and CI install. This stage installs it with a pinned,
# checksum-verified mise in locked mode, which fails on any download that
# doesn't match the lockfile; only the two static binaries leave the stage.
# Only ffmpeg is installed here; mise.toml's Node is for the docs site.
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS ffmpeg

# Bump MISE_VERSION and the sha256 together (from the release's SHASUMS256.txt).
ARG MISE_VERSION=2026.10.4
ADD --chmod=755 \
    --checksum=sha256:2b8ce21f550872807bcaabf45b6bc5c64bfbd6dc3bf49dd4e67de700ef3ceb75 \
    https://github.com/jdx/mise/releases/download/v${MISE_VERSION}/mise-v${MISE_VERSION}-linux-x64 \
    /usr/local/bin/mise
WORKDIR /tools
ENV MISE_DATA_DIR=/opt/mise \
    MISE_TRUSTED_CONFIG_PATHS=/tools
COPY mise.toml mise.lock ./
RUN mise install --locked github:BtbN/FFmpeg-Builds \
    && mkdir /out \
    && cp "$(mise where github:BtbN/FFmpeg-Builds)/bin/ffmpeg" \
          "$(mise where github:BtbN/FFmpeg-Builds)/bin/ffprobe" /out/

FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# The pinned static ffmpeg/ffprobe from the stage above (issue #295).
COPY --from=ffmpeg /out/ffmpeg /out/ffprobe /usr/local/bin/

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
