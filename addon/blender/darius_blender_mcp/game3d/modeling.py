"""High-level modeling operations: export, reference images, textures,
and the modifier verbs (subdivide / bevel / boolean / extrude / join).

The layer above the `game3d/` primitives and below the `tools/modeling.py`
MCP entry points. Everything here is `bpy`-touching but stays operator-
free where the equivalent `bmesh.ops` call works — background renders
don't reliably have a view layer, and every `bpy.ops.*` that needs one
is a landmine for headless work.
"""

from __future__ import annotations

import os
from typing import Any

import bmesh
import bpy
from mathutils import Vector

EXPORT_FORMATS = ("glb", "gltf", "fbx", "obj", "stl")


def _get_object(name: str, allow_types: tuple[str, ...] = ("MESH",)):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"no object named {name!r}")
    if allow_types and obj.type not in allow_types:
        raise ValueError(f"object {name!r} is {obj.type}, expected one of {allow_types}")
    return obj


# --------------------------------------------------------------------------- #
# Export — the missing 90%
# --------------------------------------------------------------------------- #

def export_model(output_path: str, fmt: str = "glb",
                 objects: list[str] | None = None) -> dict[str, Any]:
    """Export the scene (or a named subset) as a real 3D model file.

    Formats:

    * ``glb`` / ``gltf`` — glTF 2.0. Native import in Unity, Unreal,
      Godot, Three.js, Babylon.js, Blender, Substance. First choice.
    * ``fbx`` — Autodesk FBX. Standard in Unity / Unreal / C4D pipelines
      that predate glTF; larger files, keeps rigs and animation.
    * ``obj`` — Wavefront OBJ. Universal, geometry-only, no rigs.
    * ``stl`` — for 3D printing.

    Format is inferred from `fmt` (or the extension of `output_path` if
    `fmt` is left blank). When `objects` is supplied, only those objects
    are selected for export — otherwise the whole scene minus cameras
    and lights.
    """
    fmt = (fmt or "").lower().strip()
    if not fmt:
        fmt = os.path.splitext(output_path)[1].lstrip(".").lower()
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"unsupported export format {fmt!r}; expected one of {EXPORT_FORMATS}")

    path = os.path.abspath(os.path.expanduser(output_path))
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    # Selection: exporters key off it. Cleared to a known state so a
    # previous stray selection can't leak into the file.
    for o in bpy.context.scene.objects:
        o.select_set(False)

    if objects:
        exported: list = []
        for name in objects:
            obj = bpy.data.objects.get(name)
            if obj is None:
                raise ValueError(f"no object named {name!r} to export")
            obj.select_set(True)
            exported.append(obj)
        use_selection = True
    else:
        exported = [o for o in bpy.context.scene.objects
                    if o.type in ("MESH", "CURVE", "ARMATURE", "EMPTY")]
        for o in exported:
            o.select_set(True)
        use_selection = False

    if not exported:
        raise ValueError("nothing to export — the scene has no mesh/curve/armature objects")

    if exported:
        bpy.context.view_layer.objects.active = exported[0]

    if fmt in ("glb", "gltf"):
        bpy.ops.export_scene.gltf(
            filepath=path,
            export_format="GLB" if fmt == "glb" else "GLTF_SEPARATE",
            use_selection=use_selection,
            export_apply=True,
        )
    elif fmt == "fbx":
        bpy.ops.export_scene.fbx(
            filepath=path,
            use_selection=use_selection,
            apply_scale_options="FBX_SCALE_ALL",
            bake_space_transform=True,
        )
    elif fmt == "obj":
        # Blender 3.2+ replaced the addon with wm.obj_export; try new
        # then fall back so older builds still work.
        try:
            bpy.ops.wm.obj_export(filepath=path, export_selected_objects=use_selection)
        except AttributeError:
            bpy.ops.export_scene.obj(filepath=path, use_selection=use_selection)
    elif fmt == "stl":
        try:
            bpy.ops.wm.stl_export(filepath=path, export_selected_objects=use_selection)
        except AttributeError:
            bpy.ops.export_mesh.stl(filepath=path, use_selection=use_selection)

    size = os.path.getsize(path) if os.path.exists(path) else 0
    return {
        "path": path,
        "format": fmt,
        "bytes": size,
        "objects_exported": [o.name for o in exported],
    }


# --------------------------------------------------------------------------- #
# Reference image — model against a photo
# --------------------------------------------------------------------------- #

REFERENCE_AXES = ("front", "back", "left", "right", "top", "bottom")


def load_reference(image_path: str, axis: str = "front",
                   size: float = 2.0, opacity: float = 0.5,
                   name: str | None = None) -> dict[str, Any]:
    """Load an image as a background reference plane on one axis.

    Positions and orients a plane so the image faces the given axis at
    the world origin. This is how modelers work from concept art: place
    a front and a side view, then build against them. The plane is
    marked as a reference (non-selectable in most exports) and its
    material is set to unlit so lighting doesn't wash the reference out.

    `size` is the plane's dimension in metres; `opacity` [0..1] blends
    the image against a transparent background.
    """
    axis = axis.lower()
    if axis not in REFERENCE_AXES:
        raise ValueError(f"axis must be one of {REFERENCE_AXES}; got {axis!r}")
    path = os.path.abspath(os.path.expanduser(image_path))
    if not os.path.exists(path):
        raise ValueError(f"reference image not found: {path}")

    image = bpy.data.images.load(path, check_existing=True)

    # Build the plane in bmesh so we don't need a view layer.
    bm = bmesh.new()
    half = float(size) / 2.0
    v1 = bm.verts.new((-half, 0, -half))
    v2 = bm.verts.new((half, 0, -half))
    v3 = bm.verts.new((half, 0, half))
    v4 = bm.verts.new((-half, 0, half))
    face = bm.faces.new((v1, v2, v3, v4))
    bm.faces.ensure_lookup_table()

    # UV so the image maps flat onto the quad.
    uv_layer = bm.loops.layers.uv.new("UVMap")
    for loop, uv in zip(face.loops, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        loop[uv_layer].uv = uv

    mesh = bpy.data.meshes.new(name or f"ref_{axis}")
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new(name or f"ref_{axis}", mesh)
    bpy.context.scene.collection.objects.link(obj)

    # Orient toward the requested axis.
    from math import radians
    rotations = {
        "front":  (radians(90), 0, 0),
        "back":   (radians(90), 0, radians(180)),
        "left":   (radians(90), 0, radians(-90)),
        "right":  (radians(90), 0, radians(90)),
        "top":    (0, 0, 0),
        "bottom": (radians(180), 0, 0),
    }
    obj.rotation_euler = rotations[axis]

    # Unlit material with the image texture. Nodes because the default
    # Principled BSDF would treat the image as base colour under lighting.
    material = bpy.data.materials.new(name=f"{obj.name}_mat")
    material.use_nodes = True
    material.blend_method = "BLEND"
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image
    emit = nodes.new("ShaderNodeEmission")
    mix = nodes.new("ShaderNodeMixShader")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    output = nodes.new("ShaderNodeOutputMaterial")

    links.new(tex.outputs["Color"], emit.inputs["Color"])
    links.new(transparent.outputs["BSDF"], mix.inputs[1])
    links.new(emit.outputs["Emission"], mix.inputs[2])
    mix.inputs["Fac"].default_value = float(max(0.0, min(1.0, opacity)))
    links.new(mix.outputs["Shader"], output.inputs["Surface"])

    obj.data.materials.append(material)
    obj["is_reference"] = True   # so an exporter or a downstream tool can skip it

    return {
        "name": obj.name,
        "axis": axis,
        "size": float(size),
        "opacity": float(opacity),
        "image": path,
    }


# --------------------------------------------------------------------------- #
# Apply texture — the surface-detail lever
# --------------------------------------------------------------------------- #

def apply_texture(object_name: str, image_path: str, unwrap: bool = True) -> dict[str, Any]:
    """Apply an image as a base-colour texture on a mesh.

    Creates (or replaces) the object's first material with a
    Principled-BSDF material whose base colour is the image. When
    `unwrap` is true and the mesh has no UVs, a smart-project unwrap is
    generated so the texture actually appears rather than sampling the
    same pixel across every face.
    """
    obj = _get_object(object_name)
    path = os.path.abspath(os.path.expanduser(image_path))
    if not os.path.exists(path):
        raise ValueError(f"texture image not found: {path}")

    image = bpy.data.images.load(path, check_existing=True)

    material = bpy.data.materials.new(name=f"{obj.name}_tex")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    principled = nodes.get("Principled BSDF")
    if principled is None:
        principled = nodes.new("ShaderNodeBsdfPrincipled")
        output = nodes.new("ShaderNodeOutputMaterial")
        links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image
    links.new(tex.outputs["Color"], principled.inputs["Base Color"])

    if obj.data.materials:
        obj.data.materials[0] = material
    else:
        obj.data.materials.append(material)

    generated_uvs = False
    if unwrap and not obj.data.uv_layers:
        # bmesh-based smart-project analogue — one big planar unwrap.
        # For high-quality UVs the caller should use Blender's own
        # `smart_project` interactively; this is enough for a texture
        # to show up rather than being all one colour.
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        uv_layer = bm.loops.layers.uv.verify()
        for face in bm.faces:
            for loop in face.loops:
                loop[uv_layer].uv = (loop.vert.co.x + 0.5, loop.vert.co.y + 0.5)
        bm.to_mesh(obj.data)
        bm.free()
        generated_uvs = True

    return {
        "name": obj.name,
        "material": material.name,
        "image": path,
        "generated_uvs": generated_uvs,
    }


# --------------------------------------------------------------------------- #
# Modifier verbs — the high-poly / hard-surface levers
# --------------------------------------------------------------------------- #

def subdivide(object_name: str, levels: int = 2, smooth: bool = True) -> dict[str, Any]:
    """Add and apply a Subdivision Surface modifier. `smooth=True` uses
    Catmull-Clark (organic subdivision); `smooth=False` uses simple
    subdivision (each face becomes 4, no smoothing — good for adding
    resolution without changing shape)."""
    obj = _get_object(object_name)
    levels = max(0, min(6, int(levels)))
    mod = obj.modifiers.new(name="Subsurf", type="SUBSURF")
    mod.subdivision_type = "CATMULL_CLARK" if smooth else "SIMPLE"
    mod.levels = levels
    mod.render_levels = levels
    _apply_modifier(obj, mod.name)
    return {
        "name": obj.name,
        "levels": levels,
        "smooth": bool(smooth),
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
    }


def bevel(object_name: str, width: float = 0.02, segments: int = 2,
          profile: float = 0.5) -> dict[str, Any]:
    """Bevel every edge of the mesh — the hard-surface detail lever.

    Small `width` (2–5% of the object's smallest dimension) is the
    default for game assets; a bare edge looks cheap under normal
    lighting because it catches a single-pixel highlight. `segments`
    trades polycount for roundness (2 is enough for most cases;
    4+ gives a genuine round-over). `profile` in [0, 1] shapes the
    curve (0.5 is a circular quarter-turn, 1.0 is squared-off).
    """
    obj = _get_object(object_name)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.bevel(
        bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces),
        offset=float(width), segments=max(1, int(segments)),
        profile=float(profile), affect="EDGES",
    )
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return {
        "name": obj.name,
        "width": float(width),
        "segments": int(segments),
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
    }


BOOLEAN_OPS = ("UNION", "DIFFERENCE", "INTERSECT")


def boolean(a_name: str, b_name: str, operation: str = "UNION",
            keep_b: bool = False) -> dict[str, Any]:
    """CSG boolean between two meshes. `operation` is UNION / DIFFERENCE
    / INTERSECT. Result replaces object A; B is removed unless
    `keep_b=True` (useful when B is a cutter you want to re-use)."""
    op = operation.upper()
    if op not in BOOLEAN_OPS:
        raise ValueError(f"operation must be one of {BOOLEAN_OPS}; got {operation!r}")
    a = _get_object(a_name)
    b = _get_object(b_name)

    mod = a.modifiers.new(name="Boolean", type="BOOLEAN")
    mod.object = b
    mod.operation = op
    mod.solver = "EXACT"
    _apply_modifier(a, mod.name)

    if not keep_b:
        data = b.data
        bpy.data.objects.remove(b, do_unlink=True)
        if hasattr(data, "users") and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)

    return {
        "name": a.name,
        "operation": op,
        "removed": b_name if not keep_b else None,
        "vertices": len(a.data.vertices),
        "polygons": len(a.data.polygons),
    }


def extrude(object_name: str, direction: tuple[float, float, float] = (0, 0, 1),
            distance: float = 0.5,
            select: dict | None = None) -> dict[str, Any]:
    """Extrude a subset of faces along a direction.

    `select` is a small predicate; without it the whole mesh is
    extruded. Supported keys:

    * ``normal_axis``: ``"z+"`` | ``"z-"`` | ``"x+"`` etc — pick faces
      whose normal points primarily in that direction (dot product > 0.7).
    * ``above``: ``{"axis": "z", "value": 0.5}`` — faces whose centre
      is above the given axis value.
    """
    obj = _get_object(object_name)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()

    faces = list(bm.faces)
    if select:
        faces = _filter_faces(faces, select, obj.matrix_world)

    if not faces:
        bm.free()
        raise ValueError("no faces matched the selection; nothing to extrude")

    ext = bmesh.ops.extrude_face_region(bm, geom=faces)
    verts = [v for v in ext["geom"] if isinstance(v, bmesh.types.BMVert)]
    dx, dy, dz = direction
    vec = Vector((float(dx) * float(distance),
                  float(dy) * float(distance),
                  float(dz) * float(distance)))
    bmesh.ops.translate(bm, verts=verts, vec=vec)

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    return {
        "name": obj.name,
        "extruded_faces": len(faces),
        "distance": float(distance),
        "direction": [float(dx), float(dy), float(dz)],
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
    }


def _filter_faces(faces, select: dict, matrix_world) -> list:
    if "normal_axis" in select:
        axis_str = str(select["normal_axis"]).lower()
        axis_map = {"x": 0, "y": 1, "z": 2}
        axis = axis_map[axis_str[0]]
        sign = -1.0 if axis_str.endswith("-") else 1.0
        return [f for f in faces if (f.normal[axis] * sign) > 0.7]
    if "above" in select:
        cfg = select["above"]
        axis_map = {"x": 0, "y": 1, "z": 2}
        axis = axis_map[cfg["axis"].lower()]
        value = float(cfg["value"])
        return [f for f in faces
                if (matrix_world @ f.calc_center_median())[axis] > value]
    raise ValueError(f"unknown face selector: {list(select.keys())}")


def join_objects(names: list[str], result_name: str = "joined") -> dict[str, Any]:
    """Merge several meshes into one. Uses bmesh so no view layer /
    active object is required."""
    from .geometry import join as _join
    objects = [_get_object(n) for n in names]
    merged = _join(objects, result_name)
    if merged is None:
        raise ValueError("nothing to join")
    return {
        "name": merged.name,
        "sources": list(names),
        "vertices": len(merged.data.vertices),
        "polygons": len(merged.data.polygons),
    }


# --------------------------------------------------------------------------- #
# Modifier baking — shared with sculpt.py
# --------------------------------------------------------------------------- #

def _apply_modifier(obj, modifier_name: str) -> None:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    new_mesh = bpy.data.meshes.new_from_object(evaluated)
    old_mesh = obj.data
    obj.modifiers.remove(obj.modifiers[modifier_name])
    obj.data = new_mesh
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
