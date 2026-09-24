# Tool PWA icons (Add to Home Screen)

Every first-class Cove tool that people may pin to a phone home screen needs
its **own** icon and short name — not the Lucid Cove mark. MC keeps the LC
mark on purpose; tools should read as distinct apps beside it (jules already
does this with Julian’s avatar).

## Reference implementations

| Tool    | Title meta                         | Apple touch icon              | Manifest                         | Icons |
|---------|------------------------------------|-------------------------------|----------------------------------|-------|
| Mission Control | (MC)                        | `/static/apple-touch-icon.png`| `/static/manifest.json`          | LC `icon-192` / `icon-512` |
| jules   | `apple-mobile-web-app-title: jules`| `/static/julian-icon.png`     | (apple tags only today)          | `julian-icon.png` (180), `julian-icon-512.png` |
| Backlog | `apple-mobile-web-app-title: Backlog` | `/static/backlog-icon.png` | `/backlog-manifest.webmanifest`  | `backlog-icon.png` (180), `-192`, `-512` |

## Checklist for a new tool

When a team member ships a pin-worthy tool (`/tools/{name}` or top-level route):

1. **Short name** — what Safari shows under the icon (≤12 chars ideal).
2. **Dedicated icon assets** under `src/dashboard/static/`:
   - `{tool}-icon.png` — 180×180 (apple-touch-icon)
   - `{tool}-icon-192.png` — 192×192 (manifest)
   - `{tool}-icon-512.png` — 512×512 (manifest)
3. **HTML head** on the tool page:
   - `apple-mobile-web-app-capable`
   - `apple-mobile-web-app-title` = short name
   - `apple-touch-icon` → `{tool}-icon.png`
   - `rel=icon` → same (not LC mark)
   - `rel=manifest` → dedicated manifest route if you want Android/Chrome parity
4. **Manifest route** (recommended): `/{tool}-manifest.webmanifest` with
   `short_name`, `start_url`, `display: standalone`, and the 192/512 icons.
   Do **not** reuse `/static/manifest.json` or `/static/icon-192.png`.
5. **Close affordance** — if opened from Links, stamp `?return=links` and make
   × navigate to `/?tab=ab-links` (see backlog/jules close scripts +
   `_abLinksWithReturn` in `action-board.js`).
6. **Links default** — add a leaf card in `_default_links()` only if every Cove
   should start with it.

## Icon design notes

- **Person-shaped tools** (jules, a steward desk): use the agent avatar, cropped
  square on a near-black field — same treatment as `julian-icon*.png`.
- **Board/utility tools** (Backlog, future queues): a simple mark that reads at
  29pt (home-screen size). Dark field `#0a0a0f`, one strong accent, no tiny
  text. Prefer a unique silhouette over the LC hex mark.
- Keep padding: iOS masks icons; important content stays inside ~80% center.
- Export real PNGs into git (not generated at runtime) so deploys stay static.

## Process owners

- Shipping the tool page: the agent building the tool (usually Archimedes /
  presence builder).
- Icon + manifest checklist: part of the same PR — not a follow-up.
- Steward review: does Add to Home Screen show the right name **and** icon on
  Safari iOS before the PR is called done.

## Not every tool needs a shortcut

Pin when the operator will open it from the home screen several times a week.
Deep links that stay inside MC Links only need a Links card, not a PWA icon.
