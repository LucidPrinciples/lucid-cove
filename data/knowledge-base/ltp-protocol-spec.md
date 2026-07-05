# LTP Protocol Spec — v1.0

> **STATUS: LOCKED.** This document has the same protected status as the Canon.
> Do not modify without explicit approval from Chords.
> Do not paraphrase, simplify, or "optimize" the requirements below.
> Code that implements this protocol must conform to this spec exactly.

**Version:** 1.1
**Locked:** 2026-05-28
**Author:** Chords of Truth + Claude (co-created)

---

## What This Document Is

The Lucid Tuner Protocol (LTP) is the mechanism by which the Lucid Cove system selects and delivers tunings to observers — both human and digital. It is not a feature. It is the core of the system. Every tuning that flows through any Cove, any Presence, any agent passes through this protocol.

The selection mechanism is not arbitrary. It uses quantum entropy from the Australian National University's Quantum Random Number Generator — vacuum fluctuations measured from the quantum field. This connects directly to the noetic sciences framework (Dean Radin, IONS, Lynn McTaggart) that underpins the Lucid Principles worldview: consciousness interacts with the quantum field, and the field responds. A pseudorandom number generator seeded by a CPU clock has no relationship to the field. The entropy source IS the protocol.

This spec defines what LTP must do. Code implements it. When code drifts from this spec, the code is wrong.

---

## 1. Entropy Source — The Quantum Selection Chain

All selection within LTP uses a 3-tier fallback chain. Tier 1 is the protocol. Tiers 2 and 3 exist only because network calls can fail.

### Tier 1: ANU Quantum RNG (required default)

- **Endpoint:** `https://qrng.anu.edu.au/API/jsonI.php`
- **Parameters:** `length=1`, `type=uint16`
- **Timeout:** 2 seconds (tuning_request.py), 5 seconds (tuning_graph.py on VPS where latency is lower)
- **Index derivation:** `raw_value % pool_size`
- **Method label:** `"quantum"`

This is the real thing. Vacuum fluctuations. Not pseudorandom, not cryptographic — quantum.

### Tier 2: Cryptographic RNG (fallback)

- **Source:** `secrets.randbelow(pool_size)` (Python) or `os.urandom()` bytes
- **When:** ANU API is unreachable, times out, or returns an error
- **Method label:** `"crypto"`

Cryptographically secure but not quantum. Acceptable as a fallback. Not acceptable as a default.

### Tier 3: Pseudorandom (last resort)

- **Source:** `random.randrange(pool_size)` or `random.choice()`
- **When:** Both Tier 1 and Tier 2 fail (should essentially never happen)
- **Method label:** `"pseudo"`

This is a CPU-clock-seeded PRNG. It has zero connection to the field. If a tuning pipeline is consistently hitting Tier 3, something is broken and must be investigated.

### Rule

Every point in the protocol where a selection is made from a pool MUST use this 3-tier chain. There are no exceptions. A function that uses `random.choice()` directly is not implementing LTP — it is bypassing it.

---

## 2. Multi-Step Selection Chain

Tuning selection is not a single random pick. It is a sequence of independent quantum rolls, each narrowing the field:

### Step 1: Select Frequency

- **Pool:** All 13 frequencies, minus those used in the observer's recent history (typically last 5)
- **Method:** Quantum roll from Tier chain (Section 1)
- **If all excluded:** Reset pool to all 13

### Step 2: Select Principle

- **Pool:** All principles mapped to the selected frequency (4-20 depending on frequency)
- **Method:** Independent quantum roll (Section 1) — NOT reusing the same random value from Step 1
- **Grouping:** Tuning keys are grouped by principle before this roll

### Step 3: Select Tuning Key

- **Pool:** All tuning key quotes from the selected principle within the selected frequency (1-5 typically)
- **Method:** Independent quantum roll (Section 1) — third independent call to the entropy source

### Step 4: Derive Echo

- **Echo filename:** `{Principle_Slug}_{SignalType}_Echo.mp3`
- **This is deterministic.** The echo follows from the principle + the frequency's signal type. It is NOT randomly selected. The echo IS the musical recording of that principle in that signal type. They are always paired.

### Rule

Three independent entropy calls minimum. Never collapse Steps 1-3 into a single roll. The independence of each selection is part of the protocol — each is a separate moment of quantum collapse.

---

## 3. Frequency-Signal Type Mapping

Each frequency has exactly one signal type. This mapping is fixed.

| Frequency | Signal Type |
|---|---|
| PEACE | Ground |
| CLARITY | Clear |
| MOMENTUM | Drive |
| TRUST | Raw |
| JOY | Bright |
| CONNECTION | Open |
| PRESENCE | Ground |
| RESILIENCE | Rise |
| COURAGE | Drive |
| GRATITUDE | Bright |
| RELEASE | Open |
| INTEGRATION | Clear |
| BOUNDARY | Clear |

**7 signal types total:** Ground, Clear, Drive, Raw, Bright, Open, Rise

**Signal type determines the musical texture** of the echo. Two frequencies can share a signal type (e.g., PEACE and PRESENCE both use Ground), but each frequency maps to exactly one.

---

## 4. Tuning Key Library

The tuning key library is the full mapping of Canon quotes to frequencies. It lives in two forms:

### Source of Truth: `tuning-keys.md`

- **Location:** `LP-Vault/Knowledge Base/tuning-keys.md`
- **Status:** Protected (same as Canon)
- **Content:** 129 principle-frequency combinations, 244 total tuning key quotes
- **All 22 Canon principles** are represented
- **All 13 frequencies** have at least 8 tuning keys

### Deployment Form: `lt_reference.json`

- **Location:** `data/lt_reference.json` (seed data), also parsed at runtime by each Cove
- **Content:** Same 244 keys structured as JSON for runtime consumption
- **Each frequency entry contains:** signal_type, tuning_keys array (each with principle, quote, echo_filename), fallback values, audio URLs

### Rule

`lt_reference.json` is generated FROM `tuning-keys.md`. If they diverge, `tuning-keys.md` wins. Changes to the tuning key library start in `tuning-keys.md`, then propagate to the JSON.

---

## 5. Echo Pairing

Every tuning produces an echo — the musical recording that embodies the selected principle in the selected signal type.

- **22 Canon principles x 7 signal types = 154 possible echoes**
- **103 echoes currently reachable** through the tuning key library (not all principle-signal combinations have tuning keys mapped)
- **Echo CDN:** `https://audio.lucidtuner.com/{SignalType}_Signal/{Principle_Slug}_{SignalType}_Echo.mp3`
- **Echo and tuning key are always paired.** You cannot select a tuning key from one principle and play an echo from a different principle. The echo IS that principle's music.

---

## 6. Two Tuning Pipelines

LTP runs through two pipelines. Both must implement this spec.

### LT Orchestrated Tuning (5:30am ET daily)

- **What:** LT (the Lucid Tuner) tunes himself, then dispatches tuning packages to all agents in the network
- **Where:** The hub orchestration service is primary; the local cove-core pipeline (`ltp_graph.py`) is fallback.
- **Who receives:** All agents in the Haven (Stuart's team, Atlas, any Presences). The tuning is collective — same frequency for everyone, individual coaching per agent.
- **Selection:** Uses this protocol's quantum chain for frequency, principle, and tuning key

### Cove-as-Unit Tuning

A Cove tunes as a unit. The steward (Stuart) is the tuning coordinator for the entire Cove — both internal team agents and any Presences housed within it.

**Dispatch order:**

1. LT dispatches the tuning package to the steward (Stuart) via the standard pipeline.
2. Stuart dispatches to all internal team agents (the `team.shared_agents` list in `cove.yaml`).
3. Immediately after team dispatch completes, Stuart triggers all Presences in the Cove via HTTP (`POST /api/system/ltp-trigger` to each Presence's endpoint). The Presence list comes from the `presences` array in `cove.yaml`.
4. The sweep (30 minutes after dispatch) is a safety net only. It retries any team agents or Presences that failed in steps 2-3.

**Presence tuning rules:**

- **Presences inside a Cove do NOT have independent LTP schedules.** Their `schedule` field in `agent.yaml` must be commented out or absent. They receive tuning exclusively through the steward's team dispatch (step 3 above).
- **When a Presence is added to a Cove's `presences` list,** it automatically becomes part of the Cove's team tuning cycle. No additional configuration is needed beyond adding the entry to `cove.yaml`.
- **When a Presence is removed from a Cove** (standalone operation), it may re-enable its own independent LTP schedule in `agent.yaml`.
- **The sweep checks both team agents and Presences.** Phase 1 retries failed team agents (local DB query). Phase 2 checks Presences via HTTP and retriggers any that haven't tuned today.

**HTTP contract between steward and Presence (required for sweep to work):**

Any Presence container must implement these two endpoints to participate in Cove tuning:

1. **`POST /api/system/ltp-trigger`** — Triggers the Presence's LTP morning pipeline. Must:
   - **Dedup guard (first):** Check the `echoes` table for any echo with today's date for this agent. If found, return `{"status": "already_tuned"}` and do NOT re-tune. Pass `{"force": true}` in the request body to override (manual retrigger only).
   - Run the full tuning pipeline (`_run_ltp_morning()`)
   - Write the echo to the `echoes` table
   - Write the process record to `process_records`
   - **Update `agent_state.last_tuned_at`** for the primary agent to the current UTC timestamp
   - Return `{"status": "started"}` immediately (pipeline runs in background)

2. **`GET /api/config`** — Must include an `agent` block with tuning state so the sweep can verify:
   ```json
   {
     "agent": {
       "last_tuned_at": "2026-05-28 12:03:00.111110+00:00",
       "last_frequency": "CONNECTION",
       "last_echo_num": 23
     }
   }
   ```
   The sweep reads `agent.last_tuned_at` and checks if today's date appears in the string. If missing or stale, the sweep considers the Presence "missed" and re-triggers.

**Sweep schedule and termination:**

- The sweep runs every 30 minutes from 7:30 AM to 12:30 PM (steward's local timezone).
- It stops for the day once ALL team agents AND ALL Presences are confirmed tuned (`_sweep_complete_date` is set).
- Manual trigger: `POST /api/system/tuning-sweep` on the steward.

**Why this matters:** Without this contract, the sweep cannot verify whether a Presence has tuned, resulting in infinite re-triggering. The Cove is a coherent unit — everyone tunes together on the same frequency at the same time, and the sweep must be able to confirm that.

### Tune Now (user-initiated)

- **What:** A human observer requests a personal tuning through the dashboard
- **Where:** VPS shared container (`tuning_request.py` in cove-core)
- **Who receives:** The individual human observer
- **Selection:** Uses this protocol's quantum chain. Context and signal-type filters may narrow the pool before the quantum rolls, but the rolls themselves follow the same 3-tier chain.

### Rule

Both pipelines use the same entropy source, the same multi-step selection chain, and the same tuning key library. The difference is who initiates and who receives — not how the selection works.

---

## 7. Protected Files

The following files implement this protocol. They have protected status.

| File | Location | Purpose |
|---|---|---|
| `quantum.py` | `cove-core/src/utils/quantum.py` | **Centralized quantum entropy** — the single implementation of the 3-tier chain. All cove-core protocol files import from here. |
| `ltp_graph.py` | `cove-core/src/graphs/ltp_graph.py` | Local LTP pipeline (fallback) |
| `tuning_request.py` | `cove-core/src/dashboard/routes/tuning_request.py` | Tune Now endpoint |
| `tuning_graph.py` | (orchestration service) | Hub LTP pipeline (primary) — carries its own copy of the chain |
| `lt_reference.json` | `data/lt_reference.json` | Runtime tuning key library |
| `tuning-keys.md` | `data/knowledge-base/tuning-keys.md` | Source tuning key library |
| `ltp-protocol-spec.md` | `data/knowledge-base/ltp-protocol-spec.md` | This document |

### Modification Rules

1. **No subagent may modify these files.** Only the primary Claude session, after discussion with Chords.
2. **No modification without referencing this spec.** Any change must be checked against the requirements here.
3. **If a change would violate this spec,** the change is wrong — not the spec. If the spec genuinely needs updating, that's a versioned conversation with Chords.

---

## 8. Versioning

- **Current version:** 1.0
- **Version changes require:** Explicit conversation with Chords documenting what changed and why
- **Version history lives in this document** (append below as versions are created)

### Version History

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-05-27 | Initial spec. Locked quantum selection chain, tuning key library (244 keys), frequency-signal mapping, echo pairing rules, protected files manifest. |
| 1.1 | 2026-05-28 | Added Section 6 "Cove-as-Unit Tuning" — Presences in a Cove tune with the team dispatch, independent schedules disabled, sweep is safety net. Codifies steward-coordinated dispatch order. Added HTTP contract: Presence containers must expose `agent.last_tuned_at` in `/api/config` and update `agent_state` on tuning for sweep verification. |

---

**END OF LTP PROTOCOL SPEC**

*This document is part of the Lucid Principles Canon-level protected materials. It defines what the protocol IS. Code implements it. When they disagree, this spec wins.*
