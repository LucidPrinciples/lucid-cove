# Reaching your Cove from other devices (the private network)

Your Lucid Cove is **private** — it isn't published to the open internet, there are no ports
to forward, and nothing is exposed for strangers to find. Instead, your Cove and the people
you invite all join one small private network (a "mesh"). Once a device is on the mesh it
can reach your Cove from anywhere — home, work, a phone on cellular — with real HTTPS, as if
it were on your home wifi.

The mesh uses **Tailscale** (the app each device installs) pointed at **Lucid Cove's
coordinator**, so you don't depend on anyone else's cloud or account.

> You only need this to reach the Cove from *other* devices. On the computer it's installed
> on, `http://localhost` already works — including the microphone and voice.

---

## 1. Connect your Cove's box

After you finish the setup wizard, get a one-time join code in your Cove
(**Start Here → Connect → Get join code**), then on the box run the helper that shipped in
your install folder — it joins the mesh **and** points your address at the box for you:

```bash
bash connect-mesh.sh <YOUR-JOIN-KEY>
```

(If Tailscale isn't installed it'll tell you where to get it, then re-run.) Then, in Mission
Control, **Claim your address** (`yourcove.lucidcove.org` or your own domain) — it points at
the box's private mesh address, so only people on your mesh can reach it.

---

## 2. Connect each person's devices

Everyone you invite does this once per device: install Tailscale, point it at the Lucid Cove
coordinator, and sign in with a one-time join code from your Cove (**Start Here → Connect →
join code**, good for ~1 hour).

### Computer (Mac / Windows / Linux)
1. Install Tailscale: https://tailscale.com/download
2. Run (add a clear `--hostname` so the mesh list stays readable — **#MESH-NAME**):
   ```bash
   tailscale up --login-server https://headscale.lucidcove.org --authkey <JOIN-CODE> --accept-dns=true --hostname my-laptop
   ```
   Letters, numbers, hyphens only. Skip names like `localhost`, `invalid-…`, or random OS defaults.
3. Open your Cove: `https://yourcove.lucidcove.org`
4. Rename later if needed: `tailscale set --hostname my-laptop`

### Phone (iPhone / Android)
1. Install the **Tailscale** app (App Store / Play Store / F-Droid).
2. **QR (preferred, #MESH2 + #MESH5):** in Mission Control open **Start Here → Connect on mobile**
   or **Settings → Devices → Get join code**, then scan the QR with the phone camera. It opens a
   short **public** `/mesh-join` page on the Lucid Cove hub (`app.lucidcove.org`) — not your
   mesh-only Cove address — so the walkthrough loads **before** the phone is on the mesh. The
   page shows the coordinator + join code and, when known, an “Open your Cove” link to use
   **only after** Tailscale shows Connected. This is **not** Nextcloud's app-login QR and
   **not** a native Tailscale deep link.
3. Or by hand: point Tailscale at the Lucid Cove coordinator
   `https://headscale.lucidcove.org`
   - **iPhone:** account menu → *Use a custom coordination server*.
   - **Android:** account menu → *Use an alternate server*.
   Then sign in with the join code from your Cove.
4. Wait until Tailscale shows **Connected**, then open `https://yourcove.lucidcove.org` (or the
   button on the join page) and add **jules** to your Home Screen for voice capture. Leave
   MagicDNS / accept DNS on when asked. Opening the Cove URL before Connected will fail — that
   address is private mesh-only by design.
5. **Name the phone (#MESH-NAME).** Junk mesh names (`localhost-…`, `invalid-…`) are hard to
   manage. Set a clear device name in phone settings before join when you can. On a computer
   already on the mesh: `tailscale set --hostname jade-iphone`.

> Custom-server setup is still the fiddliest part of the stock Tailscale apps (wording shifts
> between versions). The QR path reduces typing; if a "open Tailscale" button on the join page
> does nothing, use the on-page coordinator + code. Desktop steps above still confirm the rest.

---

## What being on the mesh gets you
- Reach your Cove from anywhere, behind any router or carrier network — no port forwarding.
- Real HTTPS, so the microphone and voice work.
- Nothing about your Cove is exposed to the public internet.


## Address claimed but the browser says NXDOMAIN / "can't find the server"

Your Cove's public DNS points at a **private mesh IP** (`100.64.0.0/10`). That is intentional
(nothing is exposed on the open internet). Some Mac/phone resolvers and "secure DNS" / ad-block
products **filter those answers** (DNS rebinding protection). Symptoms:

- Cloudflare / DoH lookup shows `yourcove.lucidcove.org → 100.64.0.x` (and `matrix.yourcove…`)
- `curl` / Chrome on the box still say **Could not resolve host** / NXDOMAIN
- Cove UI may load after an apex pin while **Connect** still fails with `ERR_NAME_NOT_RESOLVED` for `matrix.yourcove…` — both names need to resolve
- The Cove is fine on the mesh; only **name resolution on that device** is broken

**On the Cove host** (after claim), re-run the host command from Set Address — current
`set_domain.py` verifies resolve and repairs it (Tailscale DNS + scoped `/etc/hosts` pin when
needed). Or by hand:

```bash
tailscale set --accept-dns=true   # or sudo
# if still broken (host only) — pin BOTH names (Connect uses matrix.*):
echo '100.64.0.X yourcove.lucidcove.org' | sudo tee -a /etc/hosts   # use your mesh IP
echo '100.64.0.X matrix.yourcove.lucidcove.org' | sudo tee -a /etc/hosts
sudo dscacheutil -flushcache 2>/dev/null; sudo killall -HUP mDNSResponder 2>/dev/null
curl -vI https://yourcove.lucidcove.org/
curl -sS -o /dev/null -w "%{http_code}\n" https://matrix.yourcove.lucidcove.org/_matrix/client/versions
```

**Other devices** must be on the mesh (section 2) and use Tailscale DNS / not block `100.64/10`.
This is not "wait for Cloudflare" when public DoH already has the record.

### Name resolves but browser says ERR_SSL_PROTOCOL_ERROR

Different problem: DNS is fine; **HTTPS is still issuing**. After Set Address / the host
command, Caddy reloads immediately and host resolve can already succeed while the ACME
cert is mid-flight (often 30–90s). Chrome then shows *"This site can't provide a secure
connection"* / `ERR_SSL_PROTOCOL_ERROR` on the first Open my Cove tab. Wait and **Reload** —
or check on the host:

```bash
curl -vI https://yourcove.lucidcove.org/
# look for HTTP/2 200 or a real TLS handshake; retry until it stops SSL-erroring
```

That is expected on first open, not a dead address. The product UI warns on the sign-on
door for the same reason.



## Family node durability (do not silently expire)

Join **codes** expire (~1 hour, single-use) — that is intentional. **Devices that have
already joined** should not silently drop off the mesh and force a re-auth.

Headscale defaults can age out unused node keys. For a family Cove we want durable
nodes:

1. Prefer non-ephemeral pre-auth keys when minting device joins (`ephemeral: false` —
   already the path in `provision/mesh.py` for both the HTTP API and CLI mint).
2. On the coordinator, keep family nodes from expiring. With Headscale CLI (on the
   VPS that runs the `headscale` container), list nodes and disable key expiry for
   each family device after first join:

   ```bash
   docker exec headscale headscale nodes list
   docker exec headscale headscale nodes expire --identifier <NODE_ID> --expiry 0
   # If your Headscale version uses a different flag, use the equivalent
   # "disable key expiry" / "set expiry far in the future" command from
   # `headscale nodes --help` for that version.
   ```

3. Document for operators: if a device that used to work suddenly cannot resolve
   `*.lucidcove.org` mesh names, check `tailscale status` first. If the node is gone
   from the coordinator, mint a fresh join code from the Cove (**Connect this
   computer** / Settings → Devices) and re-join with `--accept-dns=true`.

Product path still mints short-lived **join codes** only; durability is about the
**registered node**, not the one-time key.

## Device names on the mesh (#MESH-NAME)

Headscale lists every joined node by hostname. Phones and fresh OS installs often land as
`localhost-…` or `invalid-…`, which makes family device lists unusable.

**At join (preferred)**
- Desktop/server join command from Mission Control may include `--hostname <clean-name>` when
  this machine already has a non-junk hostname; otherwise add one yourself.
- Cove host helper: `bash connect-mesh.sh <join-key> [hostname]` (optional second arg; otherwise
  uses `COVE_ID` from `.env` or a clean short hostname).
- Phone QR `/mesh-join` page reminds you to set a clear system name and documents rename.

**After join**
```bash
tailscale set --hostname your-device-name
```

Coordinator-side rename (operator on VPS, when a node already registered wrong) is still
available via `headscale nodes rename` for your Headscale version — prefer client-side
`tailscale set --hostname` when the device is online.

## Disconnect / troubleshoot
- A join code is single-use and expires (~1 hour) — generate a new one anytime from your Cove.
- Disconnect a device: `tailscale down` (computer) or toggle off in the app (phone).
- Check the coordinator is reachable: open `https://headscale.lucidcove.org/health`.
- **Address claimed but HTTPS never issues** (browser warning, or your subdomains time out):
  your box gets its certificate via DNS-01 through the hub's challenge DNS, which must be
  reachable on **port 53** from the public internet. Test from any machine:
  `dig +short acme.lucidcove.org @<hub public IP>` — a timeout means the hub side (or a firewall
  in front of it) is blocking DNS, not your box. Your Cove's Caddy keeps retrying and picks
  up automatically once it clears. (Hub operators: allow TCP **and** UDP 53 to the acme-dns
  container — cloud-provider firewalls often block them by default.)


## Host reachability (optional, MESH3 L2)

Your Cove stays **private on the mesh** either way. When the *host machine* is easy
for other devices to punch a direct path to, pages feel snappier; when it is not,
traffic can still flow through Lucid Cove's own relay (MESH3 L1).

Mission Control may show an Attention card **"Make this Cove easier to reach"** after
basic setup — only if a check has never been run, or the last check said the host is
hard to reach. It never opens the Cove to the public internet.

On the Cove machine:

```bash
# Attention prints the absolute paths for this install when it can resolve them, e.g.:
bash /path/to/clone/scripts/probe-host-reachability.sh \
  --out /path/to/out/your-cove/config/host_reachability.json
```

(The card shows the exact host paths for your install — not a cwd-relative guess.) If the report says hard to reach:
enable UPnP/NAT-PMP on the router, **or** DHCP-reserve the box and forward **UDP 41641**
to it, then re-run the probe. Skip the card anytime.

