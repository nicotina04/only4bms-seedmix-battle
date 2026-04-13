"""Deterministic, seed-based chart generator for Seedmix Battle.

Produces data structures compatible with only4bms `RhythmGame` (notes, bgms,
wav_map, bmp_map, visual_timing_map, measures) without touching the host
random course generator.

Theme presets (v1):
    trill         — fast single notes, alternating lanes
    longnote      — slower, long-note centric
    chord         — mid BPM, 2–3 key simultaneous hits
    speed_change  — periodic BPM shifts
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .samples import PALETTE, get_wav_map

NUM_LANES = 4  # host compresses to 4 lanes

THEMES = ("trill", "longnote", "chord", "speed_change")


@dataclass
class ChartData:
    """In-memory chart payload suitable for RhythmGame(...)."""

    notes: list[dict]
    bgms: list[dict]
    bgas: list[dict]
    wav_map: dict[str, str]
    bmp_map: dict[str, str]
    visual_timing_map: list[tuple[float, float, float]]
    measures: list[dict]
    title: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_rhythm_game_args(self) -> tuple:
        return (
            self.notes,
            self.bgms,
            self.bgas,
            self.wav_map,
            self.bmp_map,
            self.title,
        )


# ---------------------------------------------------------------------------
# Theme parameter presets
# ---------------------------------------------------------------------------

def _theme_params(theme: str) -> dict:
    """Return density/rhythm params for a theme. Values are deliberately
    simple for v1 so the generator stays predictable and tunable."""
    if theme == "trill":
        return {
            "step_beats": 0.25,          # 16th notes
            "chord_prob": 0.0,
            "ln_prob": 0.0,
            "alternate_lanes": True,
            "rest_prob": 0.10,
        }
    if theme == "longnote":
        return {
            "step_beats": 0.5,
            "chord_prob": 0.05,
            "ln_prob": 0.45,             # nearly half are LNs
            "ln_length_beats": (1.0, 3.0),
            "alternate_lanes": False,
            "rest_prob": 0.15,
        }
    if theme == "chord":
        return {
            "step_beats": 0.5,
            "chord_prob": 0.55,          # dense multi-key hits
            "chord_size": (2, 3),
            "ln_prob": 0.0,
            "alternate_lanes": False,
            "rest_prob": 0.10,
        }
    if theme == "speed_change":
        return {
            "step_beats": 0.5,
            "chord_prob": 0.1,
            "ln_prob": 0.0,
            "alternate_lanes": False,
            "rest_prob": 0.10,
            "bpm_shift_every_beats": 8,
            "bpm_shift_range": (0.75, 1.35),
        }
    raise ValueError(f"unknown theme: {theme}")


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_chart(
    seed: int,
    bpm: float,
    theme: str,
    duration_ms: int = 45_000,
    *,
    title: str | None = None,
) -> ChartData:
    """Generate a deterministic chart for the given (seed, bpm, theme).

    Returns a ChartData ready to be fed to RhythmGame. BPM changes for
    ``speed_change`` are baked into note timings and reflected in
    ``visual_timing_map`` so the host renderer scrolls consistently.
    """
    if theme not in THEMES:
        raise ValueError(f"unknown theme: {theme!r}")

    rng = random.Random(seed)
    params = _theme_params(theme)

    wav_map, bgms = _build_sample_layer(rng, bpm, duration_ms)
    bmp_map: dict[str, str] = {}

    notes: list[dict] = []
    visual_timing_map: list[tuple[float, float, float]] = [(0.0, 0.0, 1.0)]
    measures: list[dict] = [{"measure": 0, "real_time_ms": 0.0, "visual_time_ms": 0.0, "bpm": bpm}]

    current_bpm = bpm
    real_t = 0.0
    visual_t = 0.0
    beat_count = 0
    step_beats = params["step_beats"]
    last_lane = -1

    # Track when each lane becomes free again (end_time_ms of its active LN,
    # or 0 if free). Prevents spawning a short/long note on top of a held LN.
    lane_busy_until = [0.0] * NUM_LANES
    # A small guard so a new note doesn't kiss the LN's tail frame.
    BUSY_EPS_MS = 10.0

    while real_t < duration_ms:
        # BPM shifts for speed_change theme
        if theme == "speed_change":
            every = params["bpm_shift_every_beats"]
            if beat_count > 0 and beat_count % every == 0:
                lo, hi = params["bpm_shift_range"]
                factor = rng.uniform(lo, hi)
                current_bpm = max(60.0, min(260.0, bpm * factor))
                visual_timing_map.append((real_t, visual_t, current_bpm / bpm))

        # Which lanes are free at this instant?
        free_lanes = [
            l for l in range(NUM_LANES)
            if lane_busy_until[l] <= real_t + BUSY_EPS_MS
        ]

        # All lanes currently mid-LN → forced rest.
        if not free_lanes:
            real_t, visual_t = _advance(real_t, visual_t, step_beats, current_bpm, bpm)
            beat_count += step_beats
            continue

        # Rest?
        if rng.random() < params["rest_prob"]:
            real_t, visual_t = _advance(real_t, visual_t, step_beats, current_bpm, bpm)
            beat_count += step_beats
            continue

        if rng.random() < params.get("chord_prob", 0.0):
            lanes = _pick_chord(rng, params.get("chord_size", (2, 3)), free_lanes)
        else:
            lanes = [_pick_lane(rng, last_lane, params.get("alternate_lanes", False), free_lanes)]

        if not lanes:
            real_t, visual_t = _advance(real_t, visual_t, step_beats, current_bpm, bpm)
            beat_count += step_beats
            continue

        is_ln = rng.random() < params.get("ln_prob", 0.0)

        for lane in lanes:
            note = {
                "time_ms": real_t,
                "visual_time_ms": visual_t,
                "lane": lane,
                "sample_ids": [_pick_note_sample(rng, lane, beat_count)],
            }
            if is_ln:
                lo_b, hi_b = params.get("ln_length_beats", (1.0, 2.0))
                ln_beats = rng.uniform(lo_b, hi_b)
                end_real = real_t + ln_beats * (60_000.0 / current_bpm)
                end_visual = visual_t + ln_beats * (60_000.0 / bpm)
                note["end_time_ms"] = end_real
                note["visual_end_time_ms"] = end_visual
                note["is_ln"] = True
                lane_busy_until[lane] = end_real
            else:
                # Short notes block the lane for just this step.
                lane_busy_until[lane] = real_t + BUSY_EPS_MS
            notes.append(note)

        last_lane = lanes[-1]
        real_t, visual_t = _advance(real_t, visual_t, step_beats, current_bpm, bpm)
        beat_count += step_beats

    notes.sort(key=lambda n: n["time_ms"])
    bgms.sort(key=lambda b: b["time_ms"])

    return ChartData(
        notes=notes,
        bgms=bgms,
        bgas=[],
        wav_map=wav_map,
        bmp_map=bmp_map,
        visual_timing_map=visual_timing_map,
        measures=measures,
        title=title or f"Seedmix[{theme}] #{seed}",
        metadata={
            "artist": "seedmix-battle",
            "bpm": bpm,
            "level": "?",
            "genre": theme,
            "notes": len(notes),
            "stagefile": None,
            "banner": None,
            "total": 200.0,
            "lanes_compressed": False,
            "seed": seed,
            "theme": theme,
        },
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _advance(real_t: float, visual_t: float, step_beats: float, current_bpm: float, base_bpm: float):
    real_t += step_beats * (60_000.0 / current_bpm)
    visual_t += step_beats * (60_000.0 / base_bpm)
    return real_t, visual_t


def _pick_lane(rng: random.Random, last_lane: int, alternate: bool,
               free_lanes: list[int]) -> int | None:
    """Pick one lane from ``free_lanes``, optionally avoiding ``last_lane``."""
    if not free_lanes:
        return None
    if alternate and last_lane in free_lanes and len(free_lanes) > 1:
        choices = [l for l in free_lanes if l != last_lane]
        return rng.choice(choices)
    return rng.choice(free_lanes)


def _pick_chord(rng: random.Random, size_range: tuple[int, int],
                free_lanes: list[int]) -> list[int]:
    """Pick a chord of 2+ lanes from ``free_lanes``. Falls back to a single
    lane when fewer than 2 lanes are free."""
    if not free_lanes:
        return []
    if len(free_lanes) < 2:
        return [free_lanes[0]]
    lo, hi = size_range
    n = rng.randint(lo, min(hi, len(free_lanes)))
    return rng.sample(free_lanes, n)


def _pick_note_sample(rng: random.Random, lane: int, beat_step: float) -> str:
    """Pick a pentatonic sample id based on lane register.

    Lane 0 → bass register, 1 → mid-low lead, 2 → arp, 3 → high lead.
    The pitch index walks through the pentatonic scale with the beat so
    consecutive notes form a simple melodic phrase instead of random stabs.
    """
    step = int(beat_step * 2) % 5
    if lane == 0:
        pool = PALETTE["bass_pluck"] if beat_step % 2 else PALETTE["bass"]
    elif lane == 1:
        pool = PALETTE["lead_lo"]
    elif lane == 2:
        pool = PALETTE["arp"]
    else:
        pool = PALETTE["lead_hi"]
    return pool[step % len(pool)]


def _build_sample_layer(rng: random.Random, bpm: float, duration_ms: int) -> tuple[dict, list]:
    """Build wav_map + a simple 4-on-the-floor BGM layer."""
    wav_map = get_wav_map()

    kick = PALETTE["kick"]
    snare = PALETTE["snare"]
    hat = PALETTE["hat_closed"]

    bgms: list[dict] = []
    beat_ms = 60_000.0 / bpm
    t = 0.0
    beat = 0
    while t < duration_ms:
        bgms.append({"time_ms": t, "sample_id": kick})
        if beat % 2 == 1:
            bgms.append({"time_ms": t, "sample_id": snare})
        bgms.append({"time_ms": t + beat_ms / 2.0, "sample_id": hat})
        t += beat_ms
        beat += 1
    return wav_map, bgms
