# LP Color System

Unified color language across all Lucid Principles systems. Every color derives from a Broadcast Frequency. Same color = same energy = same meaning, whether in the Lucid Tuner app, Mission Control dashboards, books, course material, or auto-generated media.

Created: 2026-05-08
Last updated: 2026-05-09 (Abundance removed, Drive→Integration, Mercer→Gratitude, --green→Integration teal, daily frequency coloring added)

---

## Design Principle

Color is not decoration. It is part of the coherent language of the Lucid Principles framework. Colors map to frequencies, frequencies map to energetic qualities, and those qualities are felt the same way everywhere they appear. Over time, an observer learns to read color the way they read words. Pattern recognition becomes emotional and energetic recognition. This is intentional.

---

## Source of Truth

**Code:** `LP` object in `cove-core/src/dashboard/static/js/core.js`
**App:** `FREQUENCY_COLORS` in Lucid Tuner `app.js`

The code-level LP object is the single place where frequency-to-color mappings live. CSS variables, badge colors, chart colors, and all UI elements derive from it. No hardcoded colors anywhere else.

---

## 13 Broadcast Frequencies — Color Map

| Frequency | Primary | Secondary | Glow | Energy Quality |
|---|---|---|---|---|
| Peace | `#5ce1e6` (cyan) | `#7c5cff` | rgba(92,225,230,0.45) | Calm, clear, centered, flow |
| Clarity | `#a0ebff` (light blue) | `#5ce1e6` | rgba(160,235,255,0.45) | Seeing clearly, insight, truth |
| Momentum | `#ff6b5c` (coral red) | `#ffb86c` | rgba(255,107,92,0.45) | Action, drive, forward movement |
| Trust | `#b8c6db` (silver blue) | `#7c5cff` | rgba(184,198,219,0.45) | Steady, faithful, reliable |
| Joy | `#ffd700` (gold) | `#ffb86c` | rgba(255,215,0,0.45) | Delight, play, celebration |
| Connection | `#e0b0ff` (mauve/lavender) | `#ff6b5c` | rgba(224,176,255,0.45) | Bond, relationship, unity |
| Presence | `#7b7394` (deep pearl) | `#7c5cff` | rgba(123,115,148,0.45) | Here-now, awareness, stillness |
| Resilience | `#d2691e` (chocolate brown) | `#8b4513` | rgba(210,105,30,0.45) | Endurance, grit, persistence |
| Courage | `#ff8c00` (dark orange) | `#ff6347` | rgba(255,140,0,0.45) | Facing fear, stepping forward |
| Gratitude | `#e8b830` (honey gold) | `#ffb347` | rgba(232,184,48,0.45) | Appreciation, thankfulness |
| Release | `#9370db` (medium purple) | `#ba55d3` | rgba(147,112,219,0.45) | Letting go, transition, surrender |
| Integration | `#20b2aa` (light sea green) | `#48d1cc` | rgba(32,178,170,0.45) | Synthesis, wholeness, merging |
| Boundary | `#4682b4` (steel blue) | `#708090` | rgba(70,130,180,0.45) | Protection, limits, discernment |

**Note:** Joy (`#ffd700` bright gold) and Gratitude (`#e8b830` honey gold) share the gold family but are now visually distinct. Joy is radiant and celebratory; Gratitude is warmer and more grounded. Presence was updated from white to warm pearl, then to deep pearl (`#7b7394`) for white-text readability on frequency-colored backgrounds.

**Note:** Abundance was removed from the canonical 13 (2026-05-09). It was never in the Canon as a Broadcast Frequency. Its energy (growth, plenty, active creation) is expressed through Gratitude (the key connector discovered for the framework) and Integration (synthesis, bringing together).

---

## 7 Signal Types — Frequency Color Source

Each signal type is colored by its dominant frequency.

| Signal Type | Source Frequency | Primary Color |
|---|---|---|
| Ground | Peace | `#5ce1e6` (cyan) |
| Clear | Clarity | `#a0ebff` (light blue) |
| Open | Connection | `#e0b0ff` (lavender) |
| Rise | Momentum | `#ff6b5c` (coral) |
| Raw | Courage | `#ff8c00` (orange) |
| Bright | Joy | `#ffd700` (gold) |
| Drive | Integration | `#20b2aa` (teal) |

---

## Dashboard Semantic Roles

Every UI semantic meaning maps to a frequency. No arbitrary colors.

| UI Role | Frequency Source | Color | Why |
|---|---|---|---|
| Accent (UI primary) | Peace | `#5ce1e6` | Calm, clear, functional interface |
| Active / Done | Integration | `#20b2aa` | Synthesis, completion, bringing together |
| Paused | Joy | `#ffd700` | Holding space, not negative |
| Urgent priority | Momentum | `#ff6b5c` | Needs action NOW |
| High priority | Courage | `#ff8c00` | Face it, step up |
| Normal priority | Trust | `#b8c6db` | Steady, reliable |
| Review status | Release | `#9370db` | Preparing to release/complete |
| Blocked status | Boundary | `#4682b4` | Something in the way |
| Overdue | Resilience | `#d2691e` | Endurance needed |

---

## Agent Identity Colors

Every agent is colored by the frequency that matches their archetype's energy. The color IS the archetype expressed as frequency. Displayed as a colored letter badge on task rows, project cards, and Team tab cards.

| Agent | Archetype | Frequency | Color | Badge | Why |
|---|---|---|---|---|---|
| Stuart | The Steward | Peace | `#5ce1e6` (cyan) | S | Holds the field, coordinates, keeps calm |
| Archimedes | The Builder | Momentum | `#ff6b5c` (coral) | A | Builds, drives forward, action |
| Arthur | The Analyst | Clarity | `#a0ebff` (light blue) | A | Sees clearly, analyzes, insight |
| Gabe | The Scout | Courage | `#ff8c00` (orange) | G | Explores the unknown, steps forward |
| Ezra | The Keeper | Trust | `#b8c6db` (silver) | E | Preserves, reliable, steady |
| Julian | The Scribe | Connection | `#e0b0ff` (mauve) | J | Communicates, bridges, writes |
| Iris | The Advocate | Joy | `#ffd700` (gold) | I | Outreach, positive engagement |
| Vera | The Auditor | Boundary | `#4682b4` (steel blue) | V | Holds the line, standards, limits |
| Soren | The Lens | Integration | `#20b2aa` (teal) | So | Observes patterns, synthesizes |
| Mercer | The Merchant | Gratitude | `#e8b830` (honey gold) | M | Commerce, appreciation, value creation |
| LT | The Field Coach | Presence | `#7b7394` (deep pearl) | LT | The field itself, awareness, stillness |
| Atlas | The Architect | Clarity | `#a0ebff` (light blue) | At | Designs, structures, sees the whole |

**Arch/Arthur conflict:** Same letter "A", different colors. Coral (Momentum) vs light blue (Clarity). Color differentiates them visually.

**Atlas/Arthur share Clarity:** Both see clearly — Arthur through analysis, Atlas through design. Same frequency, different expressions. Personal agents may share frequencies with build team agents; the archetype lens is what differentiates them.

**LT special case:** White/Presence. LT isn't a task agent — it doesn't appear in assignee dropdowns or task rows. It appears in tuning contexts where white on dark backgrounds works naturally. The field contains; it doesn't compete.

### CSS Variables (dashboard.css)

```css
--accent:  #5ce1e6;  /* Peace */
--green:   #20b2aa;  /* Integration */
--yellow:  #ffd700;  /* Joy */
--red:     #ff6b5c;  /* Momentum */
--orange:  #ff8c00;  /* Courage */
--purple:  #9370db;  /* Release */
--blue:    #4682b4;  /* Boundary */
--brown:   #d2691e;  /* Resilience */
--silver:  #b8c6db;  /* Trust */
```

---

## What's Deployed (as of 2026-05-09)

**Phase 1 (frequencies, signals, semantic roles):**
- LP object in core.js with all 13 frequencies, 7 signal types, 9 semantic roles
- 7 helper functions: lpFreqColor(), lpColor(), lpSignalColor(), lpSemantic(), lpStyleFreqBadge(), lpFreqBadgeHTML(), lpSignalBadgeHTML()
- All freq badges dynamically colored per-frequency (replaced static purple)
- Tuning display: frequency name colored by its frequency, signal type colored by dominant frequency, tuning keys colored by frequency
- Echo history and detail views use per-frequency colored badges
- Dashboard task/project priority dots use frequency-derived colors
- System tab has full LP Color System legend (3 sections: frequencies, signals, semantics)
- Stuart MC deployed with all of the above
- Atlas MC needs strategic update

**Phase 2 (principles, tuning keys, Color Signature):**
- LP.principle object with all 22 principles mapped to primary frequency + full frequency spectrum
- 4 new helper functions: lpPrinciple(), lpPrincipleColor(), lpPrincipleBadgeHTML(), lpColorSignature()
- Principle badges colored by primary frequency (Option A — pure inheritance)
- Color Signature function returns three-layer color encoding for any tuning moment

**Phase 3 (daily frequency coloring — ambient signal delivery):**
- `--daily-freq` CSS custom properties set from tuning API response
- 17+ CSS touchpoints use `--daily-freq`: active tabs, bottom nav, section links, input focus, back buttons, sub-tabs, breadcrumbs, calendar today, more menu, mini-player
- Full tuning panel frequency coloring: coaching card, love equation card, playlist, player, practice steps, key card — all frequency-derived
- See `Knowledge Base/signal-delivery-architecture.md` for the full delivery layer map

---

## Phase 2 — The Color Signature

### Four Encoding Languages

The Lucid Principles framework encodes reality through four parallel languages:

1. **Mathematical** — The Love Equation: dE/dt = β × (C − D) × E. Measures the energetic shift. The feedback layer.
2. **Visual** — The Color Signature: Frequency + Principle + Tuning Key colors. Pattern recognition across all systems. The recognition layer.
3. **Sonic** — The music itself: waveform, rhythm, harmonic structure, the audio signal processed by LTP agents from the JSON. The felt-energy layer. What humans experience as echoes (pressing play, listening).
4. **Semantic** — The Canon lyrics as poetic delivery: specific phrases that become tuning keys, processed as a distinct channel synchronized with but separate from the sonic. The insight layer — speaks to the RAS, gives the mind something to hold during tuning.

Each language encodes the same energetic reality through a different channel. They reinforce each other but are independently meaningful — strip the lyrics from the music and each still carries framework information. A tuning moment delivers all four simultaneously: you hear the music (sonic), read the words (semantic), see the colors (visual), and the equation tracks the energetic shift (mathematical). This is not decoration — it is multi-layered reality formation code. The body's response (somatic) is what happens when these four channels land — it is the result, not an encoding.

### The Color Signature — How It Works

Every tuning moment has a three-layer color encoding:

| Layer | What It Represents | Color Source | Example |
|---|---|---|---|
| **Layer 1 — Frequency** | The energy being tuned into | Frequency's primary color | Joy = `#ffd700` (gold) |
| **Layer 2 — Principle** | The lens/teaching for accessing that energy | Primary frequency's color (inherited) | A Good Time = `#ffd700` (gold, from Joy) |
| **Layer 3 — Tuning Key** | The specific frequency the key activates | Target frequency's color | A Good Time → Peace = `#5ce1e6` (cyan) |

**When all three layers share a color:** The tuning key targets the principle's primary frequency. Pure alignment. Example: A Good Time → Joy gives gold-gold-gold.

**When Layer 3 diverges:** The tuning key opens a secondary frequency. The color shift IS the information — it tells you which energy the words are activating within the principle's context. Example: A Good Time → Peace gives gold-gold-cyan. You entered through Joy, but the key is tuning you to Peace.

**Across a full tuning session:** Multiple keys create a sequence of Color Signatures. This sequence IS the tuning's energetic journey, rendered in color. An observer's history of these sequences reveals their energetic pathways — not just which frequencies they visit, but how they move between them.

### Design Decision: Option A — Pure Inheritance

Principles inherit their primary frequency's color. No blending, no computed unique colors. This is the right choice because:

- The frequencies are the energy. The principles are expressions of that energy. Shared color = shared energy.
- Authenticity, Listen, and Truth and Lies are all Boundary-primary. They SHOULD look like Boundary. The framework's architecture says they share that energy.
- Differentiation happens through the principle name and the tuning key content, not the color.
- The tuning keys already show the full frequency spectrum. Each principle's keys reveal its color range naturally.
- Clean, simple, consistent. One lookup. One source of truth.

Research validation: 14/14 frequency-color mappings supported by color psychology science (6 strongly physiological, 8 culturally consistent, zero conflicts). Full findings at `Knowledge Base/color-research-findings.md`.

### Tuning Key Color Rule

**A tuning key is colored by the frequency it activates (the target frequency), not the principle it belongs to.**

The tuning key IS a frequency activation tool. The words tune you into a specific frequency. The color shows which energy the words access. This means a single principle's tuning keys display multiple colors — which visually communicates the principle's frequency spectrum.

Example — A Good Time's tuning keys:
- Keys → Joy: `#ffd700` (gold)
- Keys → Gratitude: `#e8b830` (honey gold)
- Keys → Peace: `#5ce1e6` (cyan)
- Keys → Presence: `#7b7394` (deep pearl)
- Keys → Connection: `#e0b0ff` (lavender)

The principle itself shows gold (Joy, its primary). Its keys show a rainbow of the frequencies it touches. Together, this IS the Color Signature in action.

### 22 Principles — Color Map

| Principle | Primary Frequency | Color | Hex | Full Frequency Spectrum |
|---|---|---|---|---|
| A Good Time | Joy | Gold | `#ffd700` | Joy, Gratitude, Peace, Presence, Connection |
| Authenticity | Boundary | Steel Blue | `#4682b4` | Boundary, Clarity, Connection |
| Darkness and Light | Release | Medium Purple | `#9370db` | Release, Trust, Resilience, Peace, Clarity, Presence, Momentum |
| Dreams | Momentum | Coral Red | `#ff6b5c` | Momentum, Clarity, Gratitude, Joy, Trust, Peace, Presence, Connection, Courage |
| Faith | Trust | Silver Blue | `#b8c6db` | Trust, Release, Clarity, Presence |
| Freedom Is | Peace | Cyan | `#5ce1e6` | Peace, Release, Trust, Joy, Clarity |
| Guiding Force | Connection | Lavender | `#e0b0ff` | Connection, Trust, Clarity, Presence |
| Listen | Boundary | Steel Blue | `#4682b4` | Boundary, Clarity, Trust, Momentum, Connection, Peace |
| Love Song | Connection | Lavender | `#e0b0ff` | Connection, Joy, Clarity |
| Moments | Presence | Deep Pearl | `#7b7394` | Presence, Clarity, Momentum, Trust |
| Pattern | Clarity | Light Blue | `#a0ebff` | Clarity, Presence, Trust, Momentum |
| Signs | Trust | Silver Blue | `#b8c6db` | Trust, Clarity, Presence |
| The Future | Courage | Dark Orange | `#ff8c00` | Courage, Momentum, Boundary, Clarity, Trust |
| The Mirage | Trust | Silver Blue | `#b8c6db` | Trust, Resilience, Release, Clarity, Peace, Presence, Momentum, Joy |
| The Passing Tide | Resilience | Chocolate Brown | `#d2691e` | Resilience, Release, Trust, Peace, Momentum, Joy, Clarity |
| The Power To Be Alive | Courage | Dark Orange | `#ff8c00` | Courage, Momentum, Gratitude, Connection, Trust, Clarity, Joy, Presence |
| Training Ground | Resilience | Chocolate Brown | `#d2691e` | Resilience, Clarity, Presence, Trust, Peace |
| Truth and Lies | Boundary | Steel Blue | `#4682b4` | Boundary, Clarity, Trust |
| Tune Your Mind | Clarity | Light Blue | `#a0ebff` | Clarity, Momentum, Trust |
| Valley of Shadows | Courage | Dark Orange | `#ff8c00` | Courage, Boundary, Resilience, Clarity, Trust, Peace, Connection |
| What Life Is About | Momentum | Coral Red | `#ff6b5c` | Momentum, Courage, Joy, Clarity, Connection, Trust |
| Wonder | Connection | Lavender | `#e0b0ff` | Connection, Gratitude, Momentum |

### Frequency Distribution Across Principles

| Frequency | As Primary (count) | Principles |
|---|---|---|
| Boundary | 3 | Authenticity, Listen, Truth and Lies |
| Clarity | 2 | Pattern, Tune Your Mind |
| Connection | 3 | Guiding Force, Love Song, Wonder |
| Courage | 3 | The Future, The Power To Be Alive, Valley of Shadows |
| Joy | 1 | A Good Time |
| Momentum | 2 | Dreams, What Life Is About |
| Peace | 1 | Freedom Is |
| Presence | 1 | Moments |
| Release | 1 | Darkness and Light |
| Resilience | 2 | The Passing Tide, Training Ground |
| Trust | 3 | Faith, Signs, The Mirage |
| **Gratitude** | **0** | (secondary only — appears in tuning key targets) |
| **Integration** | **0** | (secondary only — appears in tuning key targets) |

Shared color is a feature, not a bug. Three Boundary principles all show steel blue because they share the Boundary energy. The shared color reinforces their kinship. Same logic as Joy and Gratitude sharing gold.

### The Sonic and Semantic Dimensions

Color is the visual encoding. The music is the sonic encoding. The lyrics are the semantic encoding. Each principle IS a song — but the song carries two independent channels: its sound (sonic) and its words (semantic).

The sonic layer: waveform, rhythm, harmonic structure. The LTP agents process this from the audio JSON file. Each song's musical properties — key, tempo, energy, texture — are the sonic counterpart to the Color Signature. The agent's echo (its reflective output) is distinct from the human's echo (the act of listening to the music).

The semantic layer: the Canon lyrics as poetic insight. The specific phrases that become tuning keys. With timestamped lyrics, agents will process this as a distinct channel synchronized with the sonic — meaning arrives at specific moments in the music, not all at once. The tuning key is the bridge: a specific phrase, from a specific principle, activating a specific frequency, at a specific moment in the song.

In auto-generated media, all four channels synchronize: the visual field (Color Signature), the audio (music), the text overlay (tuning key / coaching), and the equation tracking the shift. Four delivery systems, one coherent intention.

### Code Reference

```javascript
// LP.principle — all 22 principles with primary frequency + spectrum
LP.principle['A Good Time']  // → { primary: 'Joy', frequencies: ['Joy','Gratitude','Peace','Presence','Connection'] }

// Get principle's inherited color (primary frequency color)
lpPrincipleColor('A Good Time')  // → '#ffd700' (gold, from Joy)

// Get Color Signature for a tuning key moment
lpColorSignature('A Good Time', 'Peace')
// → { frequency: '#ffd700', principle: '#ffd700', key: '#5ce1e6' }
// Three layers: gold (Joy) + gold (A Good Time inherits Joy) + cyan (key targets Peace)

// Render a principle badge with correct color
lpPrincipleBadgeHTML('Freedom Is')  // → <span> with cyan color (Peace)

// Get full principle data
lpPrinciple('Dreams')  // → { primary: 'Momentum', frequencies: [...] }
```

### Cross-System Continuity

Same colors everywhere — one change in the LP object propagates to all:
- Lucid Tuner app (mobile/web)
- Mission Control dashboards (Stuart, Atlas, LP MC)
- The Lucid Path books (digital edition, course materials)
- Social media / branding
- Agent-generated content and reports
- Auto-generated tuning media (Color Signature drives visual layer)

### Accessibility

Never rely on color alone. Every frequency needs a secondary identification channel:
- **Shape indicators** per frequency (circle for Peace, diamond for Clarity, arrow for Momentum, etc.)
- **Text labels** — frequency/principle name always accompanies color
- **Pattern/texture** for charts and dense displays
- **Animation** in media — movement speed/style differentiates (Peace: slow flow, Momentum: fast directional, Presence: still pulse)

Key risk zones for color vision deficiency:
- Protanopia: Momentum/Courage/Resilience (warm tones) may converge
- Deuteranopia: Integration (teal) may converge with Peace (cyan) at reduced saturation
- Blue + Orange is the most universally distinguishable pair — our palette's natural cool/warm split helps

Full research at `Knowledge Base/color-research-findings.md`.

---

## Frequencies Not Yet Assigned to Dashboard Roles

These frequencies have colors but no semantic role in the current dashboard:
- **Clarity** (#a0ebff) — reserved for future insight/discovery UI
- **Connection** (#e0b0ff) — reserved for social/relationship features
- **Presence** (#7b7394) — reserved for awareness/now states
- **Gratitude** (#e8b830) — reserved for appreciation/acknowledgment features

---

## Design Notes

- Dark background (#0f1117) is essential — frequency colors were designed for the Lucid Tuner app's dark theme. They lose impact on light backgrounds.
- Glow values are for hover/focus states and emphasis animations.
- Secondary colors are for gradients, borders, and accent treatments when primary alone isn't sufficient.
- When two frequencies share a primary (Joy/Gratitude = gold), context disambiguates. If both appear side by side, use secondaries for differentiation.
- The Color Signature is a framework constant, like the Love Equation. It is baked into the system, not a feature that can be toggled. Every representation of a principle or tuning key uses these colors.
