"""`game3d_*` modeling tools — export, reference, texture, and the
modifier verbs (subdivide / bevel / boolean / extrude / join).

The tool surface an agent uses to turn Blender output into engine-ready
assets. Every function here is a thin `run_on_main` wrapper around
`game3d.modeling`; the mesh work happens there.
"""

from __future__ import annotations

from typing import Any

from ..game3d import modeling
from ..server.mcp_server import registry, run_on_main


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

@registry.tool(
    name="game3d_export_model",
    description=(
        "Export the scene, or a named subset of objects, as a real 3D model file "
        "for a game engine or DCC. Formats: glb (glTF binary — Unity, Unreal, "
        "Godot, Three.js, Babylon all import natively), gltf (glTF separate), "
        "fbx (Autodesk FBX — legacy Unity/Unreal/C4D pipelines), obj (universal, "
        "geometry only), stl (3D printing). glb is the safe default."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "output_path": {"type": "string",
                            "description": "Absolute path for the exported file."},
            "format": {"type": "string", "enum": list(modeling.EXPORT_FORMATS),
                       "description": "Explicit format; inferred from the extension if omitted."},
            "objects": {"type": "array", "items": {"type": "string"},
                        "description": "Names to export; omit to export the whole scene."},
        },
        "required": ["output_path"],
        "additionalProperties": False,
    },
)
def game3d_export_model(output_path: str, format: str = "",
                        objects: list[str] | None = None) -> dict[str, Any]:
    return run_on_main(modeling.export_model, output_path, format, objects, timeout=120.0)


# --------------------------------------------------------------------------- #
# Reference image
# --------------------------------------------------------------------------- #

@registry.tool(
    name="game3d_load_reference",
    description=(
        "Load an image as a background reference plane on one axis (front, back, "
        "left, right, top, bottom). The way a modeler works from concept art: "
        "place a front and a side view, then build against them. The plane uses "
        "an unlit shader so lighting doesn't wash the reference out, and is "
        "tagged is_reference so subsequent tools can skip it in exports."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "image_path": {"type": "string", "description": "Absolute path to the image."},
            "axis": {"type": "string", "enum": list(modeling.REFERENCE_AXES),
                     "description": "Which world axis the image faces (default front)."},
            "size": {"type": "number", "minimum": 0.1, "maximum": 20.0,
                     "description": "Plane size in metres (default 2.0)."},
            "opacity": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                        "description": "Reference opacity (default 0.5)."},
            "name": {"type": "string", "description": "Object name (optional)."},
        },
        "required": ["image_path"],
        "additionalProperties": False,
    },
)
def game3d_load_reference(image_path: str, axis: str = "front",
                          size: float = 2.0, opacity: float = 0.5,
                          name: str | None = None) -> dict[str, Any]:
    return run_on_main(modeling.load_reference, image_path, axis, size, opacity, name)


# --------------------------------------------------------------------------- #
# Texture
# --------------------------------------------------------------------------- #

@registry.tool(
    name="game3d_apply_texture",
    description=(
        "Apply an image as a base-colour texture on a mesh, generating a basic "
        "planar UV unwrap when the mesh has none (so the texture actually "
        "appears rather than sampling one pixel across every face). For a "
        "single wooden crate, a rock face, or a hero prop — the surface-detail "
        "lever between plain palette colours and full PBR."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "object_name": {"type": "string"},
            "image_path": {"type": "string"},
            "unwrap": {"type": "boolean",
                       "description": "Generate a planar unwrap when missing (default true)."},
        },
        "required": ["object_name", "image_path"],
        "additionalProperties": False,
    },
)
def game3d_apply_texture(object_name: str, image_path: str,
                         unwrap: bool = True) -> dict[str, Any]:
    return run_on_main(modeling.apply_texture, object_name, image_path, unwrap)


# --------------------------------------------------------------------------- #
# Subdivide
# --------------------------------------------------------------------------- #

@registry.tool(
    name="game3d_subdivide",
    description=(
        "Add and apply Subdivision Surface. smooth=true (Catmull-Clark) rounds "
        "the mesh — the primary high-poly lever for organic shapes. "
        "smooth=false (SIMPLE) just adds resolution without changing shape. "
        "levels doubles the poly count each step; stay ≤3 for game assets or "
        "the tri budget explodes."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "object_name": {"type": "string"},
            "levels": {"type": "integer", "minimum": 0, "maximum": 6},
            "smooth": {"type": "boolean"},
        },
        "required": ["object_name"],
        "additionalProperties": False,
    },
)
def game3d_subdivide(object_name: str, levels: int = 2,
                     smooth: bool = True) -> dict[str, Any]:
    return run_on_main(modeling.subdivide, object_name, levels, smooth)


# --------------------------------------------------------------------------- #
# Bevel
# --------------------------------------------------------------------------- #

@registry.tool(
    name="game3d_bevel",
    description=(
        "Bevel every edge of the mesh — the hard-surface detail lever. "
        "A bare edge looks cheap under normal lighting; even a 2mm chamfer "
        "with 2 segments catches highlights realistically. Segments=2 is the "
        "cheapest useful setting; 4+ gives a genuine round-over at higher cost."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "object_name": {"type": "string"},
            "width": {"type": "number", "minimum": 0.0001, "maximum": 1.0,
                      "description": "Bevel width in metres (default 0.02)."},
            "segments": {"type": "integer", "minimum": 1, "maximum": 12},
            "profile": {"type": "number", "minimum": 0.0, "maximum": 1.0,
                        "description": "0.5 = circular, 1.0 = squared."},
        },
        "required": ["object_name"],
        "additionalProperties": False,
    },
)
def game3d_bevel(object_name: str, width: float = 0.02, segments: int = 2,
                 profile: float = 0.5) -> dict[str, Any]:
    return run_on_main(modeling.bevel, object_name, width, segments, profile)


# --------------------------------------------------------------------------- #
# Boolean
# --------------------------------------------------------------------------- #

@registry.tool(
    name="game3d_boolean",
    description=(
        "CSG boolean between two meshes — hard-surface construction. UNION "
        "welds them, DIFFERENCE cuts B out of A (holes, slots), INTERSECT "
        "keeps only the shared volume. Result replaces A; B is removed unless "
        "keep_b=true (set that when B is a re-usable cutter)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "a_name": {"type": "string", "description": "The mesh to modify."},
            "b_name": {"type": "string", "description": "The other operand."},
            "operation": {"type": "string", "enum": list(modeling.BOOLEAN_OPS)},
            "keep_b": {"type": "boolean"},
        },
        "required": ["a_name", "b_name", "operation"],
        "additionalProperties": False,
    },
)
def game3d_boolean(a_name: str, b_name: str, operation: str,
                   keep_b: bool = False) -> dict[str, Any]:
    return run_on_main(modeling.boolean, a_name, b_name, operation, keep_b)


# --------------------------------------------------------------------------- #
# Extrude
# --------------------------------------------------------------------------- #

@registry.tool(
    name="game3d_extrude",
    description=(
        "Extrude a subset of faces along a direction — the primary modeling "
        "verb for pulling geometry out of a starting shape (roof spire from a "
        "tower top, blade from a hilt, ridge from a wall). Without a selector "
        "the whole mesh is extruded; with one, only matching faces are. "
        "Selectors: {normal_axis: 'z+'} for faces pointing up; {above: {axis: "
        "'z', value: 0.8}} for faces above a height."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "object_name": {"type": "string"},
            "direction": {"type": "array", "items": {"type": "number"},
                          "minItems": 3, "maxItems": 3,
                          "description": "Unit-ish direction; scaled by distance."},
            "distance": {"type": "number"},
            "select": {"type": "object",
                       "description": "Face selector; omit to extrude everything."},
        },
        "required": ["object_name"],
        "additionalProperties": True,
    },
)
def game3d_extrude(object_name: str,
                   direction: list | None = None,
                   distance: float = 0.5,
                   select: dict | None = None) -> dict[str, Any]:
    return run_on_main(
        modeling.extrude, object_name, tuple(direction or [0, 0, 1]),
        distance, select,
    )


# --------------------------------------------------------------------------- #
# Join
# --------------------------------------------------------------------------- #

@registry.tool(
    name="game3d_join",
    description=(
        "Merge several meshes into one object. Materials are preserved and "
        "re-slotted; source objects are removed. Do this before export when "
        "your engine expects one mesh per prop rather than a hierarchy."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "names": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "result_name": {"type": "string"},
        },
        "required": ["names"],
        "additionalProperties": False,
    },
)
def game3d_join(names: list[str], result_name: str = "joined") -> dict[str, Any]:
    return run_on_main(modeling.join_objects, names, result_name)
