# Dependency pins (supply-chain)

Product compose pins third-party images by **digest** where the registry
supports it. The app image install path prefers `requirements.lock` over open
ranges in `pyproject.toml`.

## Image digests

| Service | Reference | Notes |
|---------|-----------|--------|
| Postgres (pgvector) | `pgvector/pgvector:pg16@sha256:…` | example + provisioner |
| Nextcloud | `nextcloud:29-apache@sha256:…` | example + provisioner |
| Redis | `redis:7-alpine@sha256:…` | example + provisioner |
| Dendrite | `matrixdotorg/dendrite-monolith@sha256:…` | provisioner |
| Umami | `ghcr.io/umami-software/umami:postgresql-latest@sha256:…` | example + provisioner |
| SearXNG | `docker.io/searxng/searxng:latest@sha256:…` | example + provisioner |

Tag names stay for humans; the digest is what Compose pulls.

## Intentionally still floating

- **Local app / Caddy image tags** (`cove-core:latest`, `lucid-cove:latest`,
  `lucid-cove-caddy:latest`) — built on the host, not third-party supply chain.
- **npm inside third-party images** (Umami UI, SearXNG deps) — do not run
  `npm update` in those containers. Bump by moving the image digest after
  testing a newer upstream tag.
- **`pyproject.toml` lower bounds** (`>=`) — install-from-source convenience.
  Production and CI image builds use `requirements.lock` exact pins.

## Refresh procedure

1. Pull/test a newer upstream tag on a non-prod stack.
2. Record `docker images --digests` for that tag.
3. Update example compose + `provision/centralized.py` + compose tests together.
4. Freeze app Python soft spots from `pip freeze` on a known-good app container
   into `requirements.lock` (exact `==`).
5. Existing host stacks only pick up new digests after compose file update and
   service recreate — git pull alone does not rewrite a hand-maintained
   `docker-compose.yml`.
