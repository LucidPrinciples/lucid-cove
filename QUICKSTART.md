# Quickstart — Self-Hosting a Cove

From a clean clone to a running Cove with a setup wizard. Budget ~15 minutes plus image build time.

A "Cove" is a self-contained home for you (or your family) and your intelligence agents: a Mission Control dashboard, a steward agent, files (Nextcloud), voice capture (jules), and the Lucid Tuning Protocol. The repo is the shared base — one generic image, all behavior from config. The **provisioner** generates your Cove's stack and prints a **claim link**; you finish setup in the browser (name → address → intelligence → device).

## Prerequisites

- Docker + Docker Compose v2 (the `docker compose` plugin — Docker Desktop includes it)
- `curl` and `lsof` (the installer checks; most systems have them)
- A model provider — a local [Ollama](https://ollama.com) (no key, fully self-hosted) or an API key (OpenRouter, OpenAI, Google, Groq)
- ~15 GB free disk for the first run (app, voice, Nextcloud, Matrix images + voice models); more for your data
- Ports 80 and 443 free (the shared Caddy owns them for every Cove on the box)
- Optional: a domain you control (recommended), or use a free `lucidcove.org` subdomain

## 1. Clone

```bash
git clone https://github.com/LucidPrinciples/lucid-cove-core.git cove-core
cd cove-core
```

## 2. Write your config

Create `cove.config.yaml` (the only thing you edit). The minimum:

```yaml
cove:
  id: smith                      # a short slug
  name: Smith                    # your Cove (family) name
  domain: cove.yourdomain.com    # your address — or "" to set it in the wizard
operator:
  name: Your Name
  handle: yourhandle             # your permanent @handle
  email: you@example.com
  token: ""                      # optional: your token from app.lucidcove.org
                                 # (Settings → Tools → "Get my config") to join the network
team: on
model_providers: []              # leave empty — connect a model in the wizard
deploy:
  target: standalone
  cove_core_path: /absolute/path/to/this/clone
matrix:
  enabled: true
compute:
  voice:
    mode: local                  # CPU voice (jules), no GPU needed
ltp:
  dry_run: true
```

**Address options:**
- **Your own domain (recommended).** Set `cove.domain` and, if your box is behind NAT, add your DNS token so HTTPS is automatic without opening ports:
  ```yaml
  dns:
    provider: cloudflare
    token: YOUR_CLOUDFLARE_TOKEN
  ```
  Create one DNS record: `cove.yourdomain.com` → your box's IP (public, or mesh IP if private).
- **A lucidcove.org subdomain.** Leave `cove.domain: ""` and claim it in the wizard (we provision the DNS + cert).
- **Just trying it locally?** Leave `cove.domain: ""`. The Cove runs at `http://localhost:8200` — a secure context, so voice works too.

## 3. Generate + start

```bash
python3 provision/centralized.py cove.config.yaml --output ./out
cd out/smith-cove && docker compose up -d --build
```

The provisioner prints a **claim link**. The build includes the app, Postgres, Nextcloud, Redis, your Matrix homeserver, CPU voice, and (with a domain) a bundled Caddy for HTTPS.

## 4. Claim + finish in the browser

Open the claim link (`http://localhost:8200/p/...`, or `https://your-domain/p/...`). The wizard:

1. **Confirms you** (name + @handle, locked).
2. **Set your address** — if you didn't set `cove.domain`, do it here.
3. **Add intelligence** — your provider + key (or Ollama). This switches on your agent + tools.
4. **Get it on your phone** — a one-time mesh join code; install Tailscale on your phone, then open your Cove's URL and add jules to your Home Screen.

Then you're in Mission Control: chat, jules voice capture → your Inbox, the Backlog, files, and tuning. Change the address or model anytime in **Settings**.

## Notes

- The container carries no source — cove-core is mounted from the repo and merged at runtime. `git pull` + recreate the container to update.
- This is pre-release software. Expect rough edges and changing config.

## License

Code: Apache 2.0 (see `LICENSE`). Canon framework content: CC BY 4.0. See `NOTICE`.
