"""`game3d_sculpt_*` — sculpting through spatial functions.

Every op takes an object name and a **region** dict — a small predicate
that selects vertices (a sphere in space, a box, a half-space above a
plane) — and applies a brush-equivalent operation to just those vertices.
The LLM specifies the region and the parameters; Blender does the mesh
work.

See `game3d/implicit.py` for the region + falloff maths, and
`game3d/sculpt.py` for the actual bmesh operations.
"""

from __future__ import annotations

from typing import Any

from ..game3d import sculpt
from ..server.mcp_server import registry, run_on_main


_REGION_DESC = (
    "Region spec: one of {sphere: [x,y,z,r]}, {box: [x1,y1,z1,x2,y2,z2], feather: f}, "
    "{axis_above: {axis: 'z', value: 0.5, feather: 0.1}}, or {all: true}. "
    "Optional 'falloff': 'smooth' (default) | 'linear' | 'constant' | 'sharp'."
)


@registry.tool(
    name="game3d_sculpt_start",
    description=(
        "Create a base mesh with uniform sculpt-friendly topology. Start every "
        "sculpting session with this — a mesh with wildly varying edge lengths "
        "punishes every op that follows. voxel_size (metres) sets the target "
        "edge length; 0.05 is good for a ~1m tall subject, 0.02 for a small prop."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "base_shape": {"type": "string", "enum": list(sculpt.BASE_SHAPES)},
            "voxel_size": {"type": "number", "minimum": 0.005, "maximum": 1.0},
            "name": {"type": "string"},
            "subdivisions": {"type": "integer", "minimum": 0, "maximum": 6,
                             "description": "For cube base, extra subdivisions before remesh."},
        },
        "additionalProperties": False,
    },
)
def game3d_sculpt_start(base_shape: str = "sphere", voxel_size: float = 0.05,
                        name: str = "sculpt", subdivisions: int = 4) -> dict[str, Any]:
    return run_on_main(sculpt.sculpt_start, base_shape, voxel_size, name, subdivisions,
                       timeout=120.0)


@registry.tool(
    name="game3d_sculpt_displace",
    description=(
        "Push vertices in a region along their normals. Positive distance adds "
        "material (clay-draw / inflate); negative removes (scrape / dent). This "
        "is the brush that builds volume — spheres for bulges, boxes for slabs. "
        + _REGION_DESC
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "region": {"type": "object", "description": _REGION_DESC},
            "distance": {"type": "number"},
            "falloff": {"type": "string", "enum": ["smooth", "linear", "constant", "sharp"]},
        },
        "required": ["name", "region", "distance"],
        "additionalProperties": False,
    },
)
def game3d_sculpt_displace(name: str, region: dict, distance: float,
                           falloff: str = "smooth") -> dict[str, Any]:
    return run_on_main(sculpt.sculpt_displace, name, region, distance, falloff)


@registry.tool(
    name="game3d_sculpt_smooth",
    description=(
        "Laplacian smooth of vertices in a region — the shift+brush that "
        "removes small features. Several light iterations (factor 0.3–0.5) "
        "give a more even result than one heavy one. " + _REGION_DESC
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "region": {"type": "object"},
            "iterations": {"type": "integer", "minimum": 1, "maximum": 20},
            "factor": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["name", "region"],
        "additionalProperties": False,
    },
)
def game3d_sculpt_smooth(name: str, region: dict, iterations: int = 3,
                         factor: float = 0.5) -> dict[str, Any]:
    return run_on_main(sculpt.sculpt_smooth, name, region, iterations, factor,
                       timeout=180.0)


@registry.tool(
    name="game3d_sculpt_pinch",
    description=(
        "Pull vertices toward the region centre — the crease brush. "
        "Good for sharpening a ridge or defining a corner. " + _REGION_DESC
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "region": {"type": "object"},
            "strength": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "falloff": {"type": "string"},
        },
        "required": ["name", "region"],
        "additionalProperties": False,
    },
)
def game3d_sculpt_pinch(name: str, region: dict, strength: float = 0.2,
                        falloff: str = "smooth") -> dict[str, Any]:
    return run_on_main(sculpt.sculpt_pinch, name, region, strength, falloff)


@registry.tool(
    name="game3d_sculpt_grab",
    description=(
        "Move vertices in the region by a vector, weighted by the falloff — "
        "the grab brush. For pulling a snout out, bending a limb, tilting "
        "an ear. " + _REGION_DESC
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "region": {"type": "object"},
            "vector": {"type": "array", "items": {"type": "number"},
                       "minItems": 3, "maxItems": 3},
            "falloff": {"type": "string"},
        },
        "required": ["name", "region", "vector"],
        "additionalProperties": False,
    },
)
def game3d_sculpt_grab(name: str, region: dict, vector: list,
                       falloff: str = "smooth") -> dict[str, Any]:
    return run_on_main(sculpt.sculpt_grab, name, region, tuple(vector), falloff)


@registry.tool(
    name="game3d_sculpt_from_metaballs",
    description=(
        "Combine a list of metaball primitives into a smooth organic mesh. "
        "The workhorse for organic sculpting: spec each part of the subject as "
        "a sphere / ellipsoid / capsule at a position, and Blender's implicit "
        "surface evaluator blends them seamlessly. Example primitives: "
        "[{type:'BALL',pos:[0,0,0],radius:1.0}, "
        "{type:'CAPSULE',pos:[1.2,0,0],size:[0.6,0.3,0.3]}]. "
        "resolution (smaller = smoother + slower) and threshold (default 0.6) "
        "control the mesh."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "primitives": {"type": "array", "minItems": 1,
                           "items": {"type": "object"}},
            "name": {"type": "string"},
            "resolution": {"type": "number", "minimum": 0.01, "maximum": 0.5},
            "threshold": {"type": "number", "minimum": 0.1, "maximum": 5.0},
        },
        "required": ["primitives"],
        "additionalProperties": False,
    },
)
def game3d_sculpt_from_metaballs(primitives: list, name: str = "meta_sculpt",
                                 resolution: float = 0.05,
                                 threshold: float = 0.6) -> dict[str, Any]:
    return run_on_main(sculpt.from_metaballs, primitives, name, resolution, threshold,
                       timeout=180.0)


@registry.tool(
    name="game3d_sculpt_from_sdf",
    description=(
        "Evaluate a signed-distance-field expression on a grid and mesh it. "
        "The expression is a small whitelisted DSL: primitives sphere((x,y,z),r), "
        "box((x,y,z),(w,h,d)), capsule((x1,y1,z1),(x2,y2,z2),r), torus((x,y,z),R,r); "
        "operators union(...), intersect(...), subtract(a,b), smooth_union(a,b,k), "
        "translate(fn,(dx,dy,dz)), scale(fn,factor). No imports or attribute "
        "access allowed. bounds is [x1,y1,z1,x2,y2,z2] tightly enclosing the "
        "shape; resolution samples along the longest axis (64 fast, 128 smooth)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "expression": {"type": "string"},
            "bounds": {"type": "array", "items": {"type": "number"},
                       "minItems": 6, "maxItems": 6},
            "resolution": {"type": "integer", "minimum": 16, "maximum": 256},
            "name": {"type": "string"},
        },
        "required": ["expression", "bounds"],
        "additionalProperties": False,
    },
)
def game3d_sculpt_from_sdf(expression: str, bounds: list, resolution: int = 64,
                           name: str = "sdf_sculpt") -> dict[str, Any]:
    return run_on_main(sculpt.from_sdf, expression, tuple(bounds), resolution, name,
                       timeout=300.0)
