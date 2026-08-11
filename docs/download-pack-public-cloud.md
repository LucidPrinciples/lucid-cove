# Download pack and public Cloud egress

## Product outcome

Mission Control **Download pack** lists publish-ready files (newest first), tracks last pull, and hands the browser **short-lived, read-only, per-file** Nextcloud public-link download URLs. Bytes go **browser → public Cloud origin** (not Mission Control proxy) so multi‑GB pulls work the same on home hosts and VPS.

## Security

- Nextcloud **login and folder browsing stay authenticated**. Publishing a hostname does not open the whole tree.
- Pack links are **shareType public link, permissions read-only**, with **expiry** (API default ~1 day, capped at 14).
- Anyone with a live link can fetch **that file** until expiry or revoke — treat links like temporary tickets.
- Do not use world-readable shares on parent folders of all video for “speed.”

## URL contract

| Source | Role |
|--------|------|
| `NEXTCLOUD_URL` | In-network API/WebDAV (containers). |
| `NEXTCLOUD_PUBLIC_URL` | **Browser-facing** origin for share/download links when set to a non-loopback URL. **Wins** over derived `https://cloud.{domain}`. |
| `https://cloud.{domain}` | Default when no explicit public URL (mesh or full public Cove). |

Self-host pattern: keep mesh DNS on `cloud.{domain}` for LAN/mesh UI; set `NEXTCLOUD_PUBLIC_URL=https://files.example.org` (or similar) once that name is on a Cloudflare Tunnel to local Nextcloud.

## Enabling public bulk egress (self-host)

On the host that already runs `cloudflared` for the Cove (or a shared tunnel):

```bash
python3 provision/enable_public_cloud.py \
  --hostname files.example.org \
  --tunnel-id <tunnel-uuid> \
  --service http://127.0.0.1:<nextcloud-host-port>
```

Then set on the **app** container and restart:

```bash
NEXTCLOUD_PUBLIC_URL=https://files.example.org
```

Prefer a **dedicated** `files.*` (or similar) one-label hostname under the zone for Universal SSL. Merging via `ensure_public_hostname` does **not** wipe other tunnel routes (e.g. analytics).

Never paste API or tunnel tokens into chat logs.

## Operator check

1. `https://files…/` shows Nextcloud login (or redirect), not a dead mesh/502 path.
2. Download pack → Download next only → browser shelf shows multi‑MB/s on a good remote link (bounded by house upload).
3. If links still point at mesh-only hosts, confirm `NEXTCLOUD_PUBLIC_URL` is set on the running app and `/api/config` exposes `nextcloud_public_url` accordingly.

## Cloudflare tunnel transport (HTTP/2 default)

Self-host bulk egress (Download pack via a public `files.*` hostname) depends on a healthy
`cloudflared` connector. Cloudflare's default edge transport is **QUIC** (UDP). On many
dual-stack home hosts QUIC fails (`network is unreachable` on UDP/7844) and public file
downloads crawl at ~KB/s even when host uplink to Cloudflare is multi‑MB/s.

**Product default for Cove connectors is HTTP/2 (TCP):**

- `provision/enable_tunnel.py` starts cloudflared with `--protocol http2` and
  `TUNNEL_TRANSPORT_PROTOCOL=http2`.
- `docker/cloudflared.compose.fragment.yml` defaults the same for compose-managed connectors.
- Existing connectors: on the host,
  `python3 provision/pin_cloudflared_http2.py --name <cove-id>-cloudflared`
  (does not print tokens).

Override only after measuring: `CLOUDFLARED_PROTOCOL=quic` or
`TUNNEL_TRANSPORT_PROTOCOL=quic`.

Pair with `NEXTCLOUD_PUBLIC_URL=https://files.<zone>` after
`provision/enable_public_cloud.py` (or equivalent Zero Trust public hostname → local Nextcloud).
