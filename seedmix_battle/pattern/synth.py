"""Procedural sample synthesizer for Seedmix Battle.

Transplanted from only4bms `mods/course_mode/course_generator.py` so this
mod has an independent copy it can evolve. Generates a small drum + synth
palette into a cache directory on first use and returns the WAV path map.

No binary assets are shipped in the repo — everything is synthesized at
runtime from numpy + the stdlib ``wave`` module.
"""

from __future__ import annotations

import os
import wave

import numpy as np

# ── A minor pentatonic frequencies across octaves ──────────────────────────
_PENTA = {
    1: [55.0, 65.41, 73.42, 82.41, 98.0],
    2: [110.0, 130.81, 146.83, 164.81, 196.0],
    3: [220.0, 261.63, 293.66, 329.63, 392.0],
    4: [440.0, 523.25, 587.33, 659.25, 784.0],
}

# ── Master sound table ─────────────────────────────────────────────────────
# (wav_id, filename, freq, duration, wave_type, volume)
_SOUND_TABLE: list[tuple[str, str, float, float, str, float]] = [
    # BGM percussion
    ("01", "kick.wav",       80,    0.5,  "kick",       0.9),
    ("02", "snare.wav",      400,   0.3,  "noise",      0.7),
    ("03", "hat_closed.wav", 800,   0.1,  "noise",      0.5),
    ("04", "hat_open.wav",   6000,  0.25, "hihat_open", 0.5),
    # Sub bass (octave 1)
    *[("%02X" % (0x10 + i), f"bass_{i}.wav", _PENTA[1][i], 0.8, "sub_bass", 0.85) for i in range(5)],
    # Bass pluck (octave 2)
    *[("%02X" % (0x15 + i), f"bpluck_{i}.wav", _PENTA[2][i], 0.2, "pluck", 0.7) for i in range(5)],
    # Lead low (octave 3)
    *[("1%s" % chr(65 + i), f"lead_lo_{i}.wav", _PENTA[3][i], 0.25, "synth_lead", 0.65) for i in range(5)],
    # Lead high (octave 4)
    *[("1%s" % chr(70 + i), f"lead_hi_{i}.wav", _PENTA[4][i], 0.25, "synth_lead", 0.65) for i in range(5)],
    # Arp pluck (octave 4)
    *[("%02X" % (0x20 + i), f"arp_{i}.wav", _PENTA[4][i], 0.15, "pluck", 0.7) for i in range(5)],
    # Chord stab (octave 3)
    *[("%02X" % (0x25 + i), f"chord_{i}.wav", _PENTA[3][i], 0.2, "chord_stab", 0.6) for i in range(5)],
    # FX
    ("2A", "fx_riser_lo.wav",  400, 0.3, "fx_riser",  0.5),
    ("2B", "fx_riser_hi.wav",  600, 0.3, "fx_riser",  0.5),
    ("2C", "fx_impact_lo.wav", 80,  0.2, "fx_impact", 0.7),
    ("2D", "fx_impact_hi.wav", 120, 0.2, "fx_impact", 0.7),
]


def sound_table() -> list[tuple[str, str, float, float, str, float]]:
    return list(_SOUND_TABLE)


# Groups used by the chart generator when picking note sounds.
PALETTE = {
    "kick":       "01",
    "snare":      "02",
    "hat_closed": "03",
    "hat_open":   "04",
    "bass":       ["%02X" % (0x10 + i) for i in range(5)],
    "bass_pluck": ["%02X" % (0x15 + i) for i in range(5)],
    "lead_lo":    ["1%s" % chr(65 + i) for i in range(5)],
    "lead_hi":    ["1%s" % chr(70 + i) for i in range(5)],
    "arp":        ["%02X" % (0x20 + i) for i in range(5)],
    "chord":      ["%02X" % (0x25 + i) for i in range(5)],
    "fx_riser":   ["2A", "2B"],
    "fx_impact":  ["2C", "2D"],
}


# ── Cache location ─────────────────────────────────────────────────────────

def _cache_dir() -> str:
    # Local user cache so the repo stays binary-free.
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    d = os.path.join(base, "only4bms", "seedmix_battle", "samples")
    os.makedirs(d, exist_ok=True)
    return d


def ensure_samples(force: bool = False) -> dict[str, str]:
    """Synthesize the sample pack into the cache (once) and return id→path."""
    out_dir = _cache_dir()
    wav_map: dict[str, str] = {}
    for wav_id, fname, freq, dur, wtype, vol in _SOUND_TABLE:
        path = os.path.join(out_dir, fname)
        if force or not os.path.exists(path):
            _generate_wav(path, freq, dur, wtype, volume=vol)
        wav_map[wav_id] = path
    return wav_map


# ── Waveform synthesis (lifted from course_generator._generate_wav) ───────

def _generate_wav(filename: str, freq: float, duration_sec: float,
                  wave_type: str = "sine", samplerate: int = 44100,
                  volume: float = 0.5) -> None:
    t = np.linspace(0, duration_sec, int(samplerate * duration_sec), endpoint=False)

    if wave_type == "sine":
        audio = np.sin(2 * np.pi * freq * t)
    elif wave_type == "square":
        audio = np.sign(np.sin(2 * np.pi * freq * t))
    elif wave_type == "kick":
        freq_env = np.exp(-t * 30) * freq
        audio = np.sin(2 * np.pi * freq_env * t)
    elif wave_type == "noise":
        audio = np.random.uniform(-1, 1, len(t))
    elif wave_type == "clack":
        noise = np.random.uniform(-1, 1, len(t)) * 0.5
        pulse = np.sin(2 * np.pi * freq * t) * 0.5
        audio = noise + pulse
    elif wave_type == "sub_bass":
        saw = 2.0 * ((t * freq) % 1.0) - 1.0
        saw2 = 2.0 * ((t * freq * 2.005) % 1.0) - 1.0
        audio = saw * 0.6 + saw2 * 0.3
        audio = np.tanh(audio * 2.0) * 0.7
    elif wave_type == "synth_lead":
        saw1 = 2.0 * ((t * freq) % 1.0) - 1.0
        saw2 = 2.0 * ((t * freq * 1.005) % 1.0) - 1.0
        saw3 = 2.0 * ((t * freq * 0.995) % 1.0) - 1.0
        audio = (saw1 + saw2 + saw3) / 3.0
    elif wave_type == "pluck":
        audio = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t)
    elif wave_type == "chord_stab":
        audio = (np.sin(2 * np.pi * freq * t) +
                 np.sin(2 * np.pi * freq * 1.26 * t) +
                 np.sin(2 * np.pi * freq * 1.5 * t)) / 3.0
    elif wave_type == "fx_riser":
        sweep = np.linspace(freq, freq * 4, len(t))
        audio = np.sin(2 * np.pi * np.cumsum(sweep) / samplerate)
    elif wave_type == "fx_impact":
        audio = np.random.uniform(-1, 1, len(t)) * 0.6 + np.sin(2 * np.pi * freq * t) * 0.4
    elif wave_type == "hihat_open":
        audio = np.random.uniform(-1, 1, len(t))
        kernel_size = max(1, int(samplerate * 0.001))
        if kernel_size > 1:
            smooth = np.convolve(audio, np.ones(kernel_size) / kernel_size, mode="same")
            audio = audio - smooth * 0.5
    else:
        audio = np.sin(2 * np.pi * freq * t)

    if wave_type == "kick":
        envelope = np.exp(-t * 30)
    elif wave_type == "clack":
        envelope = np.exp(-t * 80)
    elif wave_type == "noise":
        envelope = np.exp(-t * 20)
    elif wave_type == "sub_bass":
        attack = np.minimum(t / 0.005, 1.0)
        envelope = attack * np.exp(-t * 2.5)
    elif wave_type == "synth_lead":
        attack = np.minimum(t / 0.01, 1.0)
        release = np.exp(-np.maximum(t - 0.15, 0) * 6)
        envelope = attack * release
    elif wave_type == "pluck":
        envelope = np.exp(-t * 15)
    elif wave_type == "chord_stab":
        envelope = np.exp(-t * 8)
    elif wave_type == "fx_riser":
        envelope = np.minimum(t / (duration_sec * 0.8), 1.0)
    elif wave_type == "fx_impact":
        envelope = np.exp(-t * 25)
    elif wave_type == "hihat_open":
        envelope = np.exp(-t * 6)
    else:
        envelope = np.exp(-t * 3)

    audio = audio * envelope * volume
    audio = np.clip(audio, -1.0, 1.0)
    audio = np.int16(audio * 32767)

    with wave.open(filename, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(audio.tobytes())
