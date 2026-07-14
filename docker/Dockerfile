FROM python:3.11-slim@sha256:6d85378d88a19cd4d76079817532d62232be95757cb45945a99fec8e8084b9c2

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    openssh-client \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# ── Install Python dependencies ─────────────────────────────────────────
# Uses requirements.lock for reproducible builds when available.
# The REAL source code comes from runtime volume mounts — not baked into
# the image. This keeps the image generic: same image for all agents.
COPY pyproject.toml .
COPY requirements.lock* .
RUN mkdir -p src && touch src/__init__.py \
 && if [ -f requirements.lock ]; then \
      pip install --no-cache-dir -r requirements.lock; \
    else \
      pip install --no-cache-dir ".[dev]"; \
    fi \
 && rm -rf src

# ── Directories ─────────────────────────────────────────────────────────
# /app/src         — merged at entrypoint (cove-core + overlay)
# /app/config      — agent config (bind mount)
# /app/data        — persistent data volume
# /cove-core  — shared base mount (read-only)
# /overlay         — agent-specific overlay mount (read-only)
RUN mkdir -p /app/src /app/config /app/data/logs /app/data/projects \
             /app/data/scratch /app/data/runbooks \
             /cove-core /overlay

# ── Generic agent user (uid 1000 = the typical first user on a Linux host) ──
# All agents run as uid 1000. Git identity set at runtime in entrypoint.
RUN groupadd -g 1000 agent 2>/dev/null || true \
 && useradd -u 1000 -g 1000 -m -s /bin/bash agent 2>/dev/null || true \
 && chown -R 1000:1000 /app

# Git safe directory (wildcard — any mount path)
RUN git config --system --add safe.directory '*'

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Entrypoint handles runtime merge + startup
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod 755 /app/entrypoint.sh

CMD ["/bin/bash", "/app/entrypoint.sh"]
