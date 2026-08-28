"""Parametric building archetypes.

Every structure is built from the same handful of parameters — footprint,
storeys, roof style, palette — so the kit produces a coherent set rather
than a pile of unrelated models. Archetypes are shapes, not factions:
what a "shrine" means in a given game is the game's business.
"""

from __future__ import annotations

from typing import Any

import bpy

from . import geometry as geo
from . import scene as scn

ARCHETYPES = ("house", "tower", "wall", "gate", "storage", "shrine", "workshop")
ROOF_STYLES = ("hip", "pyramid", "cone", "flat")

DEFAULTS: dict[str, dict[str, Any]] = {
    "house":    {"footprint": (3.0, 2.4), "storeys": 1, "roof": "hip",     "roof_height": 1.1},
    "tower":    {"footprint": (2.0, 2.0), "storeys": 3, "roof": "cone",    "roof_height": 1.6},
    "wall":     {"footprint": (6.0, 0.8), "storeys": 1, "roof": "flat",    "roof_height": 0.0},
    "gate":     {"footprint": (5.0, 1.2), "storeys": 2, "roof": "flat",    "roof_height": 0.0},
    "storage":  {"footprint": (2.6, 2.6), "storeys": 1, "roof": "pyramid", "roof_height": 1.3},
    "shrine":   {"footprint": (2.2, 2.2), "storeys": 1, "roof": "pyramid", "roof_height": 1.8},
    "workshop": {"footprint": (3.6, 3.0), "storeys": 1, "roof": "hip",     "roof_height": 1.0},
}

STOREY_HEIGHT = 1.4


def list_archetypes() -> dict[str, Any]:
    return {
        "structures": [
            {"name": name, **{k: (list(v) if isinstance(v, tuple) else v)
                              for k, v in DEFAULTS[name].items()}}
            for name in ARCHETYPES
        ],
        "roof_styles": list(ROOF_STYLES),
    }


def _roof(name: str, style: str, footprint, height: float, top: float, materials, parts) -> None:
    width, depth = footprint
    overhang = 0.18
    base = (width + overhang * 2, depth + overhang * 2)
    if style == "flat" or height <= 0:
        parts.append(geo.box(f"{name}_parapet", (base[0], base[1], 0.22),
                             (0, 0, top + 0.11), materials["trim"]))
        return
    if style == "cone":
        parts.append(geo.cone(f"{name}_roof", radius_bottom=max(base) * 0.62, radius_top=0.0,
                              depth=height, origin=(0, 0, top + height / 2),
                              segments=16, material=materials["roof"]))
        return
    if style == "pyramid":
        parts.append(geo.pyramid(f"{name}_roof", base, height, (0, 0, top), materials["roof"]))
        return
    parts.append(geo.hip_roof(f"{name}_roof", base, height, 0.45, (0, 0, top), materials["roof"]))


def _openings(name: str, footprint, storeys: int, materials, parts) -> None:
    """Doors and windows as inset trim slabs. Boolean cuts would be more
    accurate and far more fragile in background mode; at game-asset scale
    the read is identical."""
    width, depth = footprint
    door_w, door_h = min(0.8, width * 0.3), 1.0
    parts.append(geo.box(f"{name}_door", (door_w, 0.12, door_h),
                         (0, -depth / 2 - 0.02, door_h / 2), materials["accent"]))
    for storey in range(storeys):
        z = STOREY_HEIGHT * storey + STOREY_HEIGHT * 0.62
        if storey == 0 and width < 2.0:
            continue
        for side in (-1, 1):
            x = side * width * 0.28
            parts.append(geo.box(f"{name}_win_{storey}_{side}", (0.42, 0.1, 0.42),
                                 (x, -depth / 2 - 0.01, z), materials["emissive"]))


def build(archetype: str = "house", palette: str | None = None, storeys: int | None = None,
          footprint=None, roof: str | None = None, name: str | None = None,
          detail: bool = True) -> dict[str, Any]:
    """Build one structure and return what it is and how big it came out."""
    key = (archetype or "house").strip().lower()
    if key not in DEFAULTS:
        raise ValueError(f"unknown structure {archetype!r}; try one of {', '.join(ARCHETYPES)}")

    spec = DEFAULTS[key]
    width, depth = tuple(footprint) if footprint else spec["footprint"]
    width, depth = max(0.4, float(width)), max(0.4, float(depth))
    levels = max(1, int(storeys if storeys is not None else spec["storeys"]))
    roof_style = (roof or spec["roof"]).strip().lower()
    if roof_style not in ROOF_STYLES:
        roof_style = spec["roof"]
    roof_height = spec["roof_height"] * (1.0 if roof_style != "flat" else 0.0)
    obj_name = name or f"g3d_{key}"

    materials = scn.palette_materials(palette)
    parts: list = []

    # Plinth — a base course stops the model looking like it is floating.
    parts.append(geo.box(f"{obj_name}_plinth", (width + 0.3, depth + 0.3, 0.2),
                         (0, 0, 0.1), materials["accent"]))

    body_top = 0.2
    for storey in range(levels):
        # Towers taper; everything else keeps its footprint.
        shrink = 1.0 - (0.08 * storey if key == "tower" else 0.0)
        w, d = width * shrink, depth * shrink
        centre = body_top + STOREY_HEIGHT / 2
        parts.append(geo.box(f"{obj_name}_body_{storey}", (w, d, STOREY_HEIGHT),
                             (0, 0, centre), materials["base"], bevel=0.02))
        if detail:
            parts.append(geo.box(f"{obj_name}_band_{storey}", (w + 0.08, d + 0.08, 0.12),
                                 (0, 0, body_top + STOREY_HEIGHT - 0.06), materials["trim"]))
        body_top += STOREY_HEIGHT

    if key == "gate":
        # An arch reads as a gate; a solid block reads as a wall.
        opening = min(1.8, width * 0.4)
        parts.append(geo.box(f"{obj_name}_arch", (opening, depth + 0.2, 1.6),
                             (0, 0, 0.2 + 0.8), materials["accent"]))
    elif key == "shrine" and detail:
        for side in (-1, 1):
            parts.append(geo.cylinder(f"{obj_name}_pillar_{side}", 0.16, STOREY_HEIGHT,
                                      (side * width * 0.36, -depth * 0.36,
                                       0.2 + STOREY_HEIGHT / 2), 12, materials["trim"]))
    elif key == "storage" and detail:
        parts.append(geo.cylinder(f"{obj_name}_silo", min(width, depth) * 0.32,
                                  STOREY_HEIGHT * 1.4,
                                  (width * 0.34, depth * 0.30, 0.2 + STOREY_HEIGHT * 0.7),
                                  16, materials["metal"]))

    if key != "wall" and detail:
        _openings(obj_name, (width, depth), levels, materials, parts)
    _roof(obj_name, roof_style, (width, depth), roof_height, body_top, materials, parts)

    if key == "wall" and detail:
        # Crenellations: the one detail that makes a box read as a wall.
        merlons = max(2, int(width // 0.7))
        for index in range(merlons):
            x = -width / 2 + (index + 0.5) * (width / merlons)
            parts.append(geo.box(f"{obj_name}_merlon_{index}", (width / merlons * 0.55, depth, 0.35),
                                 (x, 0, body_top + 0.32), materials["base"]))

    obj = geo.join(parts, obj_name)
    return {
        "object": obj.name,
        "archetype": key,
        "palette": scn.palettes.describe(palette)["palette"],
        "storeys": levels,
        "roof": roof_style,
        "footprint": [width, depth],
        "height": round(body_top + roof_height, 3),
        "bounds": geo.bounds(obj),
        "polygons": len(obj.data.polygons),
    }


def build_row(archetype: str = "wall", count: int = 3, spacing: float | None = None,
              palette: str | None = None) -> dict[str, Any]:
    """Repeat an archetype along X — walls and fences are never built one
    at a time."""
    count = max(1, min(24, int(count)))
    built = []
    step = spacing or (DEFAULTS.get(archetype, DEFAULTS["wall"])["footprint"][0])
    for index in range(count):
        info = build(archetype, palette=palette, name=f"g3d_{archetype}_{index}")
        obj = bpy.data.objects[info["object"]]
        obj.location.x = (index - (count - 1) / 2.0) * step
        built.append(info["object"])
    return {"objects": built, "count": count, "spacing": step}
