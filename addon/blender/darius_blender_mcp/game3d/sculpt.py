"""Sculpt-style mesh operations, driven by spatial functions rather than
brush strokes.

The insight this file is built around: a sculpt brush stroke does exactly
one thing — displace vertices within a radius by an amount that falls off
with distance. Nothing about that requires a mouse. Every function here
takes an object and a **region** dict (see `implicit.compile_region`) and
either displaces, smooths, pinches or grabs the vertices the region
selects. An LLM specifies the region and the parameters; Blender does
the mesh work.

Two "generator" helpers round it out:

* `from_metaballs` — combines a list of primitive spheres/ellipsoids into
  a smooth blob via Blender's native metaball system, then converts to
  a real mesh. This is the workhorse for organic sculpting because the
  spec ("a head is a big sphere plus a snout capsule plus two horn
  ellipsoids") is exactly what an LLM can reason about.

* `from_sdf` — evaluates a whitelisted SDF expression on a grid and
  marches it into a mesh. Same idea, sharper control. Uses
  `implicit.evaluate_sdf_expression` so the expression string can't do
  anything a whitelisted primitive doesn't do.
"""

from __future__ import annotations

from typing import Any

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

from . import implicit


BASE_SHAPES = ("sphere", "cube", "cylinder")


def _get_mesh_object(name: str):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"no object named {name!r}")
    if obj.type != "MESH":
        raise ValueError(f"object {name!r} is a {obj.type}, not a MESH")
    return obj


def _vert_positions_world(obj) -> np.ndarray:
    """Vertex coordinates in world space as an (N, 3) numpy array."""
    matrix = obj.matrix_world
    return np.asarray(
        [(matrix @ v.co).to_tuple() for v in obj.data.vertices],
        dtype=float,
    )


def _vert_normals_world(obj) -> np.ndarray:
    """Per-vertex normals in world space."""
    # rotation_part strips scale/translation; normals must not be shifted.
    rot = obj.matrix_world.to_3x3()
    normals = np.asarray(
        [(rot @ v.normal).normalized().to_tuple() for v in obj.data.vertices],
        dtype=float,
    )
    return normals


def _apply_local(obj, world_deltas: np.ndarray) -> None:
    """Add world-space per-vertex deltas back to a mesh in its local space."""
    inv = obj.matrix_world.inverted().to_3x3()
    for i, delta in enumerate(world_deltas):
        local = inv @ Vector(delta.tolist())
        obj.data.vertices[i].co += local
    obj.data.update()


# --------------------------------------------------------------------------- #
# Start: base mesh with uniform topology
# --------------------------------------------------------------------------- #

def sculpt_start(base_shape: str = "sphere", voxel_size: float = 0.05,
                 name: str = "sculpt", subdivisions: int = 4) -> dict[str, Any]:
    """Create a base mesh with uniform, sculpt-friendly topology.

    A sphere or cube gets subdivided so the polygon density is roughly
    even, then a Remesh (VOXEL) modifier is applied so the topology is
    truly uniform — a mesh with wildly varying edge lengths punishes
    every subsequent sculpt op. `voxel_size` sets the target edge length
    in metres; 0.05 is a good default for a ~1m tall subject.

    Returns the object name, world bounds and polygon count.
    """
    if base_shape not in BASE_SHAPES:
        raise ValueError(f"base_shape must be one of {BASE_SHAPES}; got {base_shape!r}")
    voxel_size = float(voxel_size)
    if voxel_size <= 0:
        raise ValueError("voxel_size must be > 0")

    bm = bmesh.new()
    if base_shape == "sphere":
        bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=0.5)
    elif base_shape == "cube":
        bmesh.ops.create_cube(bm, size=1.0)
        for _ in range(max(0, int(subdivisions))):
            bmesh.ops.subdivide_edges(
                bm, edges=bm.edges[:], cuts=1, use_grid_fill=True,
            )
    elif base_shape == "cylinder":
        bmesh.ops.create_cone(
            bm, cap_ends=True, cap_tris=False, segments=32,
            radius1=0.5, radius2=0.5, depth=1.0,
        )

    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)

    # Voxel remesh gives the uniform topology a sculpt-friendly mesh needs.
    # Applied non-destructively via a modifier so the caller can tweak
    # voxel_size later if the first pass came out too coarse or too fine.
    mod = obj.modifiers.new(name="Remesh", type="REMESH")
    mod.mode = "VOXEL"
    mod.voxel_size = voxel_size
    _apply_modifier(obj, mod.name)

    return {
        "name": obj.name,
        "base_shape": base_shape,
        "voxel_size": voxel_size,
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
        "bounds": _bounds(obj),
    }


def _apply_modifier(obj, modifier_name: str) -> None:
    """Bake a modifier into the mesh without needing an active object /
    view layer, which background renders do not reliably have."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    new_mesh = bpy.data.meshes.new_from_object(evaluated)
    old_mesh = obj.data
    obj.modifiers.remove(obj.modifiers[modifier_name])
    obj.data = new_mesh
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)


def _bounds(obj) -> dict[str, list[float]]:
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    return {
        "min": [min(xs), min(ys), min(zs)],
        "max": [max(xs), max(ys), max(zs)],
        "size": [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)],
    }


# --------------------------------------------------------------------------- #
# Displace / inflate — the "clay draw" family
# --------------------------------------------------------------------------- #

def sculpt_displace(name: str, region: dict, distance: float,
                    falloff: str = "smooth") -> dict[str, Any]:
    """Push vertices in the region along their normals by `distance`.

    Positive distance adds material (clay-draw / inflate); negative
    removes (scrape / dent). `falloff` shapes the transition between the
    region's centre and its edge — see `implicit.compile_region`.
    """
    obj = _get_mesh_object(name)
    region_spec = dict(region)
    region_spec.setdefault("falloff", falloff)
    weight_fn, _ = implicit.compile_region(region_spec)

    positions = _vert_positions_world(obj)
    normals = _vert_normals_world(obj)
    weights = weight_fn(positions)
    deltas = normals * (weights * float(distance))[:, None]

    _apply_local(obj, deltas)
    affected = int((weights > 0).sum())
    return {
        "name": obj.name,
        "affected_vertices": affected,
        "distance": float(distance),
        "falloff": region_spec["falloff"],
    }


# --------------------------------------------------------------------------- #
# Smooth — the "shift+brush" that removes small features
# --------------------------------------------------------------------------- #

def sculpt_smooth(name: str, region: dict, iterations: int = 3,
                  factor: float = 0.5) -> dict[str, Any]:
    """Laplacian smooth of vertices in the region.

    Each iteration replaces every affected vertex with a blend of its
    current position and the mean of its edge-connected neighbours,
    weighted by the region function. Multiple light iterations give a
    more even result than one heavy one.
    """
    obj = _get_mesh_object(name)
    region_spec = dict(region)
    weight_fn, _ = implicit.compile_region(region_spec)

    matrix_world = obj.matrix_world
    inv = matrix_world.inverted().to_3x3()

    positions_world = _vert_positions_world(obj)
    weights = weight_fn(positions_world)

    # Adjacency built once — bmesh.ops.smooth_vert also works but does the
    # whole mesh; we need a per-vertex weight so we roll our own.
    mesh = obj.data
    mesh.calc_loop_triangles()
    n = len(mesh.vertices)
    neighbours: list[list[int]] = [[] for _ in range(n)]
    for edge in mesh.edges:
        a, b = edge.vertices
        neighbours[a].append(b)
        neighbours[b].append(a)

    positions_local = np.asarray(
        [v.co.to_tuple() for v in mesh.vertices], dtype=float,
    )
    factor = float(np.clip(factor, 0.0, 1.0))
    affected = int((weights > 0).sum())

    for _ in range(max(1, int(iterations))):
        new_positions = positions_local.copy()
        for i in range(n):
            if weights[i] <= 0 or not neighbours[i]:
                continue
            mean = positions_local[neighbours[i]].mean(axis=0)
            blend = (1.0 - factor * weights[i]) * positions_local[i] + \
                    (factor * weights[i]) * mean
            new_positions[i] = blend
        positions_local = new_positions

    for i, pos in enumerate(positions_local):
        mesh.vertices[i].co = Vector(pos.tolist())
    mesh.update()

    return {
        "name": obj.name,
        "iterations": int(iterations),
        "affected_vertices": affected,
        "factor": factor,
    }


# --------------------------------------------------------------------------- #
# Pinch — pull vertices toward the region centre
# --------------------------------------------------------------------------- #

def sculpt_pinch(name: str, region: dict, strength: float = 0.2,
                 falloff: str = "smooth") -> dict[str, Any]:
    """Pull affected vertices toward the region's centre — the crease
    brush. `strength` is the fraction of the vertex-to-centre distance
    to move each call."""
    obj = _get_mesh_object(name)
    region_spec = dict(region)
    region_spec.setdefault("falloff", falloff)
    weight_fn, centre = implicit.compile_region(region_spec)

    positions = _vert_positions_world(obj)
    weights = weight_fn(positions)
    to_centre = centre[None, :] - positions
    deltas = to_centre * (weights * float(strength))[:, None]

    _apply_local(obj, deltas)
    return {
        "name": obj.name,
        "affected_vertices": int((weights > 0).sum()),
        "strength": float(strength),
    }


# --------------------------------------------------------------------------- #
# Grab — translate a region as a soft group
# --------------------------------------------------------------------------- #

def sculpt_grab(name: str, region: dict, vector: tuple[float, float, float],
                falloff: str = "smooth") -> dict[str, Any]:
    """Move vertices in the region by `vector`, weighted by the falloff.
    The classic "grab" brush — for pulling a snout out or bending a limb."""
    obj = _get_mesh_object(name)
    region_spec = dict(region)
    region_spec.setdefault("falloff", falloff)
    weight_fn, _ = implicit.compile_region(region_spec)

    positions = _vert_positions_world(obj)
    weights = weight_fn(positions)
    v = np.asarray(vector, dtype=float)
    deltas = v[None, :] * weights[:, None]

    _apply_local(obj, deltas)
    return {
        "name": obj.name,
        "affected_vertices": int((weights > 0).sum()),
        "vector": [float(x) for x in vector],
    }


# --------------------------------------------------------------------------- #
# Metaball composition — the workhorse for organic sculpting
# --------------------------------------------------------------------------- #

METABALL_TYPES = ("BALL", "ELLIPSOID", "CAPSULE", "CUBE", "PLANE")


def from_metaballs(primitives: list[dict], name: str = "meta_sculpt",
                   resolution: float = 0.05, threshold: float = 0.6) -> dict[str, Any]:
    """Combine a list of metaball primitives into a smooth mesh.

    Each primitive is a dict::

        {"type": "BALL"|"ELLIPSOID"|"CAPSULE"|"CUBE"|"PLANE",
         "pos": [x, y, z],
         "radius": float,        # radius (for BALL) or half-size along X
         "size":   [x, y, z],    # optional, for ELLIPSOID/CUBE/CAPSULE
         "rotation": [rx, ry, rz],  # optional Euler radians
         "stiffness": float}     # optional influence, default 2.0

    Blender combines them via its native implicit-surface evaluator (the
    same one metaballs use interactively), and the result is converted
    to a real editable mesh so the sculpt tools above can push it around.
    `resolution` is Blender's `mball.resolution` — smaller = smoother +
    slower. `threshold` is the iso value; the default 0.6 gives clean
    joins without dropping the primitives.
    """
    mball = bpy.data.metaballs.new(f"{name}_mb")
    mball.resolution = max(0.01, float(resolution))
    mball.threshold = float(threshold)

    for i, prim in enumerate(primitives):
        kind = prim.get("type", "BALL").upper()
        if kind not in METABALL_TYPES:
            raise ValueError(f"metaball type must be one of {METABALL_TYPES}; got {kind!r}")
        element = mball.elements.new(type=kind)
        pos = prim.get("pos", [0, 0, 0])
        element.co = Vector((float(pos[0]), float(pos[1]), float(pos[2])))
        if "radius" in prim:
            element.radius = float(prim["radius"])
        if "size" in prim:
            sx, sy, sz = prim["size"]
            element.size_x = float(sx)
            element.size_y = float(sy)
            element.size_z = float(sz)
        if "rotation" in prim:
            rx, ry, rz = prim["rotation"]
            element.rotation = (
                Matrix.Rotation(rx, 4, "X")
                @ Matrix.Rotation(ry, 4, "Y")
                @ Matrix.Rotation(rz, 4, "Z")
            ).to_quaternion()
        if "stiffness" in prim:
            element.stiffness = float(prim["stiffness"])

    meta_obj = bpy.data.objects.new(f"{name}_meta", mball)
    bpy.context.scene.collection.objects.link(meta_obj)

    # Metaballs only compute their mesh via the depsgraph — until then
    # `bpy.data.meshes.new_from_object` returns an empty mesh.
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = meta_obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated)
    mesh.name = f"{name}_mesh"

    result = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(result)

    # The metaball object was only ever a builder; the caller wants the
    # baked mesh, and leaving the metaball around would keep influencing
    # any future baked pass.
    bpy.data.objects.remove(meta_obj, do_unlink=True)
    bpy.data.metaballs.remove(mball)

    return {
        "name": result.name,
        "primitives": len(primitives),
        "vertices": len(result.data.vertices),
        "polygons": len(result.data.polygons),
        "bounds": _bounds(result),
    }


# --------------------------------------------------------------------------- #
# SDF expression → mesh
# --------------------------------------------------------------------------- #

def from_sdf(expression: str, bounds: tuple[float, float, float, float, float, float],
             resolution: int = 64, name: str = "sdf_sculpt") -> dict[str, Any]:
    """Evaluate an SDF expression on a grid inside `bounds` and mesh it.

    The expression is compiled by `implicit.evaluate_sdf_expression` —
    only the whitelisted primitives (sphere, box, capsule, torus) and
    operators (union, intersect, subtract, smooth_union, translate,
    scale) are in scope. `bounds` is `[x1, y1, z1, x2, y2, z2]` and
    should tightly enclose the shape; a loose bound wastes marching-
    cubes samples on empty space. `resolution` is the number of samples
    along the longest bound axis; 64 is a fast preview, 128 is smoother.
    """
    fn = implicit.evaluate_sdf_expression(expression)
    volume, origin = implicit.sample_sdf(fn, bounds, resolution)
    x1, _, _, x2, _, _ = bounds
    spacing = (x2 - x1) / (resolution - 1) if resolution > 1 else 1.0
    verts, faces = implicit.marching_cubes(volume, origin, spacing, iso=0.0)

    if len(verts) == 0:
        raise ValueError(
            "SDF surface did not intersect the bounds — either the shape is "
            "empty at iso=0 or the bounds are too small to contain it."
        )

    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(verts.tolist(), [], faces.tolist())
    mesh.validate()
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)

    return {
        "name": obj.name,
        "expression": expression,
        "resolution": int(resolution),
        "vertices": len(verts),
        "polygons": len(faces),
        "bounds_used": list(bounds),
        "surface_bounds": _bounds(obj),
    }
