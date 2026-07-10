# Blast-Radius Map — control planes reachable from an agent container (#D34)

**Scope.** This is the *static-analysis* version of #D34: what an attacker (or a
mis-tuned agent running code) inside the **Cove app container** can reach, derived
entirely from repo sources — `provision/centralized.py` (the compose the app runs
in), `provision/netconfig.py` (Caddy), the docker init SQL, and the env-var wiring.
It has **no live-box access**, so any row whose real exposure depends on a running
host's network/bindings is flagged `NEEDS-LIVE-CONFIRM`.

**Threat model.** The Cove *app* container runs agent code (LangGraph turns, tools).
Agents are gated by the approval/tool layer, but a prompt-injection or a tool bug
that reaches `os.environ`, the DB, or an in-network address bypasses that gate. So
"who can reach it" below means *the app process*, i.e. *any code path an agent can
influence*, not "an agent is allowed to."

**This is a report.** Fixes become their own tickets (see *Proposed hardening* +
the follow-up list at the bottom) — none are part of batch-2 except #D35, already
shipped.

---

## Surfaces

| # | Surface | Who can reach it | Credential involved | Risk | Proposed hardening |
|---|---------|------------------|---------------------|------|--------------------|
| 1 | **Caddy admin API** (`:2019` on the box's Caddy network; the real admin is loopback `:2018` after #D35) | The app container over the bridge (it POSTs `/load` on address-claim). Before #D35, **any co-tenant container** on `lucidcove-net`. | None (#D32 was Host-header only) → `LP_CADDY_ADMIN_TOKEN` after #D35 | Reconfigure the box's TLS/routing for **every** Cove it fronts; hijack/serve any hostname. | **DONE (#D35):** loopback admin + token-gated proxy. Residual: rotate the token; confirm no other bridge path. |
| 2 | **Ollama** (`OLLAMA_BASE_URL`, default `host.docker.internal:11434`, or mesh `100.64.0.1:11434`) | The app container (model calls). **No auth on Ollama.** | None | Pull/replace/delete models (poison the tuner model), exhaust the single RTX 3090 (DoS all tuning), read prompts of co-located Coves. | Put Ollama behind a token proxy or a private admin net; restrict `/api/pull`/`/api/delete`. `NEEDS-LIVE-CONFIRM` (host binding + who else is on that address). |
| 3 | **Nextcloud admin** (`{cid}-nextcloud:80`) | The app container: `NEXTCLOUD_ADMIN_USER` / `NEXTCLOUD_ADMIN_PASSWORD` are in the **app env** (`centralized.py` app `environment:`). | NC **admin** password | Full control of the Cove's Nextcloud — every Presence's files, calendars, WebDAV. The admin pw sitting in the agent app's env is the sharp edge. | Give the app a **scoped NC service account**, not admin; keep the admin pw out of the agent app env (vault / separate provisioning-only container). **HIGH.** |
| 4 | **Cove Postgres** (`postgres:5432`) | The app container: `DATABASE_URL` (with `POSTGRES_PASSWORD`) in the app env. | Postgres app role pw | Full read/write of `accounts` (incl. per-operator BYOK `model_api_key`, `nc_password`), `echoes`, approvals, queues. Expected for the app, but agent code inherits it wholesale. | Least-privilege DB role for agent-invokable paths (no `accounts`/secret columns); move BYOK keys to an encrypted store (already TODO #121). **HIGH.** |
| 5 | **Model-provider API keys** | The app container: `OPENROUTER_API_KEY` (env), plus per-operator BYOK keys in `accounts.model_api_key` (DB, plaintext — TODO #121). | Paid API keys | Exfiltrate keys → run up spend / abuse under the operator's account. | Encrypt BYOK at rest (#121); front provider calls with a broker that holds the key so it never enters agent-readable env/DB. **HIGH.** |
| 6 | **Hub inter-service API** (`SHARED_CONTAINER_URL` → the shared/hub container) | The app container: `SHARED_CONTAINER_SECRET` is written into the Cove `.env` (`centralized.py`), and sent as `X-Shared-Secret`. | `SHARED_CONTAINER_SECRET` | Call the hub's admin/commerce endpoints (tier upgrades, `/api/admin/accounts`, registry writes) as a trusted service. | Scope the Cove→hub secret to only the endpoints a Cove legitimately needs; separate the registry-write capability from the tier/admin capability. `NEEDS-LIVE-CONFIRM` (which hub endpoints accept this secret in prod). |
| 7 | **Cloudflare DNS + Tunnel** (`cloudflare_dns.py`, `cloudflare_tunnel.py`, `reachability.py`) | The app container **when** a tunnel/DNS claim runs: `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` are read from env / `cove.yaml dns.token` and even set into `os.environ` (`centralized.py:208`). | Cloudflare API token | Edit the zone's DNS, open/point named tunnels → domain takeover, expose the home IP, MITM. | Do DNS/tunnel changes in a **separate short-lived provisioner step/container**, never leave the CF token in the long-running agent app env. **HIGH.** `NEEDS-LIVE-CONFIRM` (whether the token persists in the running app env vs claim-time only). |
| 8 | **Matrix / Dendrite** (`dendrite:8008`, admin API) | The app container over the bridge; Dendrite DB creds (`connection_string`) are in the Dendrite config, not the app. | Dendrite admin (shared-secret registration / admin API) | Register/deactivate users, read room state across the Cove's homeserver. | Confirm the Dendrite admin API isn't reachable unauthenticated from the app network; gate registration. `NEEDS-LIVE-CONFIRM` (admin API bind + auth). |
| 9 | **acme-dns cert issuance** (`LP_ACMEDNS_URL`, acme-dns update creds) | The app / Caddy path for DNS-01. | acme-dns update credential (scoped subdomain) | Issue certs for the Cove's own `*.lucidcove.org` subdomain. Blast radius is bounded to that subdomain by design. | Confirm the credential is per-Cove-scoped (it should be); low priority. `NEEDS-LIVE-CONFIRM`. |
| 10 | **Shared Caddy conf.d (rw mount)** — `~/.lucidcove/caddy/conf.d:/app/shared-caddy-confd` (multi-Cove-per-box) | The app container writes its own routing snippet here. | None (filesystem) | On a shared box, the mount is the **shared** Caddy's conf.d — a compromised Cove app can write a snippet that reroutes **another Cove's** hostnames. **Cross-Cove.** | Constrain each Cove to writing only `conf.d/{own_cid}.caddy` (validate on write / per-Cove subdir + import filter). **HIGH** on shared boxes. `NEEDS-LIVE-CONFIRM` (single-Cove boxes don't mount this). |
| 11 | **Bundled Caddy docker dir (rw mount)** — `./docker:/app/cove-docker` (single-Cove) | The app container rewrites its own `Caddyfile` on address change. | None (filesystem) | Persist a malicious routing/TLS config that survives restart (the app re-renders the Caddyfile). Bounded to this one Cove. | Write-validate the rendered Caddyfile; or render via the admin API only (no rw disk mount). MEDIUM. |
| 12 | **cove-core source** — `/cove-core:ro` | The app container (read-only). | None | Read-only source visibility — no write, low risk (info only). | None needed. |
| 13 | **GitHub push** (`cove-agent-push` PAT, per-role) | Team-agent dev tools. | Non-admin fine-grained PAT (Contents + PR write) | Push branches / open PRs on the per-role repos. `main` is branch-protected, so **no merge** — the gate is GitHub-enforced. Blast radius bounded by design (the locked agent-github-scope decision). | Keep the PAT non-admin + per-role; confirm it isn't broadened. LOW by design. `NEEDS-LIVE-CONFIRM` (PAT scope on the live box). |
| 14 | **Voice / pipecat** (`{cid}-voice:8300`, `PIPECAT_INTERNAL_SECRET`) | The app container. | `PIPECAT_INTERNAL_SECRET` | Drive the voice/ASR service; pipecat is a stateless processor (pulls per-request creds), so limited standing data. | Confirm the internal secret gates the voice endpoints. LOW–MEDIUM. `NEEDS-LIVE-CONFIRM`. |

---

## What is NOT reachable (deliberate boundaries — good)

- **The Docker socket is never mounted** into the app (or any) container
  (`grep` across `provision/`, `docker/`, `src/` finds only comments *reinforcing*
  this, e.g. `set_domain.py`: "must not hold the docker socket"). So an agent
  cannot escape to host container control. This is the single most important
  boundary and it holds.
- Address-claim runs **in-app over the admin API**, not via a host command or the
  docker socket (`centralized.py` comment: "no docker socket, no host command").

---

## Highest-priority follow-ups (each = its own ticket)

1. **NC admin pw out of the agent app env** (row 3) — service account + vault.
2. **Cloudflare token out of the long-running app env** (row 7) — provisioner-only.
3. **Cross-Cove conf.d write confinement on shared boxes** (row 10).
4. **BYOK key encryption + provider brokering** (rows 4–5; ties to #121).
5. **Ollama admin lockdown** (row 2) — token/private-net.
6. **Least-privilege DB role for agent paths** (row 4).

## Rows to confirm on a live box (then convert to tickets)

Ollama host binding + neighbors (2) · CF token persistence in app env (7) ·
Dendrite admin API auth/bind (8) · acme-dns credential scope (9) · shared conf.d
mount presence per topology (10) · `cove-agent-push` PAT scope (13) · pipecat
secret enforcement (14) · which hub endpoints honor `SHARED_CONTAINER_SECRET` (6).

*Static analysis only — generated for #D34, batch-2. No fixes applied.*
