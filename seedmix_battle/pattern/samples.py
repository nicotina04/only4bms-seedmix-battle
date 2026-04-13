"""Sample pack facade — delegates to the procedural synth.

Kept as a thin wrapper so the generator can import a stable
``get_wav_map`` / ``PALETTE`` API without knowing synthesis details.
"""

from __future__ import annotations

from .synth import PALETTE, ensure_samples


def get_wav_map() -> dict[str, str]:
    """Ensure samples exist in cache and return id → wav path."""
    return ensure_samples()


__all__ = ["PALETTE", "get_wav_map"]
