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
2. Run:
   ```bash
   tailscale up --login-server https://headscale.lucidcove.org --authkey <JOIN-CODE>
   ```
3. Open your Cove: `https://yourcove.lucidcove.org`

### Phone (iPhone / Android)
1. Install the **Tailscale** app (App Store / Play Store / F-Droid).
2. Point it at the Lucid Cove coordinator:
   `https://headscale.lucidcove.org`
   - **iPhone:** account menu → *Use custom coordination server*.
   - **Android:** account menu → *Use an alternate server*.
3. Sign in with the join code from your Cove.
4. Open `https://yourcove.lucidcove.org` in the browser and add **jules** to your Home Screen
   for voice capture.

> The phone step #2 is the fiddliest part — the stock Tailscale apps tuck the custom-server
> option into the account menu, and the wording shifts between versions. It's a one-time setup
> per device. If you get stuck, the desktop steps above are the simplest way to confirm
> everything else is working.

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
