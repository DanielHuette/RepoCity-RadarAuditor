# Worker image: Python worker, Playwright Chromium, Claude Code CLI and the tools for the one-time
# browser logins (virtual display, VNC server and noVNC). Built and run by docker-compose.yml; the
# server steps are in Dokumentation/SERVER.md.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:24-bookworm-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.32

FROM ${UV_IMAGE} AS uv
FROM ${NODE_IMAGE} AS node

FROM ${PYTHON_IMAGE}

ARG CLAUDE_CODE_VERSION=latest
ARG APP_UID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/radarauditor/.venv \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    PATH=/opt/radarauditor/.venv/bin:$PATH

# WeasyPrint needs Pango and HarfBuzz; tini reaps the Chromium and Claude Code child processes;
# Xvfb, x11vnc and noVNC serve the visible browser of the one-time logins (scripts/browser_login.sh).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 libffi8 \
        fonts-dejavu-core tini xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# The container is rebuilt for updates; the CLI must not try to replace itself at runtime.
ENV DISABLE_AUTOUPDATER=1
# The postinstall script puts the native Claude Code binary in place of the launcher stub.
RUN npm install -g --allow-scripts=@anthropic-ai/claude-code \
        "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force

WORKDIR /opt/radarauditor

# Dependencies before the code, so a code change reuses this layer and the browser download.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# The worker launches the full Chromium build (channel "chromium"), so the headless shell is skipped.
RUN playwright install --with-deps --no-shell chromium \
    && rm -rf /var/lib/apt/lists/*

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Read at runtime: catalogue and templates by the worker, skills and commands by the headless
# Claude Code runs of /auswerten and /optimieren (ANALYSIS_PROJECT_DIR is this directory).
COPY config ./config
COPY templates ./templates
COPY skills ./skills
COPY .claude/commands ./.claude/commands
COPY --chmod=755 scripts/browser_login.sh /usr/local/bin/browser-login

# Mount points of the named volumes; created here so the volumes start owned by the worker user.
RUN useradd --create-home --uid "${APP_UID}" --shell /usr/sbin/nologin radar \
    && mkdir -p /data/browser-profile /data/work /home/radar/.claude \
    && chown -R radar:radar /data /home/radar/.claude

ENV CLAUDE_CONFIG_DIR=/home/radar/.claude \
    BROWSER_PROFILE_DIR=/data/browser-profile \
    RADAR_WORK_DIR=/data/work \
    ANALYSIS_PROJECT_DIR=/opt/radarauditor

USER radar

HEALTHCHECK --interval=60s --timeout=20s --start-period=3m --retries=3 \
    CMD ["radarauditor", "worker", "alive"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["radarauditor", "worker", "run"]
