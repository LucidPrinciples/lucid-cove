---
name: site-chunk-edit
description: Edit large personal-site HTML with small find/replace patches instead of full-file rewrites. Use when a page is too big to safely rewrite (~8k+), site_edit_file would dump the whole document, or a personal agent must change head/nav/sections without team escalation.
license: Apache-2.0
metadata:
  author: lucid-principles
  version: "1.0"
  ticket: SITECHUNK1
---

# Site chunk edit (personal / Tier B)

Surgical edits for presence-owned sites. Prefer **many small patches** over one giant rewrite so context limits, approval diffs, and isolation all stay sane.

## When to use

- The page is large (rough guide: **~8k characters or more**, or `site_read_file` returns a wall of HTML).
- You need a **local** change: title, meta, one section, a link, a class, nav item, footer line.
- You are a **personal agent** on a presence door (Tier B). Do not ambient-list or edit another presence’s sites.
- Full-site generation or brand-system work belongs on the **Cove steward + team** path with an **explicit** handoff — not silent cross-door discovery.

## Tools (in order)

| Goal | Tool |
|------|------|
| See tree | `site_list_files` |
| Read one file (or confirm anchors) | `site_read_file` |
| Change a known substring | `site_patch_file` ← **default** |
| Replace entire file (small files only) | `site_edit_file` |
| New file | `site_create_file` |
| Publish folder → live | `site_deploy` (after approved patches, when that is the product path) |

Every patch/edit still goes through **operator approval**. Chunking does not bypass Attention.

## Hard rules

1. **Patch-first.** If `site_patch_file` can do it, do not call `site_edit_file`.
2. **One logical change per patch** (or one tight group the operator can review as one intent). Prefer several approvals over one opaque full-file diff.
3. **Exact `find_text`.** Copy anchors from `site_read_file` — whitespace and quotes must match. If the tool says text not found, re-read; do not invent nearby HTML.
4. **Unique anchors.** If `find_text` appears more than once and you only want one site, widen the anchor (include a parent line or distinctive attribute) so you do not replace every occurrence by accident.
5. **No cross-presence browse.** Only domains the acting presence owns / has in Site Builder for this door. Steward help is **explicit escalate or operator handoff**, not “list everyone’s sites.”
6. **Do not rewrite the whole page** to change a paragraph. Split work:

## Chunk patterns

### A — Head / meta inject
1. `site_read_file` → capture the exact `<title>…</title>` or a unique `<meta …>` / `</head>` neighbor.
2. `site_patch_file` with that exact string → new title/meta block.
3. Stop. Approve. Re-read only if a second head change is needed.

### B — One section body
1. Find a stable open/close pair (`id="…"`, comment markers, or a unique heading + the following block).
2. Patch **only that section’s inner HTML** (or the whole section element if small).
3. Leave scripts, nav, and unrelated sections untouched.

### C — Nav / footer link
1. Read enough of the shared partial or layout to get one `<a …>…</a>` exactly.
2. Patch that anchor only.
3. If the same nav is duplicated in multiple files, patch **each file** with its own call — do not assume one file covers the site.

### D — Multi-spot same page
1. List the spots in chat order (top → bottom).
2. Patch spot 1 → wait for success/approval path as product requires → then spot 2.
3. After each patch, if the next `find_text` might have shifted, **re-read** before the next find/replace.

### E — File still too large to hold in one read
1. Prefer path knowledge from `site_list_files` + the smallest file that holds the target (partials, `includes/`, section files).
2. If the repo is one giant HTML: patch using the **smallest unique substring** you already know from the operator or a prior read; avoid emitting a full rewritten document.
3. If you cannot get a reliable anchor, **stop and ask the operator** for the exact snippet to find — do not dump a guessed full file.

## When full `site_edit_file` is OK

- File is **short** (comfortably under the ~8k guide) **and** the change is structural (many interdependent edits where patch thrash would be worse).
- Still describe the edit clearly for the approval card.
- Never use full edit as a shortcut because patch failed once — fix the anchor first.

## Escalation (isolation-safe)

| Situation | Do |
|-----------|-----|
| Minor copy/CSS/link on own site | Stay on presence door; this skill + `site_patch_file` |
| New multi-page build, design system, shared brand | Operator runs it through **Cove steward + team** (Tier A / main Cove), or explicitly invites help on this door |
| Another member asks “what sites does X have?” | Do **not** inventory other presences. Decline ambient discovery. |

Best practice from product intent: operator **uploads or scaffolds** the site into the presence Sites folder; the personal agent **maintains** with chunks; big greenfield builds use the Cove team on purpose.

## Done checklist

- [ ] Used patch for each local change (or justified a small full edit)
- [ ] Each `find_text` verified from a read or operator paste
- [ ] No other presence’s domain touched
- [ ] Operator still has an approval card per write
- [ ] Told the operator what changed and which file/path

## Anti-patterns

- Pasting an entire rewritten `index.html` through `site_edit_file` to fix one heading
- Broad `find_text` like `<div>` or `class="btn"` that smashes the page
- Silent steward/team reach-across to “just check” another member’s Site Builder
- Claiming deploy finished when only a branch + approval request exists
