"""`game3d_*` — the /3dgame tool surface.

Six calls cover the whole loop: check, set up, build, recolour, render.
Each returns structured data (not prose) so the agent can decide what to do
next without re-reading the scene.
"""

from __future__ import annotations

import os
from typing import Any

import bpy

from ..game3d import palettes, scene, structures, units
from ..server.mcp_server import registry, run_on_main
from ..server.protocol import SERVER_VERSION

_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}


# ---- health -----------------------------------------------------------------

def _health_impl() -> dict[str, Any]:
    scn = bpy.context.scene
    return {
        "status": "ok",
        "addon_version": SERVER_VERSION,
        "blender": bpy.app.version_string,
        "engine": scn.render.engine,
        "resolution": [scn.render.resolution_x, scn.render.resolution_y],
        "camera": scn.camera.name if scn.camera else None,
        "objects": len(bpy.data.objects),
        "palettes": palettes.palette_names(),
        "structures": list(structures.ARCHETYPES),
        "units": list(units.ARCHETYPES),
        "ready": True,
    }


@registry.tool(
    name="game3d_health_check",
    description=("Readiness of the 3dgame kit: Blender version, render engine, camera, "
                 "and the archetypes and palettes available. Call this first."),
    input_schema=_NO_ARGS,
)
def game3d_health_check() -> dict[str, Any]:
    return run_on_main(_health_impl)


# ---- scene ------------------------------------------------------------------

def _setup_impl(resolution: int, samples: int, engine: str, clear: bool,
                transparent: bool) -> dict[str, Any]:
    removed = scene.clear_scene() if clear else 0
    render = scene.setup_render(resolution, samples, engine, transparent)
    camera = scene.setup_camera()
    lights = scene.setup_lighting()
    return {"cleared": removed, "camera": camera.name,
            "lights": [light.name for light in lights], **render}


@registry.tool(
    name="game3d_scene_setup",
    description=("Prepare a scene for asset rendering: clear it, set the render engine, "
                 "resolution and samples, and add a three-quarter orthographic camera "
                 "with three-point lighting on a transparent background."),
    input_schema={
        "type": "object",
        "properties": {
            "resolution": {"type": "integer", "minimum": 64, "maximum": 4096,
                           "description": "Square output size in pixels (default 512)."},
            "samples": {"type": "integer", "minimum": 1, "maximum": 4096,
                        "description": "Render samples (default 128)."},
            "engine": {"type": "string",
                       "description": ("Render engine: CYCLES for quality, EEVEE for speed. "
                                       "Resolved against what this Blender build actually "
                                       "has, so either EEVEE spelling works.")},
            "clear": {"type": "boolean", "description": "Empty the scene first (default true)."},
            "transparent": {"type": "boolean",
                            "description": "Transparent background (default true)."},
        },
        "additionalProperties": False,
    },
)
def game3d_scene_setup(resolution: int = 512, samples: int = 128, engine: str = "CYCLES",
                       clear: bool = True, transparent: bool = True) -> dict[str, Any]:
    return run_on_main(_setup_impl, int(resolution), int(samples), engine, bool(clear),
                       bool(transparent))


# ---- building ---------------------------------------------------------------

@registry.tool(
    name="game3d_build_structure",
    description=("Build a parametric building. Archetypes: house, tower, wall, gate, "
                 "storage, shrine, workshop. Returns the object name, dimensions and "
                 "polygon count."),
    input_schema={
        "type": "object",
        "properties": {
            "archetype": {"type": "string",
                          "enum": list(structures.ARCHETYPES),
                          "description": "Which structure to build."},
            "palette": {"type": "string", "enum": palettes.palette_names(),
                        "description": "Colour palette (default stone)."},
            "storeys": {"type": "integer", "minimum": 1, "maximum": 12},
            "footprint": {"type": "array", "items": {"type": "number"},
                          "minItems": 2, "maxItems": 2,
                          "description": "[width, depth] in metres."},
            "roof": {"type": "string", "enum": list(structures.ROOF_STYLES)},
            "name": {"type": "string", "description": "Object name (optional)."},
            "detail": {"type": "boolean", "description": "Add trim, windows, doors."},
        },
        "additionalProperties": False,
    },
)
def game3d_build_structure(archetype: str = "house", palette: str | None = None,
                           storeys: int | None = None, footprint: list | None = None,
                           roof: str | None = None, name: str | None = None,
                           detail: bool = True) -> dict[str, Any]:
    return run_on_main(structures.build, archetype, palette, storeys, footprint, roof,
                       name, detail)


@registry.tool(
    name="game3d_build_unit",
    description=("Build a parametric unit figure. Archetypes: worker, melee, ranged, "
                 "mounted, caster. Returns the object name, dimensions and polygon count."),
    input_schema={
        "type": "object",
        "properties": {
            "archetype": {"type": "string", "enum": list(units.ARCHETYPES)},
            "palette": {"type": "string", "enum": palettes.palette_names()},
            "height": {"type": "number", "minimum": 0.6, "maximum": 6.0,
                       "description": "Overall height in metres."},
            "build_factor": {"type": "number", "minimum": 0.5, "maximum": 2.5,
                             "description": "Bulk: 1.0 is average."},
            "weapon": {"type": "string", "enum": list(units.WEAPONS)},
            "name": {"type": "string"},
            "detail": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
)
def game3d_build_unit(archetype: str = "melee", palette: str | None = None,
                      height: float | None = None, build_factor: float | None = None,
                      weapon: str | None = None, name: str | None = None,
                      detail: bool = True) -> dict[str, Any]:
    return run_on_main(units.build, archetype, palette, height, build_factor, weapon,
                       name, detail)


# ---- palette ----------------------------------------------------------------

def _palette_impl(palette: str) -> dict[str, Any]:
    changed = scene.repalette(palette)
    return {"palette": palettes.describe(palette)["palette"], "materials_updated": changed,
            "available": palettes.palette_names()}


@registry.tool(
    name="game3d_apply_palette",
    description=("Recolour everything already built to a different palette, in place. "
                 "Rewrites the shared materials rather than rebuilding the models."),
    input_schema={
        "type": "object",
        "properties": {"palette": {"type": "string", "enum": palettes.palette_names()}},
        "required": ["palette"],
        "additionalProperties": False,
    },
)
def game3d_apply_palette(palette: str) -> dict[str, Any]:
    return run_on_main(_palette_impl, palette)


# ---- render -----------------------------------------------------------------

def _render_impl(output_path: str, frame_object: str | None, margin: float) -> dict[str, Any]:
    if bpy.context.scene.camera is None:
        raise RuntimeError("no camera in the scene — call game3d_scene_setup first")
    target = bpy.data.objects.get(frame_object) if frame_object else None
    if frame_object and target is None:
        raise ValueError(f"no object named {frame_object!r} to frame")
    if target is not None:
        scene.frame_object(target, margin)

    path = os.path.abspath(os.path.expanduser(output_path))
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    written = scene.render_to(path)
    return {
        "path": written,
        "exists": os.path.exists(written),
        "bytes": os.path.getsize(written) if os.path.exists(written) else 0,
        "framed": target.name if target is not None else None,
    }


@registry.tool(
    name="game3d_render_asset",
    description=("Render the scene to a PNG at an absolute path, optionally framing one "
                 "object first. Returns the path and the file size actually written."),
    input_schema={
        "type": "object",
        "properties": {
            "output_path": {"type": "string", "description": "Absolute .png path."},
            "frame_object": {"type": "string",
                             "description": "Object to fit in frame before rendering."},
            "margin": {"type": "number", "minimum": 1.0, "maximum": 4.0,
                       "description": "Framing margin (default 1.25)."},
        },
        "required": ["output_path"],
        "additionalProperties": False,
    },
)
def game3d_render_asset(output_path: str, frame_object: str | None = None,
                        margin: float = 1.25) -> dict[str, Any]:
    return run_on_main(_render_impl, output_path, frame_object, float(margin), timeout=900.0)


# ---- discovery --------------------------------------------------------------

@registry.tool(
    name="game3d_list_archetypes",
    description="Every structure archetype, unit archetype, roof style, weapon and palette.",
    input_schema=_NO_ARGS,
)
def game3d_list_archetypes() -> dict[str, Any]:
    # Pure data — no reason to take a trip through Blender's main thread.
    return {
        **structures.list_archetypes(),
        **units.list_archetypes(),
        "palettes": palettes.palette_names(),
    }
