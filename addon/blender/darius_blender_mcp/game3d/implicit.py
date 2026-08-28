"""Region predicates, falloff curves, and a signed-distance-field mesher.

Deliberately `bpy`-free: every function here takes numbers/tuples and
returns numbers/tuples/arrays. That way the sculpting maths — which is
where an implementation bug hides for the longest, because the visual
output makes it look "sort of right" — is tested outside Blender against
the same asserts a human would eyeball.

Two things live here:

* **Region + falloff** — the spatial predicates the sculpt tools use to
  decide which vertices to touch and by how much. A region is a small
  dict the agent sends over the wire (`{"sphere": [x, y, z, r]}`), which
  compiles here into a fast per-vertex weight function.

* **SDF primitives + a marcher** — signed-distance functions for the
  common primitives (sphere, box, capsule, torus), a smooth-union so
  organic combinations don't have hard seams, and a plain marching-cubes
  mesher that turns any SDF into a triangle mesh. No external mesher
  required: numpy carries the grid and a compact 256-case table does the
  extraction.

None of this touches Blender. `sculpt.py` is the file that hands the
outputs to `bmesh` / `bpy.data.meshes`.
"""

from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np

# --------------------------------------------------------------------------- #
# Regions & falloff
# --------------------------------------------------------------------------- #

FalloffFn = Callable[[float], float]
RegionFn = Callable[[np.ndarray], np.ndarray]  # (N,3) -> (N,) weights in [0,1]


def falloff_smooth(t: float) -> float:
    """Smoothstep 3t² − 2t³. The default sculpt curve — soft edges without
    the parabolic bulge a plain quadratic produces at the tip."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def falloff_linear(t: float) -> float:
    return max(0.0, min(1.0, t))


def falloff_constant(t: float) -> float:
    return 1.0 if 0.0 <= t <= 1.0 else 0.0


def falloff_sharp(t: float) -> float:
    """Rises fast, plateaus — the "hard brush" curve for creasing."""
    t = max(0.0, min(1.0, t))
    return t * t


FALLOFFS: dict[str, FalloffFn] = {
    "smooth": falloff_smooth,
    "linear": falloff_linear,
    "constant": falloff_constant,
    "sharp": falloff_sharp,
}


def _vectorize(fn: FalloffFn) -> Callable[[np.ndarray], np.ndarray]:
    """Wrap a scalar falloff so it can be called on a numpy array. Vector
    inputs are the common case (whole vertex batches at once); a python
    for-loop over 20k verts is unusably slow."""
    return np.vectorize(fn, otypes=[float])


def compile_region(spec: dict[str, Any]) -> tuple[RegionFn, np.ndarray]:
    """Turn a region spec into `(weight_fn, centre)`.

    `weight_fn(points)` returns weights in [0, 1] for every point. `centre`
    is the region's spatial anchor, used by ops that need a direction
    (pinch, grab) rather than a scalar weight.

    Supported spec keys:

    * ``sphere``: ``[x, y, z, r]`` — smooth ball of radius `r`.
    * ``box``: ``[x1, y1, z1, x2, y2, z2]`` — axis-aligned box; falloff
      is applied on distance from the nearest face, so the interior is 1
      and the outside decays over `feather` (default 0).
    * ``axis_above``: ``{"axis": "z", "value": 0.5, "feather": 0.1}`` —
      half-space; useful for "the top half of the mesh".
    * ``all``: ``true`` — every vertex gets weight 1.

    The optional ``falloff`` key ("smooth" | "linear" | "constant" |
    "sharp") controls the curve. Default is smooth.
    """
    falloff_name = spec.get("falloff", "smooth")
    falloff = FALLOFFS.get(falloff_name, falloff_smooth)
    vfalloff = _vectorize(falloff)

    if "sphere" in spec:
        x, y, z, r = spec["sphere"]
        centre = np.asarray([x, y, z], dtype=float)
        radius = float(r)
        if radius <= 0:
            raise ValueError("sphere radius must be > 0")

        def _fn(points: np.ndarray) -> np.ndarray:
            d = np.linalg.norm(points - centre, axis=1)
            # t=1 at centre, t=0 at the edge — falloff hits the 1→0 range.
            t = np.clip(1.0 - d / radius, 0.0, 1.0)
            return vfalloff(t)

        return _fn, centre

    if "box" in spec:
        x1, y1, z1, x2, y2, z2 = spec["box"]
        lo = np.asarray([min(x1, x2), min(y1, y2), min(z1, z2)], dtype=float)
        hi = np.asarray([max(x1, x2), max(y1, y2), max(z1, z2)], dtype=float)
        feather = float(spec.get("feather", 0.0))
        centre = (lo + hi) / 2.0

        def _fn(points: np.ndarray) -> np.ndarray:
            # Signed distance to box: negative inside, positive outside.
            q = np.maximum(lo - points, points - hi)
            outside = np.linalg.norm(np.maximum(q, 0.0), axis=1)
            inside = np.minimum(np.max(q, axis=1), 0.0)
            d = outside + inside
            if feather <= 0:
                return (d <= 0).astype(float)
            t = np.clip(1.0 - np.maximum(d, 0.0) / feather, 0.0, 1.0)
            return vfalloff(t)

        return _fn, centre

    if "axis_above" in spec:
        cfg = spec["axis_above"]
        axis_map = {"x": 0, "y": 1, "z": 2}
        axis = axis_map[cfg["axis"].lower()]
        value = float(cfg["value"])
        feather = float(cfg.get("feather", 0.0))
        centre = np.zeros(3, dtype=float)
        centre[axis] = value

        def _fn(points: np.ndarray) -> np.ndarray:
            delta = points[:, axis] - value
            if feather <= 0:
                return (delta >= 0).astype(float)
            t = np.clip(delta / feather, 0.0, 1.0)
            return vfalloff(t)

        return _fn, centre

    if spec.get("all") is True:
        centre = np.zeros(3, dtype=float)

        def _fn(points: np.ndarray) -> np.ndarray:
            return np.ones(len(points), dtype=float)

        return _fn, centre

    raise ValueError(f"unknown region spec: {list(spec.keys())}")


# --------------------------------------------------------------------------- #
# SDF primitives & operators
# --------------------------------------------------------------------------- #

SdfFn = Callable[[np.ndarray], np.ndarray]  # (N,3) -> (N,) signed distance


def sdf_sphere(centre: tuple[float, float, float], radius: float) -> SdfFn:
    c = np.asarray(centre, dtype=float)
    r = float(radius)

    def _fn(points: np.ndarray) -> np.ndarray:
        return np.linalg.norm(points - c, axis=1) - r
    return _fn


def sdf_box(centre: tuple[float, float, float], size: tuple[float, float, float]) -> SdfFn:
    c = np.asarray(centre, dtype=float)
    half = np.asarray(size, dtype=float) / 2.0

    def _fn(points: np.ndarray) -> np.ndarray:
        q = np.abs(points - c) - half
        outside = np.linalg.norm(np.maximum(q, 0.0), axis=1)
        inside = np.minimum(np.max(q, axis=1), 0.0)
        return outside + inside
    return _fn


def sdf_capsule(a: tuple[float, float, float], b: tuple[float, float, float],
                radius: float) -> SdfFn:
    """Cylinder with hemispherical caps between points a and b."""
    pa = np.asarray(a, dtype=float)
    pb = np.asarray(b, dtype=float)
    r = float(radius)
    ab = pb - pa
    ab_len_sq = float(np.dot(ab, ab))
    if ab_len_sq < 1e-12:
        return sdf_sphere(a, r)

    def _fn(points: np.ndarray) -> np.ndarray:
        pa_p = points - pa
        t = np.clip(pa_p @ ab / ab_len_sq, 0.0, 1.0).reshape(-1, 1)
        return np.linalg.norm(pa_p - t * ab, axis=1) - r
    return _fn


def sdf_torus(centre: tuple[float, float, float], major_radius: float,
              minor_radius: float) -> SdfFn:
    c = np.asarray(centre, dtype=float)
    R = float(major_radius)
    r = float(minor_radius)

    def _fn(points: np.ndarray) -> np.ndarray:
        p = points - c
        # Torus lies in the XY plane; distance from the circle of radius R.
        xy = np.linalg.norm(p[:, :2], axis=1) - R
        return np.sqrt(xy * xy + p[:, 2] * p[:, 2]) - r
    return _fn


def sdf_union(*fns: SdfFn) -> SdfFn:
    if not fns:
        raise ValueError("sdf_union needs at least one primitive")

    def _fn(points: np.ndarray) -> np.ndarray:
        return np.min(np.stack([f(points) for f in fns], axis=0), axis=0)
    return _fn


def sdf_intersect(*fns: SdfFn) -> SdfFn:
    if not fns:
        raise ValueError("sdf_intersect needs at least one primitive")

    def _fn(points: np.ndarray) -> np.ndarray:
        return np.max(np.stack([f(points) for f in fns], axis=0), axis=0)
    return _fn


def sdf_subtract(a: SdfFn, b: SdfFn) -> SdfFn:
    def _fn(points: np.ndarray) -> np.ndarray:
        return np.maximum(a(points), -b(points))
    return _fn


def sdf_smooth_union(a: SdfFn, b: SdfFn, k: float = 0.1) -> SdfFn:
    """Polynomial smooth-min — the blob-blend that gives metaballs their
    seamless joins. `k` is the smoothing radius; 0 is a hard union."""
    k = max(1e-6, float(k))

    def _fn(points: np.ndarray) -> np.ndarray:
        da = a(points)
        db = b(points)
        h = np.clip(0.5 + 0.5 * (db - da) / k, 0.0, 1.0)
        return db * (1.0 - h) + da * h - k * h * (1.0 - h)
    return _fn


def sdf_translate(fn: SdfFn, offset: tuple[float, float, float]) -> SdfFn:
    off = np.asarray(offset, dtype=float)

    def _wrapped(points: np.ndarray) -> np.ndarray:
        return fn(points - off)
    return _wrapped


def sdf_scale(fn: SdfFn, factor: float) -> SdfFn:
    s = float(factor)
    if s <= 0:
        raise ValueError("scale factor must be > 0")

    def _wrapped(points: np.ndarray) -> np.ndarray:
        return fn(points / s) * s
    return _wrapped


# --------------------------------------------------------------------------- #
# Safe SDF expression evaluator
# --------------------------------------------------------------------------- #

_SAFE_SDF_BUILTINS = {
    "sphere": sdf_sphere,
    "box": sdf_box,
    "capsule": sdf_capsule,
    "torus": sdf_torus,
    "union": sdf_union,
    "intersect": sdf_intersect,
    "subtract": sdf_subtract,
    "smooth_union": sdf_smooth_union,
    "translate": sdf_translate,
    "scale": sdf_scale,
}


def evaluate_sdf_expression(expression: str) -> SdfFn:
    """Compile a whitelisted SDF expression into a callable.

    The expression is a small DSL: only the primitives and operators from
    `_SAFE_SDF_BUILTINS` are in scope, plus numeric literals and tuples.
    No imports, no attribute access, no arbitrary calls — an LLM-generated
    string cannot spawn a subprocess or read a file this way.

    Example valid expressions::

        sphere((0,0,0), 1.0)
        smooth_union(sphere((0,0,0), 1.0), sphere((0.5,0,0), 0.7), 0.15)
        subtract(box((0,0,0), (2,2,0.5)), sphere((0,0,0.25), 0.6))
    """
    import ast

    tree = ast.parse(expression.strip(), mode="eval")

    allowed_nodes = (
        ast.Expression, ast.Call, ast.Name, ast.Load, ast.Constant,
        ast.Tuple, ast.List, ast.UnaryOp, ast.USub, ast.UAdd,
        ast.keyword,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            raise ValueError(f"disallowed syntax in SDF expression: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("SDF calls must be direct names (no attribute access)")
            if node.func.id not in _SAFE_SDF_BUILTINS:
                raise ValueError(f"unknown SDF primitive: {node.func.id!r}")
        if isinstance(node, ast.Name) and node.id not in _SAFE_SDF_BUILTINS:
            raise ValueError(f"unknown name in SDF expression: {node.id!r}")

    code = compile(tree, "<sdf>", "eval")
    return eval(code, {"__builtins__": {}}, dict(_SAFE_SDF_BUILTINS))


# --------------------------------------------------------------------------- #
# Marching cubes
# --------------------------------------------------------------------------- #
#
# Compact standard MC. The 256-case triangle table is loaded lazily — its
# 256×16 shape makes it the largest constant in the module and the mesher
# is only useful once you have Blender in the picture. Reference: Paul
# Bourke's public-domain implementation.
#
# For a first-cut sculpting toolkit this is good enough; dual contouring
# would give sharper features but at 3× the code and a table of its own.


def sample_sdf(fn: SdfFn, bounds: tuple[float, float, float, float, float, float],
               resolution: int) -> tuple[np.ndarray, np.ndarray]:
    """Evaluate `fn` on a regular grid inside `bounds`.

    Returns `(volume, origin)` where `volume` is a (N, N, N) numpy array
    of signed distances and `origin` is the world-space (x, y, z) of the
    (0, 0, 0) sample. Grid spacing is uniform in the longest axis; a
    non-cubic bound is sampled at the same spacing on all three axes to
    keep marching cubes honest.
    """
    x1, y1, z1, x2, y2, z2 = bounds
    resolution = max(4, int(resolution))
    ax = np.linspace(x1, x2, resolution)
    ay = np.linspace(y1, y2, resolution)
    az = np.linspace(z1, z2, resolution)
    xs, ys, zs = np.meshgrid(ax, ay, az, indexing="ij")
    points = np.stack([xs.ravel(), ys.ravel(), zs.ravel()], axis=1)
    values = fn(points).reshape(resolution, resolution, resolution)
    origin = np.asarray([x1, y1, z1], dtype=float)
    return values, origin


def marching_cubes(volume: np.ndarray, origin: np.ndarray, spacing: float,
                   iso: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Extract an iso-surface from a scalar volume. Returns `(vertices,
    faces)` as numpy arrays. `vertices` is (V, 3), `faces` is (F, 3) of
    int indices into `vertices`.

    Prefer `skimage.measure.marching_cubes` when it is available — the
    output is noticeably cleaner (linear interp on the actual iso value,
    not the midpoint) — and fall back to the hand-rolled midpoint
    extractor otherwise so the sculpting tool works without an extra
    dependency in Blender's Python.
    """
    try:
        from skimage.measure import marching_cubes as sk_mc
        verts, faces, _, _ = sk_mc(volume, level=iso, spacing=(spacing,) * 3)
        return verts + origin, faces.astype(np.int32)
    except Exception:
        return _midpoint_mc(volume, origin, spacing, iso)


# Edge lookup table (12 edges of the unit cube, each as pair of corners).
_MC_EDGES = np.array([
    [0, 1], [1, 2], [2, 3], [3, 0],
    [4, 5], [5, 6], [6, 7], [7, 4],
    [0, 4], [1, 5], [2, 6], [3, 7],
], dtype=np.int32)

_MC_CORNER_OFFSETS = np.array([
    [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
    [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
], dtype=np.int32)


def _midpoint_mc(volume: np.ndarray, origin: np.ndarray, spacing: float,
                 iso: float) -> tuple[np.ndarray, np.ndarray]:
    """Fallback marcher: emits a fixed triangle set per case using the
    canonical 256-case table. Interpolation is midpoint (not linear on
    the iso value), which produces a slightly rougher surface than
    scikit-image but keeps this module free of external deps."""
    tri_table = _mc_tri_table()
    verts: list[list[float]] = []
    faces: list[list[int]] = []
    vert_cache: dict[tuple[int, int, int, int], int] = {}
    nx, ny, nz = volume.shape

    for i in range(nx - 1):
        for j in range(ny - 1):
            for k in range(nz - 1):
                cube_vals = np.array([
                    volume[i + _MC_CORNER_OFFSETS[c, 0],
                           j + _MC_CORNER_OFFSETS[c, 1],
                           k + _MC_CORNER_OFFSETS[c, 2]]
                    for c in range(8)
                ])
                mask = 0
                for c in range(8):
                    if cube_vals[c] < iso:
                        mask |= 1 << c
                if mask == 0 or mask == 255:
                    continue
                tris = tri_table[mask]
                for t in range(0, len(tris), 3):
                    if tris[t] == -1:
                        break
                    tri = []
                    for e_idx in tris[t:t + 3]:
                        a, b = _MC_EDGES[e_idx]
                        key = (i, j, k, int(e_idx))
                        v = vert_cache.get(key)
                        if v is None:
                            ca = _MC_CORNER_OFFSETS[a]
                            cb = _MC_CORNER_OFFSETS[b]
                            va = cube_vals[a]
                            vb = cube_vals[b]
                            denom = (vb - va)
                            t_lerp = 0.5 if abs(denom) < 1e-9 else (iso - va) / denom
                            t_lerp = float(np.clip(t_lerp, 0.0, 1.0))
                            pa = np.array([i + ca[0], j + ca[1], k + ca[2]], dtype=float)
                            pb = np.array([i + cb[0], j + cb[1], k + cb[2]], dtype=float)
                            pos = (pa + (pb - pa) * t_lerp) * spacing + origin
                            v = len(verts)
                            vert_cache[key] = v
                            verts.append(pos.tolist())
                        tri.append(v)
                    faces.append(tri)

    return (np.asarray(verts, dtype=float) if verts else np.zeros((0, 3), dtype=float),
            np.asarray(faces, dtype=np.int32) if faces else np.zeros((0, 3), dtype=np.int32))


_TRI_TABLE_CACHE: list[list[int]] | None = None


def _mc_tri_table() -> list[list[int]]:
    """The 256-case triangle table (public domain, Paul Bourke). Cached on
    first use so the module import stays cheap when only region/falloff is
    used."""
    global _TRI_TABLE_CACHE
    if _TRI_TABLE_CACHE is not None:
        return _TRI_TABLE_CACHE
    _TRI_TABLE_CACHE = _build_tri_table()
    return _TRI_TABLE_CACHE


def _build_tri_table() -> list[list[int]]:
    """Constructs the marching-cubes triangle table from the compact 16-per-case
    representation. Kept as a builder so the source of the code stays audit-able
    (256 rows of raw integers hide errors far better than a table you can read).
    """
    # Data source: Paul Bourke's marching cubes tables (public domain,
    # http://paulbourke.net/geometry/polygonise/). One row per corner mask
    # (0..255); each row lists edges as triples, terminated by -1.
    raw = _MC_TRI_TABLE_RAW
    return [list(row) for row in raw]


# Public-domain marching cubes triangle table. Each row is one of the 256
# corner sign patterns; entries are edge indices (0..11) grouped into
# triangles, terminated by -1. Format matches Paul Bourke's reference.
_MC_TRI_TABLE_RAW = (
    (-1,) * 16,
    (0, 8, 3, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (0, 1, 9, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (1, 8, 3, 9, 8, 1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (1, 2, 10, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (0, 8, 3, 1, 2, 10, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (9, 2, 10, 0, 2, 9, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (2, 8, 3, 2, 10, 8, 10, 9, 8, -1, -1, -1, -1, -1, -1, -1),
    (3, 11, 2, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (0, 11, 2, 8, 11, 0, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (1, 9, 0, 2, 3, 11, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (1, 11, 2, 1, 9, 11, 9, 8, 11, -1, -1, -1, -1, -1, -1, -1),
    (3, 10, 1, 11, 10, 3, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (0, 10, 1, 0, 8, 10, 8, 11, 10, -1, -1, -1, -1, -1, -1, -1),
    (3, 9, 0, 3, 11, 9, 11, 10, 9, -1, -1, -1, -1, -1, -1, -1),
    (9, 8, 10, 10, 8, 11, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (4, 7, 8, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (4, 3, 0, 7, 3, 4, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (0, 1, 9, 8, 4, 7, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (4, 1, 9, 4, 7, 1, 7, 3, 1, -1, -1, -1, -1, -1, -1, -1),
    (1, 2, 10, 8, 4, 7, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (3, 4, 7, 3, 0, 4, 1, 2, 10, -1, -1, -1, -1, -1, -1, -1),
    (9, 2, 10, 9, 0, 2, 8, 4, 7, -1, -1, -1, -1, -1, -1, -1),
    (2, 10, 9, 2, 9, 7, 2, 7, 3, 7, 9, 4, -1, -1, -1, -1),
    (8, 4, 7, 3, 11, 2, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (11, 4, 7, 11, 2, 4, 2, 0, 4, -1, -1, -1, -1, -1, -1, -1),
    (9, 0, 1, 8, 4, 7, 2, 3, 11, -1, -1, -1, -1, -1, -1, -1),
    (4, 7, 11, 9, 4, 11, 9, 11, 2, 9, 2, 1, -1, -1, -1, -1),
    (3, 10, 1, 3, 11, 10, 7, 8, 4, -1, -1, -1, -1, -1, -1, -1),
    (1, 11, 10, 1, 4, 11, 1, 0, 4, 7, 11, 4, -1, -1, -1, -1),
    (4, 7, 8, 9, 0, 11, 9, 11, 10, 11, 0, 3, -1, -1, -1, -1),
    (4, 7, 11, 4, 11, 9, 9, 11, 10, -1, -1, -1, -1, -1, -1, -1),
    (9, 5, 4, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (9, 5, 4, 0, 8, 3, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (0, 5, 4, 1, 5, 0, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (8, 5, 4, 8, 3, 5, 3, 1, 5, -1, -1, -1, -1, -1, -1, -1),
    (1, 2, 10, 9, 5, 4, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (3, 0, 8, 1, 2, 10, 4, 9, 5, -1, -1, -1, -1, -1, -1, -1),
    (5, 2, 10, 5, 4, 2, 4, 0, 2, -1, -1, -1, -1, -1, -1, -1),
    (2, 10, 5, 3, 2, 5, 3, 5, 4, 3, 4, 8, -1, -1, -1, -1),
    (9, 5, 4, 2, 3, 11, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (0, 11, 2, 0, 8, 11, 4, 9, 5, -1, -1, -1, -1, -1, -1, -1),
    (0, 5, 4, 0, 1, 5, 2, 3, 11, -1, -1, -1, -1, -1, -1, -1),
    (2, 1, 5, 2, 5, 8, 2, 8, 11, 4, 8, 5, -1, -1, -1, -1),
    (10, 3, 11, 10, 1, 3, 9, 5, 4, -1, -1, -1, -1, -1, -1, -1),
    (4, 9, 5, 0, 8, 1, 8, 10, 1, 8, 11, 10, -1, -1, -1, -1),
    (5, 4, 0, 5, 0, 11, 5, 11, 10, 11, 0, 3, -1, -1, -1, -1),
    (5, 4, 8, 5, 8, 10, 10, 8, 11, -1, -1, -1, -1, -1, -1, -1),
    (9, 7, 8, 5, 7, 9, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (9, 3, 0, 9, 5, 3, 5, 7, 3, -1, -1, -1, -1, -1, -1, -1),
    (0, 7, 8, 0, 1, 7, 1, 5, 7, -1, -1, -1, -1, -1, -1, -1),
    (1, 5, 3, 3, 5, 7, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (9, 7, 8, 9, 5, 7, 10, 1, 2, -1, -1, -1, -1, -1, -1, -1),
    (10, 1, 2, 9, 5, 0, 5, 3, 0, 5, 7, 3, -1, -1, -1, -1),
    (8, 0, 2, 8, 2, 5, 8, 5, 7, 10, 5, 2, -1, -1, -1, -1),
    (2, 10, 5, 2, 5, 3, 3, 5, 7, -1, -1, -1, -1, -1, -1, -1),
    (7, 9, 5, 7, 8, 9, 3, 11, 2, -1, -1, -1, -1, -1, -1, -1),
    (9, 5, 7, 9, 7, 2, 9, 2, 0, 2, 7, 11, -1, -1, -1, -1),
    (2, 3, 11, 0, 1, 8, 1, 7, 8, 1, 5, 7, -1, -1, -1, -1),
    (11, 2, 1, 11, 1, 7, 7, 1, 5, -1, -1, -1, -1, -1, -1, -1),
    (9, 5, 8, 8, 5, 7, 10, 1, 3, 10, 3, 11, -1, -1, -1, -1),
    (5, 7, 0, 5, 0, 9, 7, 11, 0, 1, 0, 10, 11, 10, 0, -1),
    (11, 10, 0, 11, 0, 3, 10, 5, 0, 8, 0, 7, 5, 7, 0, -1),
    (11, 10, 5, 7, 11, 5, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (10, 6, 5, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (0, 8, 3, 5, 10, 6, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (9, 0, 1, 5, 10, 6, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (1, 8, 3, 1, 9, 8, 5, 10, 6, -1, -1, -1, -1, -1, -1, -1),
    (1, 6, 5, 2, 6, 1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (1, 6, 5, 1, 2, 6, 3, 0, 8, -1, -1, -1, -1, -1, -1, -1),
    (9, 6, 5, 9, 0, 6, 0, 2, 6, -1, -1, -1, -1, -1, -1, -1),
    (5, 9, 8, 5, 8, 2, 5, 2, 6, 3, 2, 8, -1, -1, -1, -1),
    (2, 3, 11, 10, 6, 5, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (11, 0, 8, 11, 2, 0, 10, 6, 5, -1, -1, -1, -1, -1, -1, -1),
    (0, 1, 9, 2, 3, 11, 5, 10, 6, -1, -1, -1, -1, -1, -1, -1),
    (5, 10, 6, 1, 9, 2, 9, 11, 2, 9, 8, 11, -1, -1, -1, -1),
    (6, 3, 11, 6, 5, 3, 5, 1, 3, -1, -1, -1, -1, -1, -1, -1),
    (0, 8, 11, 0, 11, 5, 0, 5, 1, 5, 11, 6, -1, -1, -1, -1),
    (3, 11, 6, 0, 3, 6, 0, 6, 5, 0, 5, 9, -1, -1, -1, -1),
    (6, 5, 9, 6, 9, 11, 11, 9, 8, -1, -1, -1, -1, -1, -1, -1),
    (5, 10, 6, 4, 7, 8, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (4, 3, 0, 4, 7, 3, 6, 5, 10, -1, -1, -1, -1, -1, -1, -1),
    (1, 9, 0, 5, 10, 6, 8, 4, 7, -1, -1, -1, -1, -1, -1, -1),
    (10, 6, 5, 1, 9, 7, 1, 7, 3, 7, 9, 4, -1, -1, -1, -1),
    (6, 1, 2, 6, 5, 1, 4, 7, 8, -1, -1, -1, -1, -1, -1, -1),
    (1, 2, 5, 5, 2, 6, 3, 0, 4, 3, 4, 7, -1, -1, -1, -1),
    (8, 4, 7, 9, 0, 5, 0, 6, 5, 0, 2, 6, -1, -1, -1, -1),
    (7, 3, 9, 7, 9, 4, 3, 2, 9, 5, 9, 6, 2, 6, 9, -1),
    (3, 11, 2, 7, 8, 4, 10, 6, 5, -1, -1, -1, -1, -1, -1, -1),
    (5, 10, 6, 4, 7, 2, 4, 2, 0, 2, 7, 11, -1, -1, -1, -1),
    (0, 1, 9, 4, 7, 8, 2, 3, 11, 5, 10, 6, -1, -1, -1, -1),
    (9, 2, 1, 9, 11, 2, 9, 4, 11, 7, 11, 4, 5, 10, 6, -1),
    (8, 4, 7, 3, 11, 5, 3, 5, 1, 5, 11, 6, -1, -1, -1, -1),
    (5, 1, 11, 5, 11, 6, 1, 0, 11, 7, 11, 4, 0, 4, 11, -1),
    (0, 5, 9, 0, 6, 5, 0, 3, 6, 11, 6, 3, 8, 4, 7, -1),
    (6, 5, 9, 6, 9, 11, 4, 7, 9, 7, 11, 9, -1, -1, -1, -1),
    (10, 4, 9, 6, 4, 10, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (4, 10, 6, 4, 9, 10, 0, 8, 3, -1, -1, -1, -1, -1, -1, -1),
    (10, 0, 1, 10, 6, 0, 6, 4, 0, -1, -1, -1, -1, -1, -1, -1),
    (8, 3, 1, 8, 1, 6, 8, 6, 4, 6, 1, 10, -1, -1, -1, -1),
    (1, 4, 9, 1, 2, 4, 2, 6, 4, -1, -1, -1, -1, -1, -1, -1),
    (3, 0, 8, 1, 2, 9, 2, 4, 9, 2, 6, 4, -1, -1, -1, -1),
    (0, 2, 4, 4, 2, 6, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (8, 3, 2, 8, 2, 4, 4, 2, 6, -1, -1, -1, -1, -1, -1, -1),
    (10, 4, 9, 10, 6, 4, 11, 2, 3, -1, -1, -1, -1, -1, -1, -1),
    (0, 8, 2, 2, 8, 11, 4, 9, 10, 4, 10, 6, -1, -1, -1, -1),
    (3, 11, 2, 0, 1, 6, 0, 6, 4, 6, 1, 10, -1, -1, -1, -1),
    (6, 4, 1, 6, 1, 10, 4, 8, 1, 2, 1, 11, 8, 11, 1, -1),
    (9, 6, 4, 9, 3, 6, 9, 1, 3, 11, 6, 3, -1, -1, -1, -1),
    (8, 11, 1, 8, 1, 0, 11, 6, 1, 9, 1, 4, 6, 4, 1, -1),
    (3, 11, 6, 3, 6, 0, 0, 6, 4, -1, -1, -1, -1, -1, -1, -1),
    (6, 4, 8, 11, 6, 8, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (7, 10, 6, 7, 8, 10, 8, 9, 10, -1, -1, -1, -1, -1, -1, -1),
    (0, 7, 3, 0, 10, 7, 0, 9, 10, 6, 7, 10, -1, -1, -1, -1),
    (10, 6, 7, 1, 10, 7, 1, 7, 8, 1, 8, 0, -1, -1, -1, -1),
    (10, 6, 7, 10, 7, 1, 1, 7, 3, -1, -1, -1, -1, -1, -1, -1),
    (1, 2, 6, 1, 6, 8, 1, 8, 9, 8, 6, 7, -1, -1, -1, -1),
    (2, 6, 9, 2, 9, 1, 6, 7, 9, 0, 9, 3, 7, 3, 9, -1),
    (7, 8, 0, 7, 0, 6, 6, 0, 2, -1, -1, -1, -1, -1, -1, -1),
    (7, 3, 2, 6, 7, 2, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (2, 3, 11, 10, 6, 8, 10, 8, 9, 8, 6, 7, -1, -1, -1, -1),
    (2, 0, 7, 2, 7, 11, 0, 9, 7, 6, 7, 10, 9, 10, 7, -1),
    (1, 8, 0, 1, 7, 8, 1, 10, 7, 6, 7, 10, 2, 3, 11, -1),
    (11, 2, 1, 11, 1, 7, 10, 6, 1, 6, 7, 1, -1, -1, -1, -1),
    (8, 9, 6, 8, 6, 7, 9, 1, 6, 11, 6, 3, 1, 3, 6, -1),
    (0, 9, 1, 11, 6, 7, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (7, 8, 0, 7, 0, 6, 3, 11, 0, 11, 6, 0, -1, -1, -1, -1),
    (7, 11, 6, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (7, 6, 11, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (3, 0, 8, 11, 7, 6, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (0, 1, 9, 11, 7, 6, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (8, 1, 9, 8, 3, 1, 11, 7, 6, -1, -1, -1, -1, -1, -1, -1),
    (10, 1, 2, 6, 11, 7, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (1, 2, 10, 3, 0, 8, 6, 11, 7, -1, -1, -1, -1, -1, -1, -1),
    (2, 9, 0, 2, 10, 9, 6, 11, 7, -1, -1, -1, -1, -1, -1, -1),
    (6, 11, 7, 2, 10, 3, 10, 8, 3, 10, 9, 8, -1, -1, -1, -1),
    (7, 2, 3, 6, 2, 7, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (7, 0, 8, 7, 6, 0, 6, 2, 0, -1, -1, -1, -1, -1, -1, -1),
    (2, 7, 6, 2, 3, 7, 0, 1, 9, -1, -1, -1, -1, -1, -1, -1),
    (1, 6, 2, 1, 8, 6, 1, 9, 8, 8, 7, 6, -1, -1, -1, -1),
    (10, 7, 6, 10, 1, 7, 1, 3, 7, -1, -1, -1, -1, -1, -1, -1),
    (10, 7, 6, 1, 7, 10, 1, 8, 7, 1, 0, 8, -1, -1, -1, -1),
    (0, 3, 7, 0, 7, 10, 0, 10, 9, 6, 10, 7, -1, -1, -1, -1),
    (7, 6, 10, 7, 10, 8, 8, 10, 9, -1, -1, -1, -1, -1, -1, -1),
    (6, 8, 4, 11, 8, 6, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (3, 6, 11, 3, 0, 6, 0, 4, 6, -1, -1, -1, -1, -1, -1, -1),
    (8, 6, 11, 8, 4, 6, 9, 0, 1, -1, -1, -1, -1, -1, -1, -1),
    (9, 4, 6, 9, 6, 3, 9, 3, 1, 11, 3, 6, -1, -1, -1, -1),
    (6, 8, 4, 6, 11, 8, 2, 10, 1, -1, -1, -1, -1, -1, -1, -1),
    (1, 2, 10, 3, 0, 11, 0, 6, 11, 0, 4, 6, -1, -1, -1, -1),
    (4, 11, 8, 4, 6, 11, 0, 2, 9, 2, 10, 9, -1, -1, -1, -1),
    (10, 9, 3, 10, 3, 2, 9, 4, 3, 11, 3, 6, 4, 6, 3, -1),
    (8, 2, 3, 8, 4, 2, 4, 6, 2, -1, -1, -1, -1, -1, -1, -1),
    (0, 4, 2, 4, 6, 2, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (1, 9, 0, 2, 3, 4, 2, 4, 6, 4, 3, 8, -1, -1, -1, -1),
    (1, 9, 4, 1, 4, 2, 2, 4, 6, -1, -1, -1, -1, -1, -1, -1),
    (8, 1, 3, 8, 6, 1, 8, 4, 6, 6, 10, 1, -1, -1, -1, -1),
    (10, 1, 0, 10, 0, 6, 6, 0, 4, -1, -1, -1, -1, -1, -1, -1),
    (4, 6, 3, 4, 3, 8, 6, 10, 3, 0, 3, 9, 10, 9, 3, -1),
    (10, 9, 4, 6, 10, 4, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (4, 9, 5, 7, 6, 11, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (0, 8, 3, 4, 9, 5, 11, 7, 6, -1, -1, -1, -1, -1, -1, -1),
    (5, 0, 1, 5, 4, 0, 7, 6, 11, -1, -1, -1, -1, -1, -1, -1),
    (11, 7, 6, 8, 3, 4, 3, 5, 4, 3, 1, 5, -1, -1, -1, -1),
    (9, 5, 4, 10, 1, 2, 7, 6, 11, -1, -1, -1, -1, -1, -1, -1),
    (6, 11, 7, 1, 2, 10, 0, 8, 3, 4, 9, 5, -1, -1, -1, -1),
    (7, 6, 11, 5, 4, 10, 4, 2, 10, 4, 0, 2, -1, -1, -1, -1),
    (3, 4, 8, 3, 5, 4, 3, 2, 5, 10, 5, 2, 11, 7, 6, -1),
    (7, 2, 3, 7, 6, 2, 5, 4, 9, -1, -1, -1, -1, -1, -1, -1),
    (9, 5, 4, 0, 8, 6, 0, 6, 2, 6, 8, 7, -1, -1, -1, -1),
    (3, 6, 2, 3, 7, 6, 1, 5, 0, 5, 4, 0, -1, -1, -1, -1),
    (6, 2, 8, 6, 8, 7, 2, 1, 8, 4, 8, 5, 1, 5, 8, -1),
    (9, 5, 4, 10, 1, 6, 1, 7, 6, 1, 3, 7, -1, -1, -1, -1),
    (1, 6, 10, 1, 7, 6, 1, 0, 7, 8, 7, 0, 9, 5, 4, -1),
    (4, 0, 10, 4, 10, 5, 0, 3, 10, 6, 10, 7, 3, 7, 10, -1),
    (7, 6, 10, 7, 10, 8, 5, 4, 10, 4, 8, 10, -1, -1, -1, -1),
    (6, 9, 5, 6, 11, 9, 11, 8, 9, -1, -1, -1, -1, -1, -1, -1),
    (3, 6, 11, 0, 6, 3, 0, 5, 6, 0, 9, 5, -1, -1, -1, -1),
    (0, 11, 8, 0, 5, 11, 0, 1, 5, 5, 6, 11, -1, -1, -1, -1),
    (6, 11, 3, 6, 3, 5, 5, 3, 1, -1, -1, -1, -1, -1, -1, -1),
    (1, 2, 10, 9, 5, 11, 9, 11, 8, 11, 5, 6, -1, -1, -1, -1),
    (0, 11, 3, 0, 6, 11, 0, 9, 6, 5, 6, 9, 1, 2, 10, -1),
    (11, 8, 5, 11, 5, 6, 8, 0, 5, 10, 5, 2, 0, 2, 5, -1),
    (6, 11, 3, 6, 3, 5, 2, 10, 3, 10, 5, 3, -1, -1, -1, -1),
    (5, 8, 9, 5, 2, 8, 5, 6, 2, 3, 8, 2, -1, -1, -1, -1),
    (9, 5, 6, 9, 6, 0, 0, 6, 2, -1, -1, -1, -1, -1, -1, -1),
    (1, 5, 8, 1, 8, 0, 5, 6, 8, 3, 8, 2, 6, 2, 8, -1),
    (1, 5, 6, 2, 1, 6, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (1, 3, 6, 1, 6, 10, 3, 8, 6, 5, 6, 9, 8, 9, 6, -1),
    (10, 1, 0, 10, 0, 6, 9, 5, 0, 5, 6, 0, -1, -1, -1, -1),
    (0, 3, 8, 5, 6, 10, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (10, 5, 6, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (11, 5, 10, 7, 5, 11, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (11, 5, 10, 11, 7, 5, 8, 3, 0, -1, -1, -1, -1, -1, -1, -1),
    (5, 11, 7, 5, 10, 11, 1, 9, 0, -1, -1, -1, -1, -1, -1, -1),
    (10, 7, 5, 10, 11, 7, 9, 8, 1, 8, 3, 1, -1, -1, -1, -1),
    (11, 1, 2, 11, 7, 1, 7, 5, 1, -1, -1, -1, -1, -1, -1, -1),
    (0, 8, 3, 1, 2, 7, 1, 7, 5, 7, 2, 11, -1, -1, -1, -1),
    (9, 7, 5, 9, 2, 7, 9, 0, 2, 2, 11, 7, -1, -1, -1, -1),
    (7, 5, 2, 7, 2, 11, 5, 9, 2, 3, 2, 8, 9, 8, 2, -1),
    (2, 5, 10, 2, 3, 5, 3, 7, 5, -1, -1, -1, -1, -1, -1, -1),
    (8, 2, 0, 8, 5, 2, 8, 7, 5, 10, 2, 5, -1, -1, -1, -1),
    (9, 0, 1, 5, 10, 3, 5, 3, 7, 3, 10, 2, -1, -1, -1, -1),
    (9, 8, 2, 9, 2, 1, 8, 7, 2, 10, 2, 5, 7, 5, 2, -1),
    (1, 3, 5, 3, 7, 5, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (0, 8, 7, 0, 7, 1, 1, 7, 5, -1, -1, -1, -1, -1, -1, -1),
    (9, 0, 3, 9, 3, 5, 5, 3, 7, -1, -1, -1, -1, -1, -1, -1),
    (9, 8, 7, 5, 9, 7, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (5, 8, 4, 5, 10, 8, 10, 11, 8, -1, -1, -1, -1, -1, -1, -1),
    (5, 0, 4, 5, 11, 0, 5, 10, 11, 11, 3, 0, -1, -1, -1, -1),
    (0, 1, 9, 8, 4, 10, 8, 10, 11, 10, 4, 5, -1, -1, -1, -1),
    (10, 11, 4, 10, 4, 5, 11, 3, 4, 9, 4, 1, 3, 1, 4, -1),
    (2, 5, 1, 2, 8, 5, 2, 11, 8, 4, 5, 8, -1, -1, -1, -1),
    (0, 4, 11, 0, 11, 3, 4, 5, 11, 2, 11, 1, 5, 1, 11, -1),
    (0, 2, 5, 0, 5, 9, 2, 11, 5, 4, 5, 8, 11, 8, 5, -1),
    (9, 4, 5, 2, 11, 3, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (2, 5, 10, 3, 5, 2, 3, 4, 5, 3, 8, 4, -1, -1, -1, -1),
    (5, 10, 2, 5, 2, 4, 4, 2, 0, -1, -1, -1, -1, -1, -1, -1),
    (3, 10, 2, 3, 5, 10, 3, 8, 5, 4, 5, 8, 0, 1, 9, -1),
    (5, 10, 2, 5, 2, 4, 1, 9, 2, 9, 4, 2, -1, -1, -1, -1),
    (8, 4, 5, 8, 5, 3, 3, 5, 1, -1, -1, -1, -1, -1, -1, -1),
    (0, 4, 5, 1, 0, 5, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (8, 4, 5, 8, 5, 3, 9, 0, 5, 0, 3, 5, -1, -1, -1, -1),
    (9, 4, 5, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (4, 11, 7, 4, 9, 11, 9, 10, 11, -1, -1, -1, -1, -1, -1, -1),
    (0, 8, 3, 4, 9, 7, 9, 11, 7, 9, 10, 11, -1, -1, -1, -1),
    (1, 10, 11, 1, 11, 4, 1, 4, 0, 7, 4, 11, -1, -1, -1, -1),
    (3, 1, 4, 3, 4, 8, 1, 10, 4, 7, 4, 11, 10, 11, 4, -1),
    (4, 11, 7, 9, 11, 4, 9, 2, 11, 9, 1, 2, -1, -1, -1, -1),
    (9, 7, 4, 9, 11, 7, 9, 1, 11, 2, 11, 1, 0, 8, 3, -1),
    (11, 7, 4, 11, 4, 2, 2, 4, 0, -1, -1, -1, -1, -1, -1, -1),
    (11, 7, 4, 11, 4, 2, 8, 3, 4, 3, 2, 4, -1, -1, -1, -1),
    (2, 9, 10, 2, 7, 9, 2, 3, 7, 7, 4, 9, -1, -1, -1, -1),
    (9, 10, 7, 9, 7, 4, 10, 2, 7, 8, 7, 0, 2, 0, 7, -1),
    (3, 7, 10, 3, 10, 2, 7, 4, 10, 1, 10, 0, 4, 0, 10, -1),
    (1, 10, 2, 8, 7, 4, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (4, 9, 1, 4, 1, 7, 7, 1, 3, -1, -1, -1, -1, -1, -1, -1),
    (4, 9, 1, 4, 1, 7, 0, 8, 1, 8, 7, 1, -1, -1, -1, -1),
    (4, 0, 3, 7, 4, 3, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (4, 8, 7, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (9, 10, 8, 10, 11, 8, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (3, 0, 9, 3, 9, 11, 11, 9, 10, -1, -1, -1, -1, -1, -1, -1),
    (0, 1, 10, 0, 10, 8, 8, 10, 11, -1, -1, -1, -1, -1, -1, -1),
    (3, 1, 10, 11, 3, 10, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (1, 2, 11, 1, 11, 9, 9, 11, 8, -1, -1, -1, -1, -1, -1, -1),
    (3, 0, 9, 3, 9, 11, 1, 2, 9, 2, 11, 9, -1, -1, -1, -1),
    (0, 2, 11, 8, 0, 11, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (3, 2, 11, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (2, 3, 8, 2, 8, 10, 10, 8, 9, -1, -1, -1, -1, -1, -1, -1),
    (9, 10, 2, 0, 9, 2, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (2, 3, 8, 2, 8, 10, 0, 1, 8, 1, 10, 8, -1, -1, -1, -1),
    (1, 10, 2, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (1, 3, 8, 9, 1, 8, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (0, 9, 1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (0, 3, 8, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1),
    (-1,) * 16,
)
