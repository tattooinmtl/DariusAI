"""Scene, materials, camera and lighting for game-asset renders.

The default is a three-quarter "isometric" view on a transparent
background — the framing a 2D game needs for a sprite sheet, and the one
that shows a model's silhouette honestly.
"""

from __future__ import annotations

import math

import bpy

from . import palettes

MATERIAL_PREFIX = "g3d_"


# ---- materials --------------------------------------------------------------

def material_for(role: str, palette: str | None = None):
    """One material per (palette, role), reused across every object so a
    scene with forty parts still has six materials."""
    palette_name = palettes.describe(palette)["palette"]
    name = f"{MATERIAL_PREFIX}{palette_name}_{role}"
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing

    colour = palettes.resolve(palette_name, role)
    roughness, metallic = palettes.SURFACE.get(role, (0.8, 0.0))

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = palettes.rgba(colour)
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
        if role == "emissive":
            # Socket names moved around in 4.x; set what exists.
            for socket_name in ("Emission Color", "Emission"):
                if socket_name in bsdf.inputs:
                    bsdf.inputs[socket_name].default_value = palettes.rgba(colour)
                    break
            if "Emission Strength" in bsdf.inputs:
                bsdf.inputs["Emission Strength"].default_value = 3.0
    material.diffuse_color = palettes.rgba(colour)  # viewport fallback
    return material


def palette_materials(palette: str | None = None) -> dict:
    return {role: material_for(role, palette) for role in palettes.ROLES}


def repalette(palette: str) -> int:
    """Recolour everything in place by rewriting the existing materials —
    cheaper and safer than rebuilding the model, and it keeps object names
    and slots stable for anything already pointing at them."""
    changed = 0
    for material in bpy.data.materials:
        if not material.name.startswith(MATERIAL_PREFIX):
            continue
        role = material.name.rsplit("_", 1)[-1]
        if role not in palettes.ROLES:
            continue
        colour = palettes.resolve(palette, role)
        roughness, metallic = palettes.SURFACE.get(role, (0.8, 0.0))
        if material.use_nodes:
            bsdf = material.node_tree.nodes.get("Principled BSDF")
            if bsdf is not None:
                bsdf.inputs["Base Color"].default_value = palettes.rgba(colour)
                bsdf.inputs["Roughness"].default_value = roughness
                bsdf.inputs["Metallic"].default_value = metallic
        material.diffuse_color = palettes.rgba(colour)
        changed += 1
    return changed


# ---- scene ------------------------------------------------------------------

def clear_scene() -> int:
    """Empty the scene without `bpy.ops` — operators need a context that
    background mode does not provide."""
    removed = 0
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1
    for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.lights,
                       bpy.data.cameras, bpy.data.images):
        for block in list(collection):
            if block.users == 0:
                collection.remove(block)
    return removed


def available_engines() -> list[str]:
    """Ask Blender, don't assume. The EEVEE identifier has changed twice —
    `BLENDER_EEVEE`, then `BLENDER_EEVEE_NEXT` in 4.2, and back to
    `BLENDER_EEVEE` in 5.x — so any hardcoded name is wrong on some build."""
    return [item.identifier
            for item in bpy.context.scene.render.bl_rna.properties["engine"].enum_items]


def resolve_engine(engine: str | None) -> str:
    available = available_engines()
    wanted = (engine or "CYCLES").upper()
    if wanted in available:
        return wanted
    # Asked-for name isn't on this build: prefer the same family.
    family = ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE") if "EEVEE" in wanted else ("CYCLES",)
    for candidate in (*family, "CYCLES", "BLENDER_EEVEE", "BLENDER_EEVEE_NEXT",
                      "BLENDER_WORKBENCH"):
        if candidate in available:
            return candidate
    return available[0]


def setup_render(resolution: int = 512, samples: int = 128, engine: str = "CYCLES",
                 transparent: bool = True) -> dict:
    scene = bpy.context.scene
    scene.render.engine = resolve_engine(engine)

    scene.render.resolution_x = scene.render.resolution_y = int(resolution)
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = bool(transparent)
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"

    if scene.render.engine == "CYCLES":
        scene.cycles.samples = int(samples)
        scene.cycles.use_denoising = True
    else:
        eevee = getattr(scene, "eevee", None)
        if eevee is not None and hasattr(eevee, "taa_render_samples"):
            eevee.taa_render_samples = int(samples)

    return {"engine": scene.render.engine, "resolution": int(resolution),
            "samples": int(samples), "transparent": bool(transparent)}


def setup_camera(target=(0.0, 0.0, 0.0), distance: float = 12.0, azimuth: float = 45.0,
                 elevation: float = 30.0, orthographic: bool = True, scale: float = 8.0):
    """Three-quarter view. Orthographic by default: game art is composited
    from many renders, and perspective makes them refuse to line up."""
    camera_data = bpy.data.cameras.new("g3d_camera")
    camera_data.type = "ORTHO" if orthographic else "PERSP"
    camera_data.ortho_scale = scale
    camera = bpy.data.objects.new("g3d_camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)

    az, el = math.radians(azimuth), math.radians(elevation)
    camera.location = (
        target[0] + distance * math.cos(el) * math.cos(az),
        target[1] - distance * math.cos(el) * math.sin(az),
        target[2] + distance * math.sin(el),
    )
    _point_at(camera, target)
    bpy.context.scene.camera = camera
    return camera


def _point_at(obj, target) -> None:
    from mathutils import Vector  # noqa: PLC0415 - bpy-only dependency

    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def setup_lighting(strength: float = 4.0, warm: bool = True) -> list:
    """Key, fill and rim. Enough to read shape without blowing out the
    palette the caller asked for."""
    lights = []
    specs = [
        ("key", "AREA", (6.0, -6.0, 8.0), strength * 250, (1.0, 0.95, 0.88) if warm else (1, 1, 1), 6.0),
        ("fill", "AREA", (-7.0, -4.0, 4.0), strength * 90, (0.85, 0.90, 1.0), 8.0),
        ("rim", "AREA", (-3.0, 7.0, 6.0), strength * 140, (0.95, 0.97, 1.0), 5.0),
    ]
    for name, kind, location, power, colour, size in specs:
        data = bpy.data.lights.new(f"g3d_{name}", type=kind)
        data.energy = power
        data.color = colour
        if hasattr(data, "size"):
            data.size = size
        light = bpy.data.objects.new(f"g3d_{name}", data)
        light.location = location
        bpy.context.scene.collection.objects.link(light)
        _point_at(light, (0.0, 0.0, 1.0))
        lights.append(light)

    world = bpy.context.scene.world or bpy.data.worlds.new("g3d_world")
    bpy.context.scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.05, 0.06, 0.09, 1.0)
        background.inputs["Strength"].default_value = 0.6
    return lights


def frame_object(obj, margin: float = 1.25) -> float:
    """Fit the orthographic camera to what was just built, so a small unit
    and a large keep both fill the frame."""
    camera = bpy.context.scene.camera
    if camera is None or obj is None or camera.data.type != "ORTHO":
        return 0.0
    from .geometry import bounds  # noqa: PLC0415 - avoids a cycle at import

    size = bounds(obj)["size"]
    extent = max(size[0], size[1], size[2]) or 1.0
    camera.data.ortho_scale = extent * margin
    return camera.data.ortho_scale


def render_to(path: str) -> str:
    scene = bpy.context.scene
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    return scene.render.filepath
