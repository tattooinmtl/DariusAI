"""Neutral colour palettes for game assets.

Pure data and pure functions — no `bpy` — so the palette set can be tested
outside Blender and reused by anything that needs consistent colours.

Palettes are named for materials and moods rather than factions: a game
built with this kit picks its own fiction, and a palette called "verdant"
carries no assumptions about who lives there.
"""

from __future__ import annotations

from typing import Any

# Linear-space RGB, the space Blender's shader nodes actually work in.
# Each palette names the same six roles so any archetype can be rebuilt in
# any palette without knowing which one it got.
ROLES = ("base", "accent", "roof", "trim", "metal", "emissive")

PALETTES: dict[str, dict[str, tuple[float, float, float]]] = {
    "stone": {
        "base": (0.52, 0.51, 0.48), "accent": (0.38, 0.37, 0.35),
        "roof": (0.30, 0.29, 0.30), "trim": (0.68, 0.66, 0.60),
        "metal": (0.55, 0.56, 0.58), "emissive": (1.00, 0.83, 0.45),
    },
    "timber": {
        "base": (0.42, 0.28, 0.16), "accent": (0.28, 0.18, 0.10),
        "roof": (0.35, 0.22, 0.14), "trim": (0.72, 0.60, 0.42),
        "metal": (0.48, 0.44, 0.36), "emissive": (1.00, 0.72, 0.32),
    },
    "desert": {
        "base": (0.80, 0.68, 0.45), "accent": (0.62, 0.50, 0.32),
        "roof": (0.52, 0.40, 0.26), "trim": (0.90, 0.82, 0.64),
        "metal": (0.72, 0.62, 0.34), "emissive": (1.00, 0.86, 0.50),
    },
    "verdant": {
        "base": (0.30, 0.42, 0.26), "accent": (0.20, 0.30, 0.18),
        "roof": (0.24, 0.34, 0.22), "trim": (0.62, 0.68, 0.48),
        "metal": (0.50, 0.54, 0.46), "emissive": (0.62, 1.00, 0.55),
    },
    "obsidian": {
        "base": (0.11, 0.11, 0.13), "accent": (0.06, 0.06, 0.08),
        "roof": (0.09, 0.09, 0.12), "trim": (0.30, 0.30, 0.36),
        "metal": (0.42, 0.43, 0.48), "emissive": (0.85, 0.30, 0.20),
    },
    "frost": {
        "base": (0.72, 0.79, 0.86), "accent": (0.52, 0.61, 0.72),
        "roof": (0.40, 0.50, 0.62), "trim": (0.90, 0.94, 0.98),
        "metal": (0.62, 0.68, 0.76), "emissive": (0.55, 0.85, 1.00),
    },
    "crimson": {
        "base": (0.48, 0.14, 0.14), "accent": (0.32, 0.09, 0.09),
        "roof": (0.26, 0.08, 0.10), "trim": (0.78, 0.52, 0.36),
        "metal": (0.62, 0.50, 0.24), "emissive": (1.00, 0.42, 0.22),
    },
    "azure": {
        "base": (0.18, 0.30, 0.52), "accent": (0.11, 0.19, 0.36),
        "roof": (0.14, 0.22, 0.40), "trim": (0.62, 0.72, 0.86),
        "metal": (0.55, 0.60, 0.70), "emissive": (0.35, 0.70, 1.00),
    },
}

DEFAULT_PALETTE = "stone"

# Roughness/metallic per role, so a palette swap keeps surfaces believable
# instead of turning stone into chrome.
SURFACE: dict[str, tuple[float, float]] = {  # role -> (roughness, metallic)
    "base": (0.85, 0.0),
    "accent": (0.90, 0.0),
    "roof": (0.75, 0.0),
    "trim": (0.65, 0.0),
    "metal": (0.35, 1.0),
    "emissive": (0.40, 0.0),
}


def palette_names() -> list[str]:
    return sorted(PALETTES)


def get_palette(name: str | None) -> dict[str, tuple[float, float, float]]:
    """Resolve a palette by name, falling back to the default. Unknown
    names fall back rather than raising: a colour is never worth failing a
    build over, and `list_archetypes` advertises the real ones."""
    if not name:
        return PALETTES[DEFAULT_PALETTE]
    return PALETTES.get(str(name).strip().lower(), PALETTES[DEFAULT_PALETTE])


def resolve(name: str | None, role: str) -> tuple[float, float, float]:
    palette = get_palette(name)
    return palette.get(role, palette["base"])


def rgba(colour: tuple[float, float, float], alpha: float = 1.0) -> tuple[float, float, float, float]:
    return (colour[0], colour[1], colour[2], alpha)


def describe(name: str | None = None) -> dict[str, Any]:
    resolved = DEFAULT_PALETTE if not name else str(name).strip().lower()
    if resolved not in PALETTES:
        resolved = DEFAULT_PALETTE
    return {
        "palette": resolved,
        "roles": {role: list(PALETTES[resolved][role]) for role in ROLES},
        "available": palette_names(),
    }
