"""The bpy-free half of the sculpting toolkit: region predicates,
falloff curves, SDF primitives, the whitelisted expression evaluator,
and the marching-cubes fallback.

These tests exercise the actual maths — if `sphere` returns the wrong
distances or `smooth_union` blends the wrong direction, the sculpting
tool will produce visible garbage inside Blender and only there. Better
to catch it here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon" / "blender" / "darius_blender_mcp"


def _load(name: str, relative: str):
    """Load a submodule directly off disk, bypassing the addon package
    __init__ (which imports bpy). Same trick test_blender_addon.py uses."""
    spec = importlib.util.spec_from_file_location(name, ADDON / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


implicit = _load("_darius_implicit", "game3d/implicit.py")


# --------------------------------------------------------------------------- #
# Falloff curves
# --------------------------------------------------------------------------- #

def test_smooth_falloff_endpoints():
    assert implicit.falloff_smooth(0.0) == 0.0
    assert implicit.falloff_smooth(1.0) == 1.0
    # Smoothstep has zero slope at both ends, so the midpoint sits at exactly 0.5.
    assert abs(implicit.falloff_smooth(0.5) - 0.5) < 1e-9


def test_smooth_falloff_is_monotone():
    xs = np.linspace(0, 1, 20)
    ys = [implicit.falloff_smooth(x) for x in xs]
    assert all(b >= a for a, b in zip(ys, ys[1:]))


def test_falloffs_clamp_out_of_range():
    for fn in (implicit.falloff_smooth, implicit.falloff_linear,
               implicit.falloff_sharp):
        assert fn(-0.5) == 0.0
        assert fn(1.5) == 1.0


# --------------------------------------------------------------------------- #
# Region: sphere
# --------------------------------------------------------------------------- #

def test_sphere_region_hits_only_inside():
    fn, centre = implicit.compile_region({"sphere": [0.0, 0.0, 0.0, 1.0]})
    pts = np.array([
        [0.0, 0.0, 0.0],   # centre → 1.0
        [0.5, 0.0, 0.0],   # inside → weight ~smoothstep(0.5) = 0.5
        [1.0, 0.0, 0.0],   # edge   → 0.0
        [2.0, 0.0, 0.0],   # outside → 0.0
    ])
    weights = fn(pts)
    assert weights[0] == 1.0
    assert 0.0 < weights[1] < 1.0
    assert weights[2] == 0.0
    assert weights[3] == 0.0
    assert list(centre) == [0.0, 0.0, 0.0]


def test_sphere_region_rejects_zero_radius():
    with pytest.raises(ValueError):
        implicit.compile_region({"sphere": [0, 0, 0, 0]})


def test_sphere_region_respects_falloff_choice():
    smooth_fn, _ = implicit.compile_region({"sphere": [0, 0, 0, 1.0], "falloff": "smooth"})
    constant_fn, _ = implicit.compile_region({"sphere": [0, 0, 0, 1.0], "falloff": "constant"})
    pt = np.array([[0.5, 0, 0]])
    # Smooth gives a partial weight at half-distance; constant gives full 1.
    assert smooth_fn(pt)[0] < 1.0
    assert constant_fn(pt)[0] == 1.0


# --------------------------------------------------------------------------- #
# Region: box
# --------------------------------------------------------------------------- #

def test_box_region_covers_interior_and_rejects_outside():
    fn, centre = implicit.compile_region({"box": [-1, -1, -1, 1, 1, 1]})
    pts = np.array([
        [0.0, 0.0, 0.0],   # interior → 1
        [0.9, 0.9, 0.9],   # interior → 1
        [1.5, 0.0, 0.0],   # outside  → 0
        [-2.0, 0.0, 0.0],  # outside  → 0
    ])
    weights = fn(pts)
    assert weights[0] == 1.0
    assert weights[1] == 1.0
    assert weights[2] == 0.0
    assert weights[3] == 0.0
    assert list(centre) == [0.0, 0.0, 0.0]


def test_box_region_with_feather_softens_edge():
    fn, _ = implicit.compile_region(
        {"box": [-1, -1, -1, 1, 1, 1], "feather": 0.5},
    )
    inside = fn(np.array([[0, 0, 0]]))
    just_outside = fn(np.array([[1.25, 0, 0]]))
    far_outside = fn(np.array([[3.0, 0, 0]]))
    assert inside[0] == 1.0
    assert 0.0 < just_outside[0] < 1.0
    assert far_outside[0] == 0.0


# --------------------------------------------------------------------------- #
# Region: axis_above
# --------------------------------------------------------------------------- #

def test_axis_above_half_space():
    fn, _ = implicit.compile_region({"axis_above": {"axis": "z", "value": 0.0}})
    weights = fn(np.array([[0, 0, 1], [0, 0, 0], [0, 0, -1]]))
    assert weights[0] == 1.0
    assert weights[1] == 1.0     # exactly on plane counts as above
    assert weights[2] == 0.0


def test_axis_above_with_feather_ramps():
    fn, _ = implicit.compile_region(
        {"axis_above": {"axis": "z", "value": 0.0, "feather": 1.0}},
    )
    weights = fn(np.array([[0, 0, -1], [0, 0, 0.5], [0, 0, 2]]))
    assert weights[0] == 0.0
    assert 0.0 < weights[1] < 1.0
    assert weights[2] == 1.0


# --------------------------------------------------------------------------- #
# Region: all
# --------------------------------------------------------------------------- #

def test_all_region_weights_every_point():
    fn, _ = implicit.compile_region({"all": True})
    weights = fn(np.random.rand(50, 3) * 10 - 5)
    assert (weights == 1.0).all()


def test_unknown_region_raises():
    with pytest.raises(ValueError):
        implicit.compile_region({"potato": [1, 2, 3]})


# --------------------------------------------------------------------------- #
# SDF primitives
# --------------------------------------------------------------------------- #

def test_sdf_sphere_signed_distances():
    fn = implicit.sdf_sphere((0, 0, 0), 1.0)
    pts = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]])
    d = fn(pts)
    assert d[0] == -1.0     # centre → -radius
    assert d[1] == 0.0      # on the surface
    assert d[2] == 1.0      # 1 unit outside


def test_sdf_box_negative_inside_positive_outside():
    fn = implicit.sdf_box((0, 0, 0), (2, 2, 2))
    assert fn(np.array([[0, 0, 0]]))[0] < 0
    assert fn(np.array([[1, 0, 0]]))[0] == 0
    assert fn(np.array([[2, 0, 0]]))[0] == 1.0


def test_sdf_capsule_flat_at_axis():
    fn = implicit.sdf_capsule((-1, 0, 0), (1, 0, 0), 0.5)
    # Anywhere on the segment axis is inside by exactly the radius.
    for x in [-1, -0.5, 0, 0.5, 1]:
        assert fn(np.array([[x, 0, 0]]))[0] == -0.5


def test_sdf_union_takes_the_min():
    a = implicit.sdf_sphere((-1, 0, 0), 1.0)
    b = implicit.sdf_sphere((1, 0, 0), 1.0)
    both = implicit.sdf_union(a, b)
    # Between the two centres, both are equidistant; union picks one.
    assert both(np.array([[0, 0, 0]]))[0] == 0.0
    # Anywhere inside one is inside the union.
    assert both(np.array([[-1, 0, 0]]))[0] == -1.0


def test_sdf_subtract_carves_a_hole():
    solid = implicit.sdf_sphere((0, 0, 0), 1.0)
    hole = implicit.sdf_sphere((0, 0, 0), 0.5)
    carved = implicit.sdf_subtract(solid, hole)
    # Point that was inside the solid but also inside the hole is now outside.
    assert carved(np.array([[0.25, 0, 0]]))[0] > 0
    # Point in the outer shell (past the hole) is still inside.
    assert carved(np.array([[0.75, 0, 0]]))[0] < 0


def test_sdf_smooth_union_blends_hard_boundaries():
    a = implicit.sdf_sphere((-0.5, 0, 0), 0.5)
    b = implicit.sdf_sphere((0.5, 0, 0), 0.5)
    hard = implicit.sdf_union(a, b)
    smooth = implicit.sdf_smooth_union(a, b, k=0.3)
    # Between the two spheres, smooth_union should sit lower (more inside)
    # than a hard union — that's the "melty" join it exists for.
    p = np.array([[0, 0, 0]])
    assert smooth(p)[0] < hard(p)[0]


def test_sdf_translate_shifts_the_field():
    base = implicit.sdf_sphere((0, 0, 0), 1.0)
    moved = implicit.sdf_translate(base, (2, 0, 0))
    # The moved sphere now has its surface at x=1 and x=3.
    assert moved(np.array([[2, 0, 0]]))[0] == -1.0
    assert moved(np.array([[3, 0, 0]]))[0] == 0.0


def test_sdf_scale_scales_distances_too():
    base = implicit.sdf_sphere((0, 0, 0), 1.0)
    big = implicit.sdf_scale(base, 3.0)
    # Sphere of radius 1 scaled by 3 becomes radius 3.
    assert big(np.array([[3, 0, 0]]))[0] == 0.0
    assert big(np.array([[0, 0, 0]]))[0] == -3.0


# --------------------------------------------------------------------------- #
# SDF expression evaluator — the LLM-facing gate
# --------------------------------------------------------------------------- #

def test_simple_expression_evaluates():
    fn = implicit.evaluate_sdf_expression("sphere((0,0,0), 1.0)")
    assert fn(np.array([[0, 0, 0]]))[0] == -1.0


def test_nested_expression_evaluates():
    fn = implicit.evaluate_sdf_expression(
        "smooth_union(sphere((-0.5,0,0), 0.5), sphere((0.5,0,0), 0.5), 0.2)",
    )
    # Should be inside somewhere between the two spheres.
    assert fn(np.array([[0, 0, 0]]))[0] < 0


def test_expression_rejects_attribute_access():
    with pytest.raises(ValueError):
        implicit.evaluate_sdf_expression("sphere.__class__.__mro__[1]")


def test_expression_rejects_arbitrary_calls():
    with pytest.raises(ValueError):
        implicit.evaluate_sdf_expression("open('/etc/passwd').read()")


def test_expression_rejects_unknown_names():
    with pytest.raises(ValueError):
        implicit.evaluate_sdf_expression("mystery((0,0,0), 1.0)")


def test_expression_rejects_imports():
    # `import` is a statement, not an expression — parses via mode='exec' only.
    # But we still want to make sure an equivalent form fails cleanly.
    with pytest.raises((ValueError, SyntaxError)):
        implicit.evaluate_sdf_expression("__import__('os').system('rm -rf /')")


def test_expression_rejects_lambda():
    with pytest.raises(ValueError):
        implicit.evaluate_sdf_expression("(lambda: sphere((0,0,0), 1))()")


# --------------------------------------------------------------------------- #
# Marching cubes — the mesher
# --------------------------------------------------------------------------- #

def test_sample_sdf_grid_shape_matches_resolution():
    fn = implicit.sdf_sphere((0, 0, 0), 0.5)
    vol, origin = implicit.sample_sdf(fn, (-1, -1, -1, 1, 1, 1), resolution=16)
    assert vol.shape == (16, 16, 16)
    assert list(origin) == [-1, -1, -1]


def test_marching_cubes_produces_a_closed_sphere():
    """The mesher on a sphere SDF must produce a real surface — non-zero
    verts, non-zero faces, roughly centred on the sphere's centre. Exact
    triangle count depends on whether scikit-image is installed, so we
    assert only shape properties."""
    fn = implicit.sdf_sphere((0, 0, 0), 0.5)
    vol, origin = implicit.sample_sdf(fn, (-1, -1, -1, 1, 1, 1), resolution=24)
    spacing = 2.0 / 23
    verts, faces = implicit.marching_cubes(vol, origin, spacing)
    assert len(verts) > 100
    assert len(faces) > 100
    # Sphere is centred on origin, so vertex centroid should be near it.
    centroid = verts.mean(axis=0)
    assert np.allclose(centroid, [0, 0, 0], atol=0.1)


def test_marching_cubes_empty_volume_returns_empty_mesh():
    """A volume that never crosses the iso value emits no geometry —
    the mesher must not crash on that or invent phantom triangles."""
    vol = np.ones((10, 10, 10)) * 5.0    # everywhere positive
    origin = np.zeros(3)
    verts, faces = implicit.marching_cubes(vol, origin, 1.0)
    assert len(verts) == 0
    assert len(faces) == 0
