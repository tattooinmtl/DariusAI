---
name: 3dgame
description: >-
  Build 3D game assets in Blender through DariusAI's MCP bridge and export
  them for Unity, Unreal, Godot, C4D, Three.js or any other engine. Covers
  parametric structures/units, procedural modeling (subdivide/bevel/boolean/
  extrude), sculpting through spatial functions and metaballs, image
  references, and glTF/FBX/OBJ export.
allowed-tools: Read, Bash
---

# 3D Game Assets

Drive Blender from DariusAI over MCP to produce real 3D assets — parametric
structures, sculpted organic shapes, hard-surface props — and export them in
formats every engine imports. No paid services, no external mesh generation:
Blender does the geometry, an LLM composes the tool calls.

## Before every task

1. Call `game3d_health_check`. It returns the Blender version, the render
   engine, and the archetypes and palettes this build offers.
2. Check the Blender light in the title bar if the call fails:
   - **green** — connected, go ahead
   - **orange** — something is answering on the port but it is not a healthy
     bridge (wrong port, or Blender is mid-start)
   - **red** — Blender is not running. Ask the user to start it, or offer
     Settings → Blender → Launch Blender.
3. Never guess archetype or palette names. `game3d_list_archetypes` is the
   authority and is cheap.

## The three modeling loops

Pick one based on what you're making. They compose freely — sculpt a base,
add hard-surface details, export.

### 1. Parametric block-out (fastest)

```
game3d_scene_setup      → clear, engine, resolution, camera, lights
game3d_build_structure  → house, tower, wall, gate, storage, shrine, workshop
game3d_build_unit       → worker, melee, ranged, mounted, caster
game3d_apply_palette    → recolour the whole set at once
```

`game3d_scene_setup` clears the scene by default. Call it **once** at the
start of a set, not before each asset, or you will delete what you just made.

### 2. Procedural modeling (hard-surface, high detail)

```
game3d_load_reference   → optional: image plane on front/side/top
game3d_extrude          → pull faces out along a direction
game3d_bevel            → chamfer edges (a bare edge looks cheap under light)
game3d_boolean          → UNION welds, DIFFERENCE cuts holes, INTERSECT keeps the overlap
game3d_subdivide        → smooth=true for Catmull-Clark; the main high-poly lever
game3d_apply_texture    → image as base colour, with auto-unwrap if there are no UVs
game3d_join             → merge parts into one export-ready object
```

The composition order matters. Bevel *before* subdivide, or the bevels get
lost in the smoothing. Boolean *before* subdivide, or the CSG solver
struggles with the extra faces.

### 3. Sculpting (organic shapes)

Sculpting here is **not brush-driven** — an LLM can't drag a stylus. Instead,
every sculpt op takes a **region spec** (a sphere in space, a box, a
half-space) and applies a brush-equivalent operation to just the vertices
that region selects.

```
game3d_sculpt_start          → uniform-topology base mesh (sphere/cube/cylinder)
game3d_sculpt_from_metaballs → build organic shape from spheres/capsules
game3d_sculpt_displace       → push vertices along their normals (clay/dent)
game3d_sculpt_smooth         → Laplacian smooth in a region (shift+brush)
game3d_sculpt_pinch          → pull toward region centre (crease brush)
game3d_sculpt_grab           → translate a region as a soft group (grab brush)
game3d_sculpt_from_sdf       → mesh a signed-distance-field expression
```

### Region spec

Every sculpt op takes a `region` dict. Supported forms:

- `{"sphere": [x, y, z, r]}` — smooth ball centred at (x,y,z) with radius r
- `{"box": [x1, y1, z1, x2, y2, z2], "feather": 0.1}` — axis-aligned box; `feather` softens the outside
- `{"axis_above": {"axis": "z", "value": 0.5, "feather": 0.1}}` — half-space above a plane
- `{"all": true}` — every vertex

Optional `"falloff"`: `"smooth"` (default) | `"linear"` | `"constant"` | `"sharp"`.

## Export — the step that matters

`game3d_export_model(output_path, format, objects)`

| Format | Extension | Use for |
|---|---|---|
| `glb` | `.glb` | **Default.** Unity, Unreal, Godot, Three.js, Babylon.js all import natively |
| `gltf` | `.gltf` | Same as glb but human-readable + separate texture files |
| `fbx` | `.fbx` | Unity / Unreal / C4D pipelines that predate glTF; keeps rigs |
| `obj` | `.obj` | Universal, geometry only, no rigs or animation |
| `stl` | `.stl` | 3D printing |

`output_path` must be absolute. `objects` is optional — omit it to export
everything (minus cameras and lights); pass names to export a subset.

## Palettes (parametric only)

`stone`, `timber`, `desert`, `verdant`, `obsidian`, `frost`, `crimson`, `azure`.

Every palette defines the same six roles (base, accent, roof, trim, metal,
emissive), so `game3d_apply_palette` recolours a whole set in one call without
rebuilding anything.

## Worked examples

### A watchtower for Unity

```
game3d_scene_setup(resolution=1024)
game3d_build_structure(archetype="tower", palette="stone", storeys=4)
game3d_bevel(object_name="g3d_tower", width=0.03, segments=2)
game3d_apply_texture(object_name="g3d_tower", image_path="C:/refs/stone.png")
game3d_export_model(output_path="C:/assets/watchtower.glb", format="glb")
```

### A crate with iron banding, exported as FBX

```
game3d_scene_setup()
game3d_build_structure(archetype="storage", palette="timber")
game3d_bevel(object_name="g3d_storage", width=0.02, segments=2)

# Iron bands: three thin boxes, boolean-unioned to the crate
# (use execute_python for the bespoke geometry, or build them as unit
# structures if the archetypes cover it)
game3d_export_model(output_path="C:/assets/crate.fbx", format="fbx")
```

### A dragon head via metaballs

```
game3d_sculpt_from_metaballs(
  primitives=[
    {"type": "BALL",     "pos": [0, 0, 0],       "radius": 1.0},   # cranium
    {"type": "CAPSULE",  "pos": [1.2, 0, -0.2],  "size": [0.6, 0.3, 0.3]},  # snout
    {"type": "ELLIPSOID","pos": [0.3, 0.4, 0.9], "size": [0.15, 0.15, 0.4]},  # horn L
    {"type": "ELLIPSOID","pos": [0.3, -0.4, 0.9],"size": [0.15, 0.15, 0.4]}, # horn R
  ],
  name="dragon_head", resolution=0.04,
)
# Eye sockets: displace inward with two spheres
game3d_sculpt_displace(name="dragon_head",
  region={"sphere": [0.5,  0.3, -0.1, 0.15]}, distance=-0.08)
game3d_sculpt_displace(name="dragon_head",
  region={"sphere": [0.5, -0.3, -0.1, 0.15]}, distance=-0.08)
# Sharpen the snout ridge
game3d_sculpt_pinch(name="dragon_head",
  region={"box": [1.5, -0.05, -0.3, 2.0, 0.05, -0.1]}, strength=0.4)
game3d_sculpt_smooth(name="dragon_head",
  region={"all": True}, iterations=2, factor=0.3)
game3d_export_model(output_path="C:/assets/dragon_head.glb")
```

### Modeling from a reference photo

```
game3d_load_reference(image_path="C:/refs/sword_side.png", axis="right", size=1.5)
game3d_load_reference(image_path="C:/refs/sword_front.png", axis="front", size=1.5)

# Build the sword against the references — the plane is unlit so the
# reference stays legible while you render for feedback.
game3d_scene_setup(clear=False)   # clear=false so the reference planes survive
# … modeling ops here …
game3d_export_model(output_path="C:/assets/sword.glb")
```

### Hard-surface booleans

```
# A wall with a doorway carved through it.
game3d_build_structure(archetype="wall", palette="stone", name="wall1")
# Build a door-sized box as the cutter.
# (execute_python for a one-off primitive, or use core.create_object)
game3d_boolean(a_name="wall1", b_name="doorway_cutter", operation="DIFFERENCE")
game3d_bevel(object_name="wall1", width=0.015, segments=2)
game3d_export_model(output_path="C:/assets/wall_with_door.glb")
```

## Composition tips

- **Bevel before subdivide.** Subdivision smooths bevels into oblivion;
  bevel first, then subdivide, so the crease survives.
- **Boolean before subdivide.** The EXACT solver handles clean topology
  better than a dense subdivided mesh.
- **Sculpt operations stack.** Multiple light passes give a better result
  than one heavy one. Aim for `distance ≈ voxel_size` per displace call.
- **`sculpt_start` first for organic work.** A mesh with wildly varying
  edge lengths punishes every op that follows.
- **Render for feedback.** After a sculpt, call `game3d_render_asset` at a
  representative angle. If the result looks wrong, the next op should fix
  it — don't stack more operations on a bad shape hoping it converges.
- **`game3d_join` before export** when the engine expects one mesh per
  prop. Materials are preserved and re-slotted.

## Rendering (still supported — for icons, sprite sheets, previews)

`game3d_render_asset(output_path, frame_object, margin)`

- `output_path` must be absolute and end in `.png`.
- Pass `frame_object` to fit the orthographic camera to that object.
- Transparent background — drops straight into a 2D sprite or a UI icon.

Use for previews and 2D game assets; use `game3d_export_model` for anything
a 3D engine needs to import.

## When the toolkit is not enough

`execute_python` runs Python against `bpy` for anything the tools do not
cover — `bpy`, `bmesh`, `mathutils`, `math`, `random`, `json` and `numpy`
are in scope. Imports outside that set are rejected. Prefer the tools when
they fit: they keep results consistent and are far less likely to break.

Generic primitives are also available: `create_object`, `transform_object`,
`set_material`, `add_modifier`, `list_objects`, `get_object`, `delete_object`,
`render_image`, `scene_info`.

## Notes

- Blender's MCP bridge listens on **127.0.0.1:8765**. DariusAI's own web UI
  deliberately uses 8780 so the two never fight over the port.
- Every tool runs on Blender's main thread; long renders and dense
  marching-cubes passes are expected to block for a while rather than
  return early.
- SDF expressions run through a whitelist: only `sphere`, `box`, `capsule`,
  `torus`, `union`, `intersect`, `subtract`, `smooth_union`, `translate`,
  `scale` are in scope. No imports, no attribute access. A rejected
  expression comes back as an error the LLM should see and rewrite.
- The marching-cubes fallback uses a compact hand-rolled 256-case table;
  `scikit-image.measure.marching_cubes` gives smoother output and is used
  automatically when installed in Blender's Python.
- Headless batches are supported: `blender --background --python-expr`
  with `serve_background(...)` from the add-on.
