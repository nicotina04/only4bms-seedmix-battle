"""Stitch multiple per-turn charts into a single continuous-play mega-chart.

The whole battle runs inside one ``RhythmGame`` session so the player
never leaves the lanes view. Between turns there is a short *bridge*
segment with no playable notes — only a BPM-interpolated kick pattern
— during which the extension draws the previous turn's result overlay
and the next turn's theme banner on top of the playfield.

Structure of the stitched timeline::

    [Turn 1 notes]  [Bridge 1→2]  [Turn 2 notes]  [Bridge 2→3]  ...  [Turn 10 notes]

Visual time tracks real time 1:1 (constant scroll speed — BPM changes
between turns don't warp the scroll, they only affect beat spacing inside
each turn, which is already baked into note time_ms by the generator).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .generator import ChartData, generate_chart
from .samples import PALETTE, get_wav_map

INTRO_DURATION_MS = 4000   # lead-in before the very first turn's notes land
TURN_TAIL_MS = 600         # silence after last note of a turn before the bridge
BRIDGE_DURATION_MS = 5000  # length of each inter-turn bridge segment
BRIDGE_EASE_POWER = 1.7    # >1 → accelerating feel (mirrored for slow-down)
BRIDGE_OVERSHOOT = 0.15    # % beyond target BPM for the end telegraph
BRIDGE_MIN_DELTA_BPM = 15  # small deltas still get this much sweep for feel


@dataclass
class TurnBoundary:
    turn: int
    theme: str
    bpm: float
    owner_id: int | None
    play_start_ms: float      # first playable note / start of turn segment
    play_end_ms: float        # last playable note + TURN_TAIL_MS
    bridge_end_ms: float      # bridge_end == next turn's play_start (or total end)


@dataclass
class MegaChart:
    notes: list[dict]
    bgms: list[dict]
    bgas: list[dict]
    wav_map: dict[str, str]
    bmp_map: dict[str, str]
    visual_timing_map: list[tuple[float, float, float]]
    measures: list[dict]
    title: str
    metadata: dict[str, Any] = field(default_factory=dict)
    turn_boundaries: list[TurnBoundary] = field(default_factory=list)
    total_duration_ms: float = 0.0


def build_battle_chart(turns: list[dict], *, per_turn_duration_ms: int = 35_000,
                        title: str = "Seedmix Battle") -> MegaChart:
    """Stitch the given turn specs into a single continuous chart.

    ``turns`` is a list of ``{turn, seed, bpm, theme, owner_id}`` dicts.
    """
    all_notes: list[dict] = []
    all_bgms: list[dict] = []
    wav_map = dict(get_wav_map())

    boundaries: list[TurnBoundary] = []
    cursor_ms = 0.0

    # Intro lead-in: kick-only bridge ramping up toward the first turn's BPM
    # so the player has time to settle in before the first notes land.
    if turns:
        first_bpm = float(turns[0]["bpm"])
        _append_bridge_bgms(
            all_bgms, start_ms=0.0,
            duration_ms=INTRO_DURATION_MS,
            from_bpm=first_bpm * 0.75,
            to_bpm=first_bpm,
        )
        cursor_ms = INTRO_DURATION_MS

    for i, t in enumerate(turns):
        bpm = float(t["bpm"])
        chart = generate_chart(
            t["seed"], bpm, t["theme"],
            duration_ms=per_turn_duration_ms,
        )

        # Shift the turn's notes/bgms by the current cursor.
        turn_start = cursor_ms
        for n in chart.notes:
            m = dict(n)
            m["time_ms"] = m["time_ms"] + cursor_ms
            m["visual_time_ms"] = m["time_ms"]
            if "end_time_ms" in m:
                m["end_time_ms"] = m["end_time_ms"] + cursor_ms
                m["visual_end_time_ms"] = m["end_time_ms"]
            all_notes.append(m)
        for b in chart.bgms:
            all_bgms.append({
                "time_ms": b["time_ms"] + cursor_ms,
                "sample_id": b["sample_id"],
            })

        turn_notes_end = max(
            (n["time_ms"] + cursor_ms for n in chart.notes),
            default=turn_start,
        )
        play_end = turn_notes_end + TURN_TAIL_MS

        is_last = (i == len(turns) - 1)
        if is_last:
            bridge_end = play_end
        else:
            next_bpm = float(turns[i + 1]["bpm"])
            _append_bridge_bgms(
                all_bgms, start_ms=play_end,
                duration_ms=BRIDGE_DURATION_MS,
                from_bpm=bpm, to_bpm=next_bpm,
            )
            bridge_end = play_end + BRIDGE_DURATION_MS

        boundaries.append(TurnBoundary(
            turn=t["turn"],
            theme=t["theme"],
            bpm=bpm,
            owner_id=t.get("owner_id"),
            play_start_ms=turn_start,
            play_end_ms=play_end,
            bridge_end_ms=bridge_end,
        ))

        cursor_ms = bridge_end

    all_notes.sort(key=lambda n: n["time_ms"])
    all_bgms.sort(key=lambda b: b["time_ms"])

    measures = [{
        "measure": 0, "real_time_ms": 0.0, "visual_time_ms": 0.0,
        "bpm": float(turns[0]["bpm"]) if turns else 140.0,
    }]
    visual_timing_map = [(0.0, 0.0, 1.0)]

    return MegaChart(
        notes=all_notes,
        bgms=all_bgms,
        bgas=[],
        wav_map=wav_map,
        bmp_map={},
        visual_timing_map=visual_timing_map,
        measures=measures,
        title=title,
        metadata={
            "artist": "seedmix-battle",
            "bpm": float(turns[0]["bpm"]) if turns else 140.0,
            "level": "?",
            "genre": "battle",
            "notes": len(all_notes),
            "stagefile": None,
            "banner": None,
            "total": 200.0,
            "lanes_compressed": False,
        },
        turn_boundaries=boundaries,
        total_duration_ms=cursor_ms,
    )


# ---------------------------------------------------------------------------
# Bridge BGM pattern
# ---------------------------------------------------------------------------

def _append_bridge_bgms(bgms: list[dict], *, start_ms: float, duration_ms: float,
                         from_bpm: float, to_bpm: float) -> None:
    """DJ-mix style kick bridge with direction-aware BPM sweep.

    - Speed-up (to_bpm > from_bpm): ease-in curve, density ramps up
      (snares every beat, 16th-note hats) near the end, and the sweep
      overshoots ``to_bpm`` slightly before the first note lands — the
      classic "쿵쿵쿵쿵쿵" acceleration.
    - Slow-down (to_bpm < from_bpm): ease-out curve, layers thin out to
      just kick + half-beat hat near the end, sweep undershoots
      ``to_bpm`` slightly. Feels like the tempo is dropping into the
      next turn.
    - Flat (delta ≈ 0): a small pulse sweep so the transition is still
      audible instead of just metronomic.
    """
    kick = PALETTE["kick"]
    snare = PALETTE["snare"]
    hat = PALETTE["hat_closed"]

    delta = to_bpm - from_bpm
    if abs(delta) < BRIDGE_MIN_DELTA_BPM:
        # Force a perceptible swing even when the two turns are close.
        sign = 1.0 if delta >= 0 else -1.0
        delta = sign * BRIDGE_MIN_DELTA_BPM
    speeding_up = delta >= 0

    # Overshoot target: push past to_bpm in the direction of travel.
    final_bpm = to_bpm * (1.0 + BRIDGE_OVERSHOOT * (1.0 if speeding_up else -1.0))
    start_bpm = max(50.0, from_bpm)
    final_bpm = max(50.0, final_bpm)

    t = 0.0
    beat_idx = 0
    # Small pad at the end so the last bridge hit doesn't step on the
    # upcoming turn's first note.
    hard_stop = duration_ms - 15.0

    while t < hard_stop:
        progress = max(0.0, min(1.0, t / duration_ms))
        if speeding_up:
            eased = progress ** BRIDGE_EASE_POWER             # accelerate
        else:
            eased = 1.0 - (1.0 - progress) ** BRIDGE_EASE_POWER  # decelerate
        current_bpm = start_bpm + (final_bpm - start_bpm) * eased
        beat_ms = 60_000.0 / max(50.0, current_bpm)

        bgms.append({"time_ms": start_ms + t, "sample_id": kick})

        if speeding_up:
            # Densify near the end for the acceleration telegraph.
            if beat_idx % 2 == 1 or progress > 0.60:
                bgms.append({"time_ms": start_ms + t, "sample_id": snare})
            if t + beat_ms / 2.0 < hard_stop:
                bgms.append({"time_ms": start_ms + t + beat_ms / 2.0, "sample_id": hat})
            if progress > 0.50:
                if t + beat_ms / 4.0 < hard_stop:
                    bgms.append({"time_ms": start_ms + t + beat_ms / 4.0, "sample_id": hat})
                if t + (beat_ms * 3) / 4.0 < hard_stop:
                    bgms.append({"time_ms": start_ms + t + (beat_ms * 3) / 4.0, "sample_id": hat})
        else:
            # Strip layers for the deceleration / "cool-down" feel.
            if beat_idx % 2 == 1 and progress < 0.55:
                bgms.append({"time_ms": start_ms + t, "sample_id": snare})
            if progress < 0.70 and t + beat_ms / 2.0 < hard_stop:
                bgms.append({"time_ms": start_ms + t + beat_ms / 2.0, "sample_id": hat})

        t += beat_ms
        beat_idx += 1
