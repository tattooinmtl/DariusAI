"""The generic Blender surface: info, scene, objects, materials, modifiers,
render.

These are the primitives an agent composes when the `game3d_*` archetypes
don't fit. Every one runs on Blender's main thread and returns JSON-able
data.
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any

import bpy

from ..server.mcp_server import registry, run_on_main
from ..server.protocol import PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION

_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}

PRIMITIVES = ("cube", "sphere", "cylinder", "cone", "plane", "torus", "empty")


def _object_summary(obj) -> dict[str, Any]:
    return {
        "name": obj.name,
        "type": obj.type,
        "location": list(obj.location),
        "rotation_euler": [math.degrees(a) for a in obj.rotation_euler],
        "scale": list(obj.scale),
        "visible": not obj.hide_render,
        "materials": [m.name for m in obj.data.materials] if getattr(obj, "data", None)
                     and hasattr(obj.data, "materials") else [],
        "polygons": len(obj.data.polygons) if obj.type == "MESH" else 0,
        "modifiers": [m.name for m in obj.modifiers],
    }


# ---- info -------------------------------------------------------------------

def _addon_info_impl() -> dict[str, Any]:
    return {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "blender_version": list(bpy.app.version),
        "blender_version_string": bpy.app.version_string,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "background": bool(bpy.app.background),
        "transport": "streamable-http",
        "tools": registry.names(),
    }


@registry.tool(
    name="get_addon_info",
    description=("Capability handshake: add-on and protocol version, Blender and Python "
                 "versions, and every tool this server exposes. Call this first."),
    input_schema=_NO_ARGS,
)
def get_addon_info() -> dict[str, Any]:
    return run_on_main(_addon_info_impl)


# ---- scene ------------------------------------------------------------------

def _scene_info_impl() -> dict[str, Any]:
    scene = bpy.context.scene
    return {
        "name": scene.name,
        "engine": scene.render.engine,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "frame": {"current": scene.frame_current, "start": scene.frame_start,
                  "end": scene.frame_end},
        "camera": scene.camera.name if scene.camera else None,
        "object_count": len(bpy.data.objects),
        "objects": [o.name for o in bpy.data.objects][:200],
        "materials": [m.name for m in bpy.data.materials][:200],
        "file": bpy.data.filepath or None,
    }


@registry.tool(name="scene_info", description="Scene name, engine, resolution, frame range, "
                                              "camera, and the objects and materials in it.",
               input_schema=_NO_ARGS)
def scene_info() -> dict[str, Any]:
    return run_on_main(_scene_info_impl)


def _frame_set_impl(frame: int) -> dict[str, Any]:
    bpy.context.scene.frame_set(int(frame))
    return {"frame": bpy.context.scene.frame_current}


@registry.tool(
    name="frame_set", description="Jump to a frame.",
    input_schema={"type": "object", "properties": {"frame": {"type": "integer"}},
                  "required": ["frame"], "additionalProperties": False},
)
def frame_set(frame: int) -> dict[str, Any]:
    return run_on_main(_frame_set_impl, frame)


# ---- objects ----------------------------------------------------------------

def _list_objects_impl(type_filter: str | None) -> dict[str, Any]:
    objects = [o for o in bpy.data.objects
               if not type_filter or o.type == str(type_filter).upper()]
    return {"count": len(objects), "objects": [_object_summary(o) for o in objects[:200]]}


@registry.tool(
    name="list_objects", description="Every object in the file, optionally filtered by type.",
    input_schema={"type": "object",
                  "properties": {"type_filter": {"type": "string",
                                                 "description": "MESH, LIGHT, CAMERA, EMPTY…"}},
                  "additionalProperties": False},
)
def list_objects(type_filter: str | None = None) -> dict[str, Any]:
    return run_on_main(_list_objects_impl, type_filter)


def _get_object_impl(name: str) -> dict[str, Any]:
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"no object named {name!r}")
    from ..game3d.geometry import bounds  # noqa: PLC0415

    summary = _object_summary(obj)
    if obj.type == "MESH":
        summary["bounds"] = bounds(obj)
        summary["vertices"] = len(obj.data.vertices)
    return summary


@registry.tool(
    name="get_object", description="Full detail for one object: transform, materials, "
                                   "modifiers, bounds and counts.",
    input_schema={"type": "object", "properties": {"name": {"type": "string"}},
                  "required": ["name"], "additionalProperties": False},
)
def get_object(name: str) -> dict[str, Any]:
    return run_on_main(_get_object_impl, name)


def _create_object_impl(kind: str, name: str | None, location, size: float) -> dict[str, Any]:
    from ..game3d import geometry as geo  # noqa: PLC0415

    kind = (kind or "cube").lower()
    where = tuple(location or (0.0, 0.0, 0.0))
    label = name or f"{kind}"

    if kind == "cube":
        obj = geo.box(label, (size, size, size), where)
    elif kind == "sphere":
        obj = geo.sphere(label, size / 2, where)
    elif kind == "cylinder":
        obj = geo.cylinder(label, size / 2, size, where)
    elif kind == "cone":
        obj = geo.cone(label, size / 2, 0.0, size, where)
    elif kind == "plane":
        obj = geo.box(label, (size, size, 0.001), where)
    elif kind == "torus":
        # No bmesh torus primitive; a ring of segments reads the same.
        parts = []
        segments = 24
        for i in range(segments):
            angle = 2 * math.pi * i / segments
            parts.append(geo.box(f"{label}_seg{i}", (size * 0.18, size * 0.18, size * 0.18),
                                 (where[0] + math.cos(angle) * size / 2,
                                  where[1] + math.sin(angle) * size / 2, where[2])))
        obj = geo.join(parts, label)
    elif kind == "empty":
        obj = bpy.data.objects.new(label, None)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = where
    else:
        raise ValueError(f"unknown primitive {kind!r}; try one of {', '.join(PRIMITIVES)}")
    return _object_summary(obj)


@registry.tool(
    name="create_object", description="Create a primitive: cube, sphere, cylinder, cone, "
                                      "plane, torus or empty.",
    input_schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(PRIMITIVES)},
            "name": {"type": "string"},
            "location": {"type": "array", "items": {"type": "number"},
                         "minItems": 3, "maxItems": 3},
            "size": {"type": "number", "minimum": 0.001, "maximum": 1000},
        },
        "additionalProperties": False,
    },
)
def create_object(kind: str = "cube", name: str | None = None, location: list | None = None,
                  size: float = 2.0) -> dict[str, Any]:
    return run_on_main(_create_object_impl, kind, name, location, float(size))


def _delete_object_impl(name: str) -> dict[str, Any]:
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"no object named {name!r}")
    bpy.data.objects.remove(obj, do_unlink=True)
    return {"deleted": name, "remaining": len(bpy.data.objects)}


@registry.tool(
    name="delete_object", description="Delete an object by name.",
    input_schema={"type": "object", "properties": {"name": {"type": "string"}},
                  "required": ["name"], "additionalProperties": False},
)
def delete_object(name: str) -> dict[str, Any]:
    return run_on_main(_delete_object_impl, name)


def _transform_impl(name: str, location, rotation, scale) -> dict[str, Any]:
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"no object named {name!r}")
    if location is not None:
        obj.location = tuple(location)
    if rotation is not None:
        obj.rotation_euler = tuple(math.radians(a) for a in rotation)
    if scale is not None:
        obj.scale = tuple(scale)
    return _object_summary(obj)


@registry.tool(
    name="transform_object", description="Move, rotate (degrees) or scale an object.",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "location": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
            "rotation": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3,
                         "description": "Euler XYZ in degrees."},
            "scale": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        },
        "required": ["name"], "additionalProperties": False,
    },
)
def transform_object(name: str, location: list | None = None, rotation: list | None = None,
                     scale: list | None = None) -> dict[str, Any]:
    return run_on_main(_transform_impl, name, location, rotation, scale)


# ---- materials --------------------------------------------------------------

def _set_material_impl(object_name: str, colour, material_name: str | None,
                       roughness: float, metallic: float) -> dict[str, Any]:
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise ValueError(f"no object named {object_name!r}")
    if not hasattr(obj.data, "materials"):
        raise ValueError(f"{object_name!r} cannot hold materials")

    name = material_name or f"{object_name}_material"
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True

    channels = list(colour) if colour else [0.8, 0.8, 0.8]
    if len(channels) == 3:
        channels.append(1.0)          # RGB is the common case; alpha is implied
    rgba = tuple(channels[:4])
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = rgba
        bsdf.inputs["Roughness"].default_value = float(roughness)
        bsdf.inputs["Metallic"].default_value = float(metallic)
    material.diffuse_color = rgba

    if obj.data.materials:
        obj.data.materials[0] = material
    else:
        obj.data.materials.append(material)
    return {"object": object_name, "material": material.name, "color": list(rgba)}


@registry.tool(
    name="set_material", description="Give an object a Principled BSDF material with a colour, "
                                     "roughness and metallic value.",
    input_schema={
        "type": "object",
        "properties": {
            "object_name": {"type": "string"},
            "color": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 4,
                      "description": "Linear RGB or RGBA, 0..1."},
            "material_name": {"type": "string"},
            "roughness": {"type": "number", "minimum": 0, "maximum": 1},
            "metallic": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["object_name"], "additionalProperties": False,
    },
)
def set_material(object_name: str, color: list | None = None, material_name: str | None = None,
                 roughness: float = 0.6, metallic: float = 0.0) -> dict[str, Any]:
    return run_on_main(_set_material_impl, object_name, color, material_name, roughness, metallic)


# ---- modifiers --------------------------------------------------------------

def _add_modifier_impl(object_name: str, kind: str, settings) -> dict[str, Any]:
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise ValueError(f"no object named {object_name!r}")
    modifier = obj.modifiers.new(name=kind.title(), type=kind.upper())
    applied = {}
    for key, value in (settings or {}).items():
        if hasattr(modifier, key):
            setattr(modifier, key, value)
            applied[key] = value
    return {"object": object_name, "modifier": modifier.name, "type": modifier.type,
            "settings": applied}


@registry.tool(
    name="add_modifier", description="Add a modifier (SUBSURF, BEVEL, MIRROR, ARRAY, SOLIDIFY…) "
                                     "and set its properties.",
    input_schema={
        "type": "object",
        "properties": {
            "object_name": {"type": "string"},
            "kind": {"type": "string", "description": "Blender modifier type, e.g. SUBSURF."},
            "settings": {"type": "object", "description": "Property name -> value."},
        },
        "required": ["object_name", "kind"], "additionalProperties": False,
    },
)
def add_modifier(object_name: str, kind: str, settings: dict | None = None) -> dict[str, Any]:
    return run_on_main(_add_modifier_impl, object_name, kind, settings)


def _remove_modifier_impl(object_name: str, modifier_name: str) -> dict[str, Any]:
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise ValueError(f"no object named {object_name!r}")
    modifier = obj.modifiers.get(modifier_name)
    if modifier is None:
        raise ValueError(f"{object_name!r} has no modifier {modifier_name!r}")
    obj.modifiers.remove(modifier)
    return {"object": object_name, "removed": modifier_name,
            "remaining": [m.name for m in obj.modifiers]}


@registry.tool(
    name="remove_modifier", description="Remove a modifier from an object.",
    input_schema={"type": "object",
                  "properties": {"object_name": {"type": "string"},
                                 "modifier_name": {"type": "string"}},
                  "required": ["object_name", "modifier_name"], "additionalProperties": False},
)
def remove_modifier(object_name: str, modifier_name: str) -> dict[str, Any]:
    return run_on_main(_remove_modifier_impl, object_name, modifier_name)


# ---- render -----------------------------------------------------------------

def _render_impl(output_path: str, resolution: int | None, samples: int | None) -> dict[str, Any]:
    scene = bpy.context.scene
    if scene.camera is None:
        raise RuntimeError("no camera in the scene")
    if resolution:
        scene.render.resolution_x = scene.render.resolution_y = int(resolution)
    if samples and scene.render.engine == "CYCLES":
        scene.cycles.samples = int(samples)

    path = os.path.abspath(os.path.expanduser(output_path))
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return {"path": scene.render.filepath, "exists": os.path.exists(path),
            "bytes": os.path.getsize(path) if os.path.exists(path) else 0,
            "engine": scene.render.engine}


@registry.tool(
    name="render_image", description="Render the current scene to a PNG at an absolute path.",
    input_schema={
        "type": "object",
        "properties": {
            "output_path": {"type": "string"},
            "resolution": {"type": "integer", "minimum": 64, "maximum": 4096},
            "samples": {"type": "integer", "minimum": 1, "maximum": 4096},
        },
        "required": ["output_path"], "additionalProperties": False,
    },
)
def render_image(output_path: str, resolution: int | None = None,
                 samples: int | None = None) -> dict[str, Any]:
    return run_on_main(_render_impl, output_path, resolution, samples, timeout=900.0)
