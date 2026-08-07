---
name: host-hands
description: When work needs the host, a sibling Cove, hub/VPS (LT/Socrates), live docker/git/logs, or anything outside this container — emit Mac-safe copy-paste commands on the first reply, never stop at cannot-access, never nested heredocs. Use for founder/sibling diagnosis, VPS gather packs, deploys, and paste-back loops.
license: Apache-2.0
metadata:
  author: lucid-principles
  version: "1.0"
  domain: ops
---

# Host-hands

You are fenced inside a container. The operator's machine (or SSH to mesh/LAN hosts) is the only way to reach the host docker daemon, sibling stacks, and hub/VPS. **That is the product process — not a failure.**

## First reply (mandatory)

When the operator asks about anything outside your mounts:

1. One short sentence: host-hands — they run, you interpret paste-back.
2. **Immediately** emit copy-paste command blocks (gather first, fix later).
3. Stop inventing live state. Wait for paste-back, then continue.

Keep the prose short: outcome + commands + paste-back. No multi-page explainers.

Never: "I can't access that from my container" as the whole answer.  
Never: wait for them to remind you of this loop.  
Never: three-turn thrash before the first usable command.

## Mac-safe command rules (non-negotiable)

Paste from chat into Terminal on macOS **dies** on nested heredocs and nested quotes. Follow these:

| Do | Don't |
|----|--------|
| `ssh user@host "cmd1 && cmd2"` one line | `ssh user@host <<'EOF'` … `EOF` |
| Several separate fenced one-liners | One giant remote script with heredoc inside |
| `docker logs NAME --since 2h 2>&1 \| tail -80` | Multi-line `python -c '…'` over SSH |
| `docker exec NAME sh -c 'simple one-liner'` | Nested `bash -c` with mixed `'` / `"` |
| `git -C /path log -1 --oneline` | `cat <<EOF` then remote python |
| Plain `grep` / `tail` / `wc -l` | Here-docs wrapping `docker exec` |

**Quote rule:** prefer double quotes around the remote command and single quotes only for short inner shell when needed. If quoting gets hard, **split into two ssh one-liners** instead of nesting.

**Markers:** use a clear fence per command:

````text
```bash
ssh user@host "docker ps --format 'table {{.Names}}\t{{.Status}}' | head -40"
```
````

## Where names come from

1. `AgentSkills/Ops/reference.md` (or this Cove's Ops cookbook) — **preferred**
2. Operator's spoken host / container names this turn
3. Placeholders below — **never invent** production names when Ops is available

Fill `USER@HOST` and container/path tokens from Ops. Common Lucid-dev shapes (only when Ops agrees they are live):

| Target | Typical SSH | Typical app container |
|--------|-------------|------------------------|
| P620 / homebase Coves | `lphomebase@lp-homebase.mesh.lucidcove.org` (MESH) or LAN IP from Ops | e.g. `clearfield-app`, `lucidcove-6f6f-app` |
| Hub / LT / Socrates | `root@vps.mesh.lucidcove.org` | e.g. `socrates-lg-app` |

If Ops is stale, first command is discovery (`docker ps`), not a guessed path.

## Gather pack shapes (copy and fill)

### A — What's running on a host

```bash
ssh USER@HOST "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' | head -50"
```

### B — App logs (last hours)

```bash
ssh USER@HOST "docker logs CONTAINER --since 2h 2>&1 | tail -100"
```

### C — Grep errors (still one line)

```bash
ssh USER@HOST "docker logs CONTAINER --since 6h 2>&1 | grep -iE 'error|traceback|exception|failed' | tail -60"
```

### D — Code revision on disk

```bash
ssh USER@HOST "git -C CLONE_DIR log -1 --oneline && git -C CLONE_DIR status -sb"
```

### E — Sibling / founder-style stack (same host, different container)

```bash
ssh USER@HOST "docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'NAME_FRAGMENT|NAMES'"
```

```bash
ssh USER@HOST "docker logs SIBLING_APP --since 2h 2>&1 | tail -100"
```

```bash
ssh USER@HOST "git -C SIBLING_CLONE log -1 --oneline"
```

### F — Hub / VPS (LT, Socrates, noon, X)

```bash
ssh USER@HOST "docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -iE 'socrates|lt|NAMES'"
```

```bash
ssh USER@HOST "docker logs HUB_APP --since 6h 2>&1 | grep -iE 'noon|x_post|error|traceback' | tail -80"
```

```bash
ssh USER@HOST "git -C HUB_CLONE log -1 --oneline && git -C HUB_CLONE status -sb"
```

### G — Simple in-container check (no nested script)

```bash
ssh USER@HOST "docker exec CONTAINER printenv HOSTNAME"
```

```bash
ssh USER@HOST "docker exec CONTAINER ls -la /app/src 2>&1 | head -20"
```

## Deploy / fix shapes (after diagnosis + approval)

Code-only pull + restart (when that is the live deploy path for that stack):

```bash
ssh USER@HOST "cd CLONE_DIR && git pull --ff-only origin main && docker restart CONTAINER"
```

Image rebuild path (new deps) — only when Ops/runbook says so; keep each step a one-liner or a short `&&` chain, still **no heredoc**.

## After paste-back

1. Read the output; say what is true now.
2. Next command or a code/PR change inside *this* Cove's repos.
3. If deploy is needed on another stack, give the next host-hands one-liner (approval rules still apply for restarts/secrets).

## Anti-patterns (this is the thrash we are killing)

- Stopping at "can't access from container"
- Waiting for the operator to teach host-hands again
- Nested heredoc SSH "helper scripts"
- Multi-line remote Python to print what `docker logs | tail` already can
- Inventing container names or paths when discovery is one `docker ps` away
- Claiming live founder/VPS state from memory without paste-back

## Process checklist

1. Is the answer off-container? → host-hands this turn.
2. Ops/reference for USER@HOST and names?
3. Mac-safe one-liners only?
4. Gather before fix?
5. Paste-back → interpret → next step?
