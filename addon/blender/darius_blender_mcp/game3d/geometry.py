"""Primitive builders for game assets.

`bmesh.ops` rather than `bpy.ops`: operators depend on context (an active
object, a 3D viewport, a selection) that does not exist in background mode,
which is exactly where asset generation runs. bmesh works identically with
or without a UI.
"""

from __future__ import annotations

import bmesh
import bpy
from mathutils import Matrix, Vector


def _finish(bm, name: str, material=None, collection=None):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    if material is not None:
        obj.data.materials.append(material)
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def _placement(origin, rotation=None, scale=None) -> Matrix:
    matrix = Matrix.Translation(Vector(origin))
    if rotation:
        rx, ry, rz = rotation
        matrix = matrix @ (Matrix.Rotation(rx, 4, "X")
                           @ Matrix.Rotation(ry, 4, "Y")
                           @ Matrix.Rotation(rz, 4, "Z"))
    if scale:
        matrix = matrix @ Matrix.Diagonal((*scale, 1.0))
    return matrix


def box(name: str, size=(1.0, 1.0, 1.0), origin=(0.0, 0.0, 0.0), material=None,
        bevel: float = 0.0, rotation=None, collection=None):
    """Axis-aligned box. `origin` is its centre."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0, matrix=_placement(origin, rotation, size))
    if bevel > 0:
        bmesh.ops.bevel(
            bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces),
            offset=bevel, segments=2, profile=0.5, affect="EDGES",
        )
    return _finish(bm, name, material, collection)


def cylinder(name: str, radius: float = 1.0, depth: float = 1.0, origin=(0.0, 0.0, 0.0),
             segments: int = 24, material=None, rotation=None, collection=None):
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=segments,
        radius1=radius, radius2=radius, depth=depth,
        matrix=_placement(origin, rotation),
    )
    return _finish(bm, name, material, collection)


def cone(name: str, radius_bottom: float = 1.0, radius_top: float = 0.0, depth: float = 1.0,
         origin=(0.0, 0.0, 0.0), segments: int = 24, material=None, rotation=None,
         collection=None):
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=segments,
        radius1=radius_bottom, radius2=radius_top, depth=depth,
        matrix=_placement(origin, rotation),
    )
    return _finish(bm, name, material, collection)


def sphere(name: str, radius: float = 1.0, origin=(0.0, 0.0, 0.0), segments: int = 24,
           rings: int = 12, material=None, scale=None, collection=None):
    bm = bmesh.new()
    # `radius` replaced `diameter` in Blender 3.0; accept either so the
    # add-on survives on an older build rather than failing at import.
    try:
        bmesh.ops.create_uvsphere(
            bm, u_segments=segments, v_segments=rings, radius=radius,
            matrix=_placement(origin, None, scale),
        )
    except TypeError:
        bmesh.ops.create_uvsphere(
            bm, u_segments=segments, v_segments=rings, diameter=radius,
            matrix=_placement(origin, None, scale),
        )
    return _finish(bm, name, material, collection)


def pyramid(name: str, base=(1.0, 1.0), height: float = 1.0, origin=(0.0, 0.0, 0.0),
            material=None, collection=None):
    """Four-sided pyramid — the workhorse roof. `origin` is the centre of
    its base, so it sits directly on top of a wall of the same footprint."""
    hx, hy = base[0] / 2.0, base[1] / 2.0
    ox, oy, oz = origin
    verts = [
        (ox - hx, oy - hy, oz), (ox + hx, oy - hy, oz),
        (ox + hx, oy + hy, oz), (ox - hx, oy + hy, oz),
        (ox, oy, oz + height),
    ]
    faces = [(0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4), (3, 2, 1, 0)]
    return mesh_from_pydata(name, verts, faces, material, collection)


def hip_roof(name: str, base=(2.0, 1.0), height: float = 0.8, ridge: float = 0.5,
             origin=(0.0, 0.0, 0.0), material=None, collection=None):
    """A pyramid flattened into a ridge — the ordinary house roof.
    `ridge` is the ridge length as a fraction of the long side."""
    hx, hy = base[0] / 2.0, base[1] / 2.0
    rx = hx * max(0.0, min(0.95, ridge))
    ox, oy, oz = origin
    verts = [
        (ox - hx, oy - hy, oz), (ox + hx, oy - hy, oz),
        (ox + hx, oy + hy, oz), (ox - hx, oy + hy, oz),
        (ox - rx, oy, oz + height), (ox + rx, oy, oz + height),
    ]
    faces = [(0, 1, 5, 4), (2, 3, 4, 5), (1, 2, 5), (3, 0, 4), (3, 2, 1, 0)]
    return mesh_from_pydata(name, verts, faces, material, collection)


def mesh_from_pydata(name: str, vertices, faces, material=None, collection=None):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in vertices], [], [tuple(f) for f in faces])
    mesh.validate()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    if material is not None:
        obj.data.materials.append(material)
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def join(objects, name: str):
    """Merge parts into one object. Done by hand rather than via
    `bpy.ops.object.join`, which needs an active object and a view layer
    that background renders do not reliably have."""
    objects = [o for o in objects if o is not None]
    if not objects:
        return None
    if len(objects) == 1:
        objects[0].name = name
        return objects[0]

    bm = bmesh.new()
    materials: list = []
    for obj in objects:
        mesh = obj.data
        slot_map = {}
        for index, material in enumerate(mesh.materials):
            if material not in materials:
                materials.append(material)
            slot_map[index] = materials.index(material)

        before = len(bm.faces)
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        # Re-point material indices at the merged slot list — without this
        # every part after the first would draw with part one's materials.
        for face in list(bm.faces)[before:]:
            face.material_index = slot_map.get(face.material_index, 0)

    merged = bpy.data.meshes.new(name)
    bm.to_mesh(merged)
    bm.free()
    merged.update()
    for material in materials:
        merged.materials.append(material)

    result = bpy.data.objects.new(name, merged)
    bpy.context.scene.collection.objects.link(result)

    for obj in objects:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data.users == 0:
            bpy.data.meshes.remove(data)
    return result


def bounds(obj) -> dict:
    """World-space bounding box — how a caller checks what it just got."""
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]
    return {
        "min": [min(xs), min(ys), min(zs)],
        "max": [max(xs), max(ys), max(zs)],
        "size": [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)],
    }
