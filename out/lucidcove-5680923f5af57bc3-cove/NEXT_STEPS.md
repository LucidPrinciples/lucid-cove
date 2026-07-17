# New Cove Cove — Deploy & Lifecycle

Generated centralized (single-stack) Cove. Target: **standalone**.
Team: **on (steward + build team)**.

## Shared Caddy (one per box — owns 80/443, routes EVERY Cove)
This box uses a SINGLE shared Caddy so MULTIPLE Coves can run side by side (each Cove is
Caddy-less and routed by container name over the `lucidcove-net` bridge) — which is what
lets Coves on the same box federate Matrix to each other. The provisioner generated the
shared-Caddy stack into `~/.lucidcove/caddy/` (compose + base `Caddyfile` that imports
`conf.d/*.caddy` + an empty `conf.d/`). `install.sh` creates the bridge and brings the
shared Caddy up. If you deploy by hand, do this ONCE per box BEFORE the Cove:
   ```
   docker network create lucidcove-net 2>/dev/null || true
   ( cd ~/.lucidcove/caddy && COVE_CORE=/Users/mymac/Documents/LucidCove/OpenSource/lucid-cove docker compose up -d --build )
   ```
Each Cove's routing snippet lands at `~/.lucidcove/caddy/conf.d/lucidcove-5680923f5af57bc3.caddy` — written by
the in-browser "Claim your address" step (live-reloaded over the bridge) or, as a fallback,
by `provision/set_domain.py --shared`.

## Join the mesh (headless / CLI fallback)
The Cove walks you through this in the browser (Set Address → step 1 mints the join
code). Working headless? Mint the join code from the Cove UI on any device, then run
on this box:
   ```
   bash ./connect-mesh.sh <join-key>
   ```
It joins the mesh AND self-heals this Cove's DNS records afterwards.

## Deploy
1. Make sure your lucid-cove clone is at `/Users/mymac/Documents/LucidCove/OpenSource/lucid-cove` (the app mounts it).
2. Fill in model API keys in `.env` (the providers you wired).
3. From this folder:
   ```
   docker compose up -d --build
   ```
4. App: http://localhost:8200  ·  Nextcloud: http://localhost:8080

The Postgres init runs cove-core's complete `init-base.sql` + the NC database — no
migrations needed; a fresh Cove boots the full schema.

## Connect (this Cove's own Matrix homeserver — `matrix.lucidcove-5680923f5af57bc3.localhost`)
Domainless boot test: the homeserver is reachable at http://localhost:8008
(federation is off without a domain — local Connect only). The signing key is
generated on first boot; operators' Matrix accounts auto-provision on first Connect.

## Claim your Cove (founding operator)
The provisioner seeded you as the founding operator (born-owned Cove). Open your
claim link to sign in and run the setup wizard (create your Presence, build your team):

    http://localhost:8200/p/x5tAgFz8MmXZKqSBVvyf2IjaSnQtb_35xDmZ76tvby8

(If you reach the Cove on a different host/port, swap the host in that URL.)
Additional operators/presences are then added from the admin UI (copy-link invites).

## Lifecycle (debug → delete → repeat)
- Tear everything down INCLUDING data (for a clean re-test):
  ```
  docker compose down -v
  ```
- Rebuild from scratch: `docker compose up -d --build`

## Notes
- This is the centralized model (the product). Operators/presences live in ONE app,
  not separate containers.
- A real domain enables subdomain routing + Connect; leave `domain` blank for a
  local-only boot test.
