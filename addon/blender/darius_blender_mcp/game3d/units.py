"""Parametric unit archetypes.

Blocky, low-poly figures in the shape game art actually ships: readable
silhouette first, detail second. Archetypes describe a *role* — what the
figure carries and how it stands — not a faction.
"""

from __future__ import annotations

import math
from typing import Any

from . import geometry as geo
from . import scene as scn

ARCHETYPES = ("worker", "melee", "ranged", "mounted", "caster")

DEFAULTS: dict[str, dict[str, Any]] = {
    "worker": {"height": 1.8, "build": 0.95, "weapon": "tool",  "stance": 0.10},
    "melee":  {"height": 1.9, "build": 1.15, "weapon": "sword", "stance": 0.22},
    "ranged": {"height": 1.85, "build": 1.0, "weapon": "bow",   "stance": 0.16},
    "mounted": {"height": 2.4, "build": 1.2, "weapon": "lance", "stance": 0.30},
    "caster": {"height": 1.85, "build": 0.9, "weapon": "staff", "stance": 0.12},
}

WEAPONS = ("none", "tool", "sword", "bow", "lance", "staff")


def list_archetypes() -> dict[str, Any]:
    return {
        "units": [{"name": name, **DEFAULTS[name]} for name in ARCHETYPES],
        "weapons": list(WEAPONS),
    }


def _weapon(name: str, kind: str, hand, materials, parts) -> None:
    x, y, z = hand
    if kind == "sword":
        parts.append(geo.box(f"{name}_blade", (0.07, 0.07, 0.85), (x, y, z + 0.42), materials["metal"]))
        parts.append(geo.box(f"{name}_guard", (0.26, 0.09, 0.06), (x, y, z), materials["trim"]))
    elif kind == "lance":
        parts.append(geo.cylinder(f"{name}_shaft", 0.045, 1.9, (x, y, z + 0.55), 8,
                                  materials["trim"], rotation=(math.radians(12), 0, 0)))
        parts.append(geo.cone(f"{name}_tip", 0.09, 0.0, 0.28, (x, y - 0.2, z + 1.45), 8,
                              materials["metal"]))
    elif kind == "bow":
        parts.append(geo.box(f"{name}_bow", (0.06, 0.10, 1.05), (x, y, z + 0.30), materials["trim"]))
        parts.append(geo.box(f"{name}_string", (0.02, 0.02, 1.0), (x, y - 0.12, z + 0.30),
                             materials["accent"]))
    elif kind == "staff":
        parts.append(geo.cylinder(f"{name}_staff", 0.05, 1.7, (x, y, z + 0.45), 8, materials["trim"]))
        parts.append(geo.sphere(f"{name}_orb", 0.13, (x, y, z + 1.30), 12, 8, materials["emissive"]))
    elif kind == "tool":
        parts.append(geo.cylinder(f"{name}_handle", 0.045, 1.1, (x, y, z + 0.25), 8, materials["trim"]))
        parts.append(geo.box(f"{name}_head", (0.28, 0.10, 0.14), (x, y, z + 0.78), materials["metal"]))


def build(archetype: str = "melee", palette: str | None = None, height: float | None = None,
          build_factor: float | None = None, weapon: str | None = None,
          name: str | None = None, detail: bool = True) -> dict[str, Any]:
    key = (archetype or "melee").strip().lower()
    if key not in DEFAULTS:
        raise ValueError(f"unknown unit {archetype!r}; try one of {', '.join(ARCHETYPES)}")

    spec = DEFAULTS[key]
    total = max(0.6, float(height if height is not None else spec["height"]))
    girth = max(0.5, float(build_factor if build_factor is not None else spec["build"]))
    weapon_kind = (weapon or spec["weapon"]).strip().lower()
    if weapon_kind not in WEAPONS:
        weapon_kind = spec["weapon"]
    obj_name = name or f"g3d_{key}"

    materials = scn.palette_materials(palette)
    parts: list = []

    mounted = key == "mounted"
    ground = 0.0
    if mounted:
        # A simple mount: body, four legs, neck and head. The rider is
        # built on top by the same code that builds everyone else.
        body_z = total * 0.42
        parts.append(geo.box(f"{obj_name}_mount", (1.5 * girth, 0.62 * girth, 0.62),
                             (0, 0, body_z), materials["accent"], bevel=0.05))
        for sx in (-1, 1):
            for sy in (-1, 1):
                parts.append(geo.box(f"{obj_name}_leg_{sx}_{sy}", (0.15, 0.15, body_z - 0.3),
                                     (sx * 0.55 * girth, sy * 0.22 * girth, (body_z - 0.3) / 2),
                                     materials["accent"]))
        parts.append(geo.box(f"{obj_name}_neck", (0.28, 0.28, 0.55),
                             (0.72 * girth, 0, body_z + 0.38), materials["accent"]))
        parts.append(geo.box(f"{obj_name}_head", (0.44, 0.26, 0.26),
                             (0.86 * girth, 0, body_z + 0.70), materials["accent"]))
        ground = body_z + 0.31

    rider_height = total * (0.62 if mounted else 1.0)
    leg_h = rider_height * 0.42
    torso_h = rider_height * 0.36
    head_r = rider_height * 0.085
    shoulder = 0.40 * girth

    if not mounted:
        for side in (-1, 1):
            parts.append(geo.box(f"{obj_name}_leg_{side}", (0.19 * girth, 0.21 * girth, leg_h),
                                 (side * 0.14 * girth, side * spec["stance"] * 0.3, ground + leg_h / 2),
                                 materials["accent"]))
    torso_z = ground + (leg_h if not mounted else 0.0) + torso_h / 2
    parts.append(geo.box(f"{obj_name}_torso", (shoulder, 0.30 * girth, torso_h),
                         (0, 0, torso_z), materials["base"], bevel=0.03))
    if detail:
        parts.append(geo.box(f"{obj_name}_belt", (shoulder + 0.03, 0.33 * girth, 0.10),
                             (0, 0, torso_z - torso_h / 2 + 0.08), materials["trim"]))

    head_z = torso_z + torso_h / 2 + head_r
    parts.append(geo.sphere(f"{obj_name}_head", head_r, (0, 0, head_z), 16, 10, materials["trim"]))

    arm_h = torso_h * 0.92
    hand = (0.0, 0.0, 0.0)
    for side in (-1, 1):
        x = side * (shoulder / 2 + 0.09 * girth)
        parts.append(geo.box(f"{obj_name}_arm_{side}", (0.15 * girth, 0.15 * girth, arm_h),
                             (x, 0, torso_z + torso_h / 2 - arm_h / 2), materials["base"]))
        if side == 1:
            hand = (x, -0.10, torso_z + torso_h / 2 - arm_h)

    if weapon_kind != "none":
        _weapon(obj_name, weapon_kind, hand, materials, parts)

    obj = geo.join(parts, obj_name)
    return {
        "object": obj.name,
        "archetype": key,
        "palette": scn.palettes.describe(palette)["palette"],
        "height": round(total, 3),
        "build": round(girth, 3),
        "weapon": weapon_kind,
        "mounted": mounted,
        "bounds": geo.bounds(obj),
        "polygons": len(obj.data.polygons),
    }
