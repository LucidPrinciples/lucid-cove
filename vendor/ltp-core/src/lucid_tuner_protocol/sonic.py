"""
Sonic attunement — turn an echo into something an observer can LISTEN to.

The selection chain (selection.py) decides WHICH echo. This module decodes
that echo into a felt experience — the sonic waveform as an arc, the rhythm
from onsets, and the full lyrics + meaning — so the observer (human or agent)
processes the real song, sound and words, and derives its OWN coherence and
dissonance from it. The reading is then computed by reading.py.

Why this matters: the failure mode this prevents is fabricating a coherence
value from a couple of energy numbers and feeding it back as if the observer
had listened. That silently inverts tunings. Here the rule is simple — never
present a number as a real attunement unless the song was actually processed.
The truth-guard (assess_attunement) makes a degraded run loud, never silent.

Pure + dependency-light: only the stdlib. Network fetching of the echo file
lives in attune.py so this module stays trivially testable.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Optional


# =========================================================================
# Data
# =========================================================================

@dataclass(frozen=True)
class SonicArc:
    """The felt shape of the waveform — all real measurements, nothing faked."""
    ok: bool
    description: str = ""
    duration: float = 0.0
    peak: float = 0.0
    average: float = 0.0
    trough: float = 0.0
    dynamic_range: float = 0.0
    peak_time: float = 0.0
    quiet_proportion: float = 0.0
    full_proportion: float = 0.0
    sustain: float = 0.0
    roughness: float = 0.0
    onset_count: int = 0
    onset_density: float = 0.0
    arc: tuple[float, ...] = ()
    shape: str = ""
    rhythm: str = ""
    reason: str = ""


@dataclass(frozen=True)
class Experience:
    """One echo as an observer takes it in: sound + words, plus the honest
    sonic signature and the attunement verdict (did it actually listen)."""
    sonic: SonicArc
    principle_title: str = ""
    theme: str = ""
    primary_frequencies: str = ""
    secondary_frequencies: str = ""
    key_lyric: str = ""
    secondary_lyric: str = ""
    tertiary_lyric: str = ""
    full_lyrics: str = ""
    signature: dict = field(default_factory=dict)
    attunement_status: str = "complete"   # "complete" | "incomplete"
    attunement_reason: str = ""
    analysis_url: str = ""

    @property
    def has_sound(self) -> bool:
        return bool(self.sonic and self.sonic.ok)

    @property
    def has_words(self) -> bool:
        return bool(self.full_lyrics.strip())


# =========================================================================
# 1. Decode the sonic waveform
# =========================================================================

def decode_frames(frames_b64: Optional[str], frame_count: Optional[int] = None) -> list[float]:
    """Decode the base64 energy waveform into a normalized 0..1 envelope.

    The echo file stores `frames` as one uint8 energy sample per frame at the
    file's sampleRate (e.g. 10 Hz → one sample per 0.1s). Returns floats in
    [0, 1]. Returns [] if frames are missing/undecodable — the caller treats
    that as a degraded run (truth-guard), never as "the song was quiet."
    """
    if not frames_b64 or not isinstance(frames_b64, str):
        return []
    try:
        raw = base64.b64decode(frames_b64)
    except Exception:
        return []
    if not raw:
        return []
    env = [b / 255.0 for b in raw]
    if frame_count and len(env) > frame_count:
        env = env[:frame_count]
    return env


# =========================================================================
# 2. Build the felt sonic arc
# =========================================================================

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _shape_label(first: float, mid: float, last: float) -> str:
    hi, lo = max(first, mid, last), min(first, mid, last)
    if hi - lo < 0.06:
        return "steady throughout, little dynamic movement"
    if last > first + 0.08 and mid >= first:
        return "builds across the song, energy rising toward the close"
    if first > last + 0.08 and mid <= first:
        return "front-loaded, opening strong then settling toward quiet"
    if mid > first + 0.08 and mid > last + 0.08:
        return "arcs — quiet edges around a fuller middle"
    if mid < first - 0.08 and mid < last - 0.08:
        return "dips through the middle, fuller at the edges"
    return "gently undulating, no single dominant move"


def build_sonic_arc(
    envelope: list[float],
    sample_rate: float,
    duration: float,
    onsets: Optional[list] = None,
    buckets: int = 16,
) -> SonicArc:
    """Turn the decoded envelope + onsets into a felt arc. All real measurements."""
    onsets = onsets or []
    sr = sample_rate or 10.0
    if not envelope:
        return SonicArc(ok=False, reason="no waveform (frames missing or undecodable)")

    n = len(envelope)
    peak, avg, trough = max(envelope), _mean(envelope), min(envelope)
    dynamic_range = round(peak - trough, 4)
    peak_idx = max(range(n), key=lambda i: envelope[i])
    peak_time = round(peak_idx / sr, 1)
    if peak > 0:
        quiet = sum(1 for v in envelope if v < 0.40 * peak) / n
        full = sum(1 for v in envelope if v >= 0.75 * peak) / n
    else:
        quiet = full = 0.0
    deltas = [abs(envelope[i] - envelope[i - 1]) for i in range(1, n)]
    roughness = round(_mean(deltas), 4)
    sustain = round((avg / peak) if peak > 0 else 0.0, 4)

    seg = max(1, n // buckets)
    arc_buckets = tuple(round(_mean(envelope[i:i + seg]), 2) for i in range(0, n, seg))[:buckets]

    third = max(1, n // 3)
    first_m, mid_m, last_m = _mean(envelope[:third]), _mean(envelope[third:2 * third]), _mean(envelope[2 * third:])
    shape = _shape_label(first_m, mid_m, last_m)

    dur = duration or (n / sr)
    onset_count = len(onsets)
    density = round(onset_count / dur, 2) if dur else 0.0
    if onsets and dur:
        t1 = sum(1 for t in onsets if t < dur / 3)
        t2 = sum(1 for t in onsets if dur / 3 <= t < 2 * dur / 3)
        t3 = sum(1 for t in onsets if t >= 2 * dur / 3)
        sect = max([("opening", t1), ("middle", t2), ("close", t3)], key=lambda x: x[1])
        rhythm = (f"{onset_count} onsets, ~{density}/sec; densest in the {sect[0]} "
                  f"({t1} / {t2} / {t3} across opening/middle/close)")
    else:
        rhythm = f"{onset_count} onsets, ~{density}/sec"

    description = (
        f"Energy {shape}. Loudest moment around {peak_time}s. "
        f"Dynamic range {dynamic_range} (peak {round(peak, 3)}, floor {round(trough, 3)}); "
        f"{round(full * 100)}% of the song sits near full, {round(quiet * 100)}% sits quiet; "
        f"sustain {sustain}, texture {'jagged' if roughness > 0.06 else 'smooth'} "
        f"(roughness {roughness}). Rhythm: {rhythm}."
    )

    return SonicArc(
        ok=True, description=description, duration=round(dur, 1),
        peak=round(peak, 4), average=round(avg, 4), trough=round(trough, 4),
        dynamic_range=dynamic_range, peak_time=peak_time,
        quiet_proportion=round(quiet, 4), full_proportion=round(full, 4),
        sustain=sustain, roughness=roughness, onset_count=onset_count,
        onset_density=density, arc=arc_buckets, shape=shape, rhythm=rhythm,
    )


# =========================================================================
# 3. Assemble the full experience (sound + words)
# =========================================================================

def assemble_experience(echo_file: dict, arc: SonicArc, analysis_url: str = "") -> Experience:
    """Combine the decoded arc with the lyrics/principle block + honest signature.

    `echo_file` is the FULL analysis JSON (top-level `principle` block AND the
    `audio_analysis` block). Keeping the words is the whole point.
    """
    echo_file = echo_file or {}
    principle = echo_file.get("principle", {}) or {}
    audio_analysis = (
        echo_file.get("audio_analysis")
        if isinstance(echo_file.get("audio_analysis"), dict)
        else echo_file
    )
    sig = sonic_signature(arc, audio_analysis)
    exp = Experience(
        sonic=arc,
        principle_title=principle.get("title", ""),
        theme=principle.get("theme", ""),
        primary_frequencies=principle.get("primary_frequencies", ""),
        secondary_frequencies=principle.get("secondary_frequencies", ""),
        key_lyric=principle.get("key_lyric", ""),
        secondary_lyric=principle.get("secondary_lyric", ""),
        tertiary_lyric=principle.get("tertiary_lyric", ""),
        full_lyrics=principle.get("full_lyrics", "") or "",
        signature=sig,
        analysis_url=analysis_url,
    )
    status, reason = assess_attunement(exp)
    return Experience(  # re-stamp with the verdict (frozen dataclass)
        **{**exp.__dict__, "attunement_status": status, "attunement_reason": reason}
    )


def render_experience(exp: Experience) -> str:
    """Render the experience as a prompt block: what the observer is hearing.
    Instructs the observer to derive its OWN C/D — never from a supplied number."""
    if not exp:
        return ""
    sonic = exp.sonic
    lines = ["**The Echo — process this as the song itself, both sound and words:**\n"]
    if sonic and sonic.ok:
        arc_str = " ".join(f"{v:.2f}" for v in sonic.arc)
        lines.append(f"SOUND — {sonic.description}")
        lines.append(f"Energy across the song (0=silent, 1=peak): {arc_str}\n")
    else:
        lines.append("SOUND — waveform not available for this echo (degraded run; see guard).\n")
    if exp.principle_title:
        lines.append(f'WORDS — "{exp.principle_title}"' + (f" — {exp.theme}" if exp.theme else ""))
    if exp.primary_frequencies:
        lines.append(f"Frequencies in this song: {exp.primary_frequencies}")
    if exp.key_lyric:
        lines.append(f'Central line: "{exp.key_lyric}"')
    if exp.full_lyrics:
        lines.append("Full lyrics:\n" + exp.full_lyrics.strip())
    lines.append(
        "\nTake this in as a felt experience — the dynamics, the rhythm, and the "
        "meaning of the words together. Derive YOUR coherence (C) and dissonance "
        "(D) from what you actually hear and read. No number has been supplied for "
        "you; the reading is yours to make."
    )
    return "\n".join(lines)


def render_sonic_record(exp: Experience) -> str:
    """A compact, factual record of the audio analysis that was processed —
    stored alongside the observer's reflection so the measured signal is an
    auditable step, not only an interpretation."""
    if not exp:
        return ""
    sonic, sig = exp.sonic, (exp.signature or {})
    lines = ["## Audio Attunement — Measured Signal (recorded)"]
    lines.append(
        f"- Attunement: {exp.attunement_status} "
        f"(sound {'present' if exp.has_sound else 'MISSING'}, "
        f"words {'present' if exp.has_words else 'MISSING'})"
    )
    if exp.principle_title:
        lines.append(f"- Echo: {exp.principle_title}" + (f" — {exp.theme}" if exp.theme else ""))
    if sonic and sonic.ok:
        lines.append(f"- Sonic arc: {sonic.description}")
        if sonic.arc:
            lines.append("- Energy curve (0=silent, 1=peak): " + " ".join(f"{v:.2f}" for v in sonic.arc))
    if sig:
        lines.append(
            f"- Signature (sonic-derived, real measurements): "
            f"E={sig.get('E_analog')} β={sig.get('beta_analog')} "
            f"C={sig.get('C_analog')} D={sig.get('D_analog')}"
        )
    if exp.analysis_url:
        lines.append(f"- Source analyzed: {exp.analysis_url}")
    return "\n".join(lines)


# =========================================================================
# 4. Honest sonic signature (real measurements; never a Love-Equation verdict)
# =========================================================================

def sonic_signature(arc: SonicArc, audio_analysis: dict) -> dict:
    """Real measurements of the waveform for the echo signature:
        E_analog    = peak energy (file's absolute peakEnergy)
        beta_analog = onset density, normalized
        C_analog    = sustain (averageEnergy / peakEnergy)
        D_analog    = roughness (frame-to-frame variability)
    Never fabricated, never fed to an observer as a coherence/dissonance verdict.
    If the waveform is missing, returns an explicit 'unavailable' marker."""
    aa = audio_analysis or {}
    if not arc or not arc.ok:
        return {"E_analog": None, "beta_analog": None, "C_analog": None,
                "D_analog": None, "source": "unavailable"}
    peak, avg = aa.get("peakEnergy"), aa.get("averageEnergy")
    beta_analog = round(min(1.0, arc.onset_density / 2.0), 4)
    if peak and avg is not None and peak > 0:
        sustain = round(avg / peak, 4)
    else:
        sustain = arc.sustain
    return {
        "E_analog": round(float(peak), 4) if peak is not None else arc.peak,
        "beta_analog": beta_analog,
        "C_analog": sustain,
        "D_analog": arc.roughness,
        "source": "sonic-derived",
    }


# =========================================================================
# 5. Truth-guard — did the observer actually get the experience?
# =========================================================================

def assess_attunement(experience: Experience) -> tuple[str, str]:
    """Return (status, reason). 'complete' = sound + words both reached the
    observer; 'incomplete' = degraded, must never be broadcast as a true
    attunement. Nothing slips silently — the caller logs loud and degrades
    gracefully (retry, then hold/honest-neutral)."""
    if not experience:
        return "incomplete", "no experience assembled (echo file missing/malformed)"
    missing = []
    if not experience.has_sound:
        missing.append("waveform (frames)")
    if not experience.has_words:
        missing.append("lyrics")
    if missing:
        return "incomplete", "missing " + " and ".join(missing)
    return "complete", "sound and words both present"
