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

## Disconnect / troubleshoot
- A join code is single-use and expires (~1 hour) — generate a new one anytime from your Cove.
- Disconnect a device: `tailscale down` (computer) or toggle off in the app (phone).
- Check the coordinator is reachable: open `https://headscale.lucidcove.org/health`.
- **Address claimed but HTTPS never issues** (browser warning, or your subdomains time out):
  your box gets its certificate via DNS-01 through the hub's challenge DNS, which must be
  reachable on **port 53** from the public internet. Test from any machine:
  `dig +short acme.lucidcove.org @31.97.7.72` — a timeout means the hub side (or a firewall
  in front of it) is blocking DNS, not your box. Your Cove's Caddy keeps retrying and picks
  up automatically once it clears. (Hub operators: allow TCP **and** UDP 53 to the acme-dns
  container — cloud-provider firewalls often block them by default.)
