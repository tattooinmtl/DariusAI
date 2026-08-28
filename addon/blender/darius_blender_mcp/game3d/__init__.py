"""game3d — a neutral parametric kit for building game art in Blender.

Structures and units are described by parameters (footprint, storeys, roof
style, height, build, weapon) and coloured by named palettes, so the same
six calls produce a coherent asset set for whatever game is being made.

`palettes` is pure data and imports nothing from Blender; the rest needs
`bpy` and is only importable inside it.
"""

from __future__ import annotations

from . import palettes

try:  # pragma: no cover - exercised inside Blender
    from . import geometry, scene, structures, units
except ImportError:  # pragma: no cover - outside Blender
    geometry = scene = structures = units = None  # type: ignore[assignment]

__all__ = ("palettes", "geometry", "scene", "structures", "units")
