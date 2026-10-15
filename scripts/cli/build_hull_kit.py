"""Build the first two shipyard parts - a deck tile and a hull side piece -
plus the shared hull texture atlas, headlessly.

This is the proof that the part contract (docs/part-contract.md) works:
parts fit their cells, tile without visible seams, share one material, and
pass scripts/cli/validate_part.py before anything is exported. The review
render lays the parts out as a small ship section (deck 3 wide x 5 long,
hull 2 high down both sides, the port side being the same part rotated
180 deg) so seams and rotation can be judged by eye.

Geometry is authored in Unity part-local coordinates (Y-up, bow +Z,
starboard +X, metres) and converted to Blender with to_blender(), so every
number in this file reads the same as the contract and the manifest.

Run headless, no GUI or MCP connection needed:
    blender --background --python scripts/cli/build_hull_kit.py

Outputs (paths relative to the repo root):
    blend/airship/hull_kit.blend                  - editable source
    renders/hull_kit_v0.1.0_outboard.png          - review render, outside
    renders/hull_kit_v0.1.0_deck.png              - review render, on deck
    exports/Ship/HullKit/<Part>.fbx               - one FBX per part
    exports/Ship/HullKit/HullAtlas_BaseColor.png  - shared atlas (sRGB)
    exports/Ship/HullKit/manifest.json            - parts, cells, sockets, colliders
    exports/Ship/HullKit/INTEGRATION.md           - human-readable version
    exports/Ship/HullKit/package.json             - UPM package
"""

import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Euler, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upm_package import write_package_json
from validate_part import SHARED_MATERIAL, canonical_name, to_blender, to_unity, triangle_count, validate_parts

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BLEND_PATH = os.path.join(REPO_ROOT, "blend", "airship", "hull_kit.blend")
EXPORT_DIR = os.path.join(REPO_ROOT, "exports", "Ship", "HullKit")

PACKAGE_NAME = "com.tincan.ship.hullkit"
PACKAGE_VERSION = "0.1.0"
PACKAGE_DISPLAY_NAME = "Hull Kit (Shipyard Parts)"
PACKAGE_DESCRIPTION = "Modular 1 m grid airship hull parts sharing one texture atlas."

RENDER_STEM = os.path.join(REPO_ROOT, "renders", f"hull_kit_v{PACKAGE_VERSION}")
ATLAS_NAME = "HullAtlas_BaseColor"

# --- Atlas layout -----------------------------------------------------------
# 1024 px square, four 512 px quadrants, each covering 1 m x 1 m of surface
# (512 px/m). Each quadrant's pattern is periodic over 1 m so a part whose
# face spans a full cell lines up with its neighbour's. INSET texels of
# gutter on each side hold the pattern's own wrap-around, so bilinear
# filtering and mipmaps at a region edge blend into matching pixels instead
# of the neighbouring region.
ATLAS_PX = 1024
REGION_PX = 512
INSET = 16
REGIONS = {
    "deck": (0, 1),   # (column, row) in quadrants, row 1 = top half
    "hull": (1, 1),
    "beam": (0, 0),
    "trim": (1, 0),   # brass / iron / canvas / paint swatches, unused so far
}
REGION_IDS = {name: i for i, name in enumerate(REGIONS)}

# Faces thinner than this (clinker ledges, slab edges seen edge-on) get all
# their UVs collapsed onto one point of solid seam colour. A sliver seen
# nearly edge-on makes the GPU pick a huge mip/anisotropy footprint and
# sample far outside its region - neighbouring regions' colours showed up as
# speckles in the first review render. A zero-area UV can't do that.
SLIVER_M = 0.05
SLIVER_UV = {"deck": (0.0, 0.5), "hull": (0.5, 0.125), "beam": (0.5, 0.5), "trim": (0.5, 0.5)}
REGION_NAMES = list(REGIONS)


def region_uv(region, s, t):
    col, row = REGIONS[region]
    scale = (REGION_PX - 2 * INSET) / ATLAS_PX
    return (
        (col * REGION_PX + INSET) / ATLAS_PX + s * scale,
        (row * REGION_PX + INSET) / ATLAS_PX + t * scale,
    )


# --- Atlas painting -----------------------------------------------------------

def _periodic_noise(s, t, rng, terms=6, max_freq=6):
    """Smooth noise in [-1, 1] that's exactly periodic over 1 m in s and t."""
    out = np.zeros_like(s)
    for _ in range(terms):
        fs, ft = rng.integers(0, max_freq + 1, 2)
        phase = rng.uniform(0, 2 * math.pi)
        out += np.sin(2 * math.pi * (fs * s + ft * t) + phase)
    return out / terms


def _seam(dist, half_width):
    """1 away from a seam, darkening to 0.2 at its centre."""
    return np.clip(0.2 + 0.8 * dist / half_width, 0.2, 1.0)


def paint_deck(s, t, rng):
    """Deck planks run fore-aft (t), five 0.2 m planks across (s)."""
    plank = np.floor(s * 5).astype(int) % 5
    frac = (s * 5) % 1.0
    seam = _seam(np.minimum(frac, 1 - frac) * 0.2, 0.007)
    tint = rng.uniform(-0.06, 0.06, 5)[plank]
    grain = 0.05 * np.sin(2 * math.pi * (40 * s + 0.6 * _periodic_noise(s, t, rng, max_freq=3)))
    blotch = 0.04 * _periodic_noise(s, t, rng, max_freq=2)
    # Butt joints on two of the five planks so the deck doesn't read as
    # endless boards; one joint per plank per metre keeps it periodic.
    joints = {1: 0.35, 3: 0.8}
    butt = np.ones_like(s)
    for p, at in joints.items():
        d = np.abs(((t - at + 0.5) % 1.0) - 0.5)
        butt = np.where(plank == p, np.minimum(butt, _seam(d, 0.005)), butt)
    base = 0.60 + tint + grain + blotch
    shade = seam * butt
    return np.stack([base * shade, (base * 0.68) * shade, (base * 0.42) * shade], -1)


def paint_hull(s, t, rng):
    """Hull strakes run fore-aft (s), 0.25 m tall, seams at 0.125 + 0.25k
    to match the clinker steps in the geometry."""
    pos = (t - 0.125) * 4
    strake = np.floor(pos).astype(int) % 4
    frac = pos % 1.0
    seam = _seam(np.minimum(frac, 1 - frac) * 0.25, 0.008)
    tint = rng.uniform(-0.05, 0.05, 4)[strake]
    grain = 0.04 * np.sin(2 * math.pi * (30 * t + 0.8 * _periodic_noise(s, t, rng, max_freq=3)))
    weather = 0.07 * _periodic_noise(s, t, rng, max_freq=3)
    # Lighter, sun-bleached upper edge of each strake.
    bleach = 0.05 * frac
    base = 0.42 + tint + grain + weather + bleach
    return np.stack([base * seam, base * 0.62 * seam, base * 0.40 * seam], -1)


def paint_beam(s, t, rng):
    """Heavy dark timber, grain along s."""
    grain = 0.04 * np.sin(2 * math.pi * (24 * t + 0.7 * _periodic_noise(s, t, rng, max_freq=3)))
    base = 0.30 + grain + 0.04 * _periodic_noise(s, t, rng, max_freq=2)
    return np.stack([base, base * 0.62, base * 0.38], -1)


def paint_trim(s, t, rng):
    """Placeholder swatch bands for future fittings: brass, iron, canvas, red paint."""
    swatches = np.array([
        (0.72, 0.52, 0.22),
        (0.17, 0.17, 0.19),
        (0.84, 0.79, 0.66),
        (0.55, 0.16, 0.10),
    ])
    band = np.clip(np.floor(t * 4).astype(int), 0, 3)
    wear = 1.0 + 0.06 * _periodic_noise(s, t, rng)
    return swatches[band] * wear[..., None]


PAINTERS = {"deck": paint_deck, "hull": paint_hull, "beam": paint_beam, "trim": paint_trim}


def make_atlas(seed=7):
    rng = np.random.default_rng(seed)
    rgba = np.ones((ATLAS_PX, ATLAS_PX, 4), dtype=np.float32)
    px = np.arange(REGION_PX)
    # Pixel -> metres within the region; gutter pixels wrap, by design.
    m = ((px + 0.5 - INSET) / (REGION_PX - 2 * INSET)) % 1.0
    s, t = np.meshgrid(m, m)  # rows are t (V), columns s (U)
    for name, (col, row) in REGIONS.items():
        rgb = PAINTERS[name](s, t, rng)
        rgb = rgb + rng.normal(0, 0.012, rgb.shape)  # fine per-pixel grit
        y0, x0 = row * REGION_PX, col * REGION_PX
        rgba[y0:y0 + REGION_PX, x0:x0 + REGION_PX, :3] = np.clip(rgb, 0, 1)

    img = bpy.data.images.new(ATLAS_NAME, ATLAS_PX, ATLAS_PX, alpha=False)
    img.pixels.foreach_set(rgba.ravel())  # Blender rows run bottom-up, same as V
    img.update()
    os.makedirs(EXPORT_DIR, exist_ok=True)
    img.filepath_raw = os.path.join(EXPORT_DIR, ATLAS_NAME + ".png")
    img.file_format = "PNG"
    img.save()
    return img


def make_material(atlas):
    mat = bpy.data.materials.new(SHARED_MATERIAL)
    mat.use_backface_culling = True  # match Unity's default, so renders show what players see
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.8
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = atlas
    tex.location = (-400, 200)
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


# --- Geometry (authored in Unity part-local space) ---------------------------

class PartMesh:
    """bmesh builder that takes Unity-space coordinates and tags each face
    with an atlas region and an optional grain axis for UV projection."""

    def __init__(self):
        self.bm = bmesh.new()
        self.region = self.bm.faces.layers.int.new("region")
        self.grain = self.bm.faces.layers.int.new("grain")

    def face(self, pts, region, grain=-1):
        verts = [self.bm.verts.new(to_blender(p)) for p in pts]
        f = self.bm.faces.new(verts)
        f[self.region] = REGION_IDS[region]
        f[self.grain] = grain
        return f

    def box(self, lo, hi, region, grain=-1):
        (x0, y0, z0), (x1, y1, z1) = lo, hi
        c = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
        for quad in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
            self.face([c[i] for i in quad], region, grain)

    def extrude_z(self, profile, z0, z1, region, grain=-1):
        """Closed (x, y) profile swept along Unity Z, with end caps."""
        n = len(profile)
        for i in range(n):
            (xa, ya), (xb, yb) = profile[i], profile[(i + 1) % n]
            self.face([(xa, ya, z0), (xb, yb, z0), (xb, yb, z1), (xa, ya, z1)], region, grain)
        self.face([(x, y, z0) for x, y in profile], region, grain)
        self.face([(x, y, z1) for x, y in reversed(profile)], region, grain)

    def to_object(self, name):
        bm = self.bm
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
        bm.free()
        project_uvs(mesh)
        obj = bpy.data.objects.new(name, mesh)
        return obj


def project_uvs(mesh):
    """Planar projection per face along its dominant Unity axis, in metres,
    into the face's atlas region. A face's coordinates are shifted by whole
    metres so they land in 0..1 - which is why the pattern must be periodic
    over 1 m and faces mustn't span more than one cell along a textured axis.
    """
    uv = mesh.uv_layers.new(name="UVMap").data
    region = mesh.attributes["region"].data
    grain = mesh.attributes["grain"].data
    # Default (s, t) axes per dominant normal axis, in Unity space.
    defaults = {0: (2, 1), 1: (0, 2), 2: (0, 1)}
    for poly in mesh.polygons:
        n = to_unity(poly.normal)
        axis = max(range(3), key=lambda a: abs(n[a]))
        s_ax, t_ax = defaults[axis]
        g = grain[poly.index].value
        if g >= 0 and g != axis:
            s_ax, t_ax = g, ({0, 1, 2} - {axis, g}).pop()
        pts = [to_unity(mesh.vertices[i].co) for i in poly.vertices]
        # x/z cells run -0.5..0.5, y cells 0..1: shift to 0..1 per cell.
        shift = lambda a: 0.5 if a != 1 else 0.0
        s_vals = [p[s_ax] + shift(s_ax) for p in pts]
        t_vals = [p[t_ax] + shift(t_ax) for p in pts]
        s_off = math.floor(min(s_vals) + 1e-4)
        t_off = math.floor(min(t_vals) + 1e-4)
        name = REGION_NAMES[region[poly.index].value]
        if min(max(s_vals) - min(s_vals), max(t_vals) - min(t_vals)) < SLIVER_M:
            point = region_uv(name, *SLIVER_UV[name])
            for li in poly.loop_indices:
                uv[li].uv = point
            continue
        for li, s, t in zip(poly.loop_indices, s_vals, t_vals):
            uv[li].uv = region_uv(name, s - s_off, t - t_off)
    for attr in ("region", "grain"):
        mesh.attributes.remove(mesh.attributes[attr])


def build_deck():
    """1x1x1 deck tile: 0.1 m plank slab whose top is the cell's top face,
    with one cross-ship joist underneath."""
    m = PartMesh()
    m.box((-0.5, 0.9, -0.5), (0.5, 1.0, 0.5), "deck")
    m.box((-0.5, 0.72, -0.07), (0.5, 0.9, 0.07), "beam", grain=0)
    obj = m.to_object("Deck_1x1x1")
    return obj, {
        "size": [1, 1, 1],
        "category": "structure",
        "walkable": True,
        "faces": {"+X": "deck", "-X": "deck", "+Z": "deck", "-Z": "deck", "+Y": "open", "-Y": "open"},
        "colliders": [("COL_Box_01", (0, 0.95, 0), (1.0, 0.1, 1.0))],
        "sockets": [],
    }


# Clinker strakes: 0.25 m tall, each one's lower edge proud of the one below.
# Cell boundaries cut mid-strake, so the profile at y=0 and y=1 is identical
# and hull pieces stack without a visible step.
HULL_INNER = -0.5
STRAKE_OUT_BOTTOM = -0.39
STRAKE_OUT_TOP = -0.42


def build_hull_side():
    """1x1x1 straight hull side. Skin sits on the cell's inboard (-X) face so
    it meets a deck tile in the next cell flush; planking and a vertical
    batten stand out towards +X (outboard)."""
    mid = (STRAKE_OUT_BOTTOM + STRAKE_OUT_TOP) / 2
    outer = [(mid, 0.0)]
    for y in (0.125, 0.375, 0.625, 0.875):
        outer += [(STRAKE_OUT_TOP, y), (STRAKE_OUT_BOTTOM, y)]
    outer.append((mid, 1.0))
    profile = [(HULL_INNER, 0.0)] + outer + [(HULL_INNER, 1.0)]

    m = PartMesh()
    m.extrude_z(profile, -0.5, 0.5, "hull")
    m.box((-0.43, 0.0, -0.06), (-0.34, 1.0, 0.06), "beam", grain=1)
    obj = m.to_object("HullSide_1x1x1")
    return obj, {
        "size": [1, 1, 1],
        "category": "structure",
        "walkable": False,
        "faces": {"+X": "outboard", "-X": "inboard", "+Z": "hull", "-Z": "hull", "+Y": "hull", "-Y": "hull"},
        "colliders": [("COL_Box_01", (-0.445, 0.5, 0), (0.11, 1.0, 1.0))],
        "sockets": [],
    }


def add_empty(parent, name, center, size=None, rotation=(0, 0, 0)):
    """Child empty at a Unity-space position. Boxes get size via scale on a
    unit cube display, so validate_part.py can read the extents back."""
    e = bpy.data.objects.new(name, None)
    e.parent = parent
    e.location = to_blender(center)
    e.rotation_euler = Euler(rotation)
    if size is None:
        e.empty_display_type = "ARROWS"
        e.empty_display_size = 0.25
    else:
        e.empty_display_type = "CUBE"
        e.empty_display_size = 1.0
        e.scale = (size[0] / 2, size[2] / 2, size[1] / 2)
    parent.users_collection[0].objects.link(e)
    return e


def register_part(obj, spec, mat, collection):
    collection.objects.link(obj)
    obj.data.materials.append(mat)
    obj["part_size"] = spec["size"]
    obj["part_category"] = spec["category"]
    obj["part_walkable"] = spec["walkable"]
    obj["part_faces"] = json.dumps(spec["faces"])
    for name, center, size in spec["colliders"]:
        add_empty(obj, name, center, size)
    for name, center, rotation in spec["sockets"]:
        add_empty(obj, name, center, rotation=rotation)


# --- Manifest -----------------------------------------------------------------

def _r(v):
    return [round(c, 4) + 0.0 for c in v]


def part_manifest(obj):
    from validate_part import footprint_cells

    sockets, colliders = [], []
    for child in sorted(obj.children, key=lambda c: c.name):
        mw = child.matrix_local
        pos = to_unity(mw.to_translation())
        if child.name.startswith("Socket_"):
            rot = mw.to_3x3()
            sockets.append({
                "name": canonical_name(child.name),
                "kind": child.name.split("_")[1],
                "position": _r(pos),
                "forward": _r(to_unity(rot @ Vector((0, -1, 0)))),
                "up": _r(to_unity(rot @ Vector((0, 0, 1)))),
            })
        else:
            s = child.scale * child.empty_display_size * 2
            colliders.append({
                "name": canonical_name(child.name),
                "type": child.name.split("_")[1].lower(),
                "center": _r(pos),
                "size": _r((s.x, s.z, s.y)),
            })
    walkable = obj["part_walkable"]
    top = max(to_unity(v.co).y for v in obj.data.vertices) if walkable else None
    return {
        "name": obj.name,
        "file": obj.name + ".fbx",
        "category": obj["part_category"],
        "size": list(obj["part_size"]),
        "cells": [list(c) for c in footprint_cells(obj)],
        "walkable_top": top,
        "faces": json.loads(obj["part_faces"]),
        "triangles": triangle_count(obj.data),
        "sockets": sockets,
        "colliders": colliders,
    }


def write_manifest(parts):
    manifest = {
        "scene": "hull_kit",
        "formats": ["fbx"],
        "coordinate_system": "Y-up, +Z is the bow (Unity forward), +X starboard; one exported unit is one meter",
        "contract": "docs/part-contract.md",
        "grid": {
            "cell_size": 1.0,
            "pivot": "centre of the bottom face of cell [0,0,0]",
            "cell_bounds": "cell [i,j,k] spans x i-0.5..i+0.5, y j..j+1, z k-0.5..k+0.5 in part space",
        },
        "material": {
            "name": SHARED_MATERIAL,
            "base_map": ATLAS_NAME + ".png",
            "shared_by": [p.name for p in parts],
        },
        "parts": [part_manifest(p) for p in parts],
    }
    path = os.path.join(EXPORT_DIR, "manifest.json")
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    return manifest


def write_integration(manifest):
    lines = [
        "# Hull Kit Integration",
        "",
        "Modular airship hull parts on a 1 m grid, built to",
        "`docs/part-contract.md` in the Blender repo. Generated with the assets;",
        "`manifest.json` holds the same information as structured data.",
        "",
        "## Coordinates",
        "",
        "Y-up, +Z is the bow, +X starboard, 1 unit = 1 m. Each part's pivot is",
        "the centre of the bottom face of its origin cell `[0,0,0]`. Positions",
        "below are part-local Unity coordinates.",
        "",
        "## Material",
        "",
        f"All parts use one material, `{SHARED_MATERIAL}`, with",
        f"`{ATLAS_NAME}.png` (sRGB) on Base Map. Remap every FBX's",
        f"`{SHARED_MATERIAL}` to the same Unity material so parts batch together.",
        "",
        "## Parts",
        "",
    ]
    for p in manifest["parts"]:
        lines += [
            f"### {p['name']}",
            "",
            f"- File: `{p['file']}`, {p['triangles']} triangles, cells {p['cells']}",
            f"- Walkable top: {'y = ' + str(p['walkable_top']) if p['walkable_top'] is not None else 'no'}",
            "- Faces: " + ", ".join(f"{k} `{v}`" for k, v in p["faces"].items()),
        ]
        for c in p["colliders"]:
            lines.append(f"- Collider `{c['name']}`: {c['type']}, centre {c['center']}, size {c['size']}")
        for s in p["sockets"]:
            lines.append(f"- Socket `{s['name']}`: position {s['position']}, forward {s['forward']}")
        lines.append("")
    lines += [
        "## Setup",
        "",
        "1. Import with the file's scale, no generated colliders, no rig or animation.",
        "2. Build colliders from each part's `colliders` list (the `COL_*` child",
        "   nodes carry the same boxes as scaled transforms) - never a mesh collider.",
        "3. Turn `Socket_*` child nodes into `ShipSocket` markers.",
        "",
    ]
    with open(os.path.join(EXPORT_DIR, "INTEGRATION.md"), "w") as f:
        f.write("\n".join(lines))


def export_part(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    for child in obj.children:
        # Give this part's children their canonical names for the export,
        # moving whichever other part's child currently holds that name.
        name = canonical_name(child.name)
        holder = bpy.data.objects.get(name)
        if holder is not None and holder is not child:
            holder.name = name + ".001"  # Blender bumps this to .002+ if taken
        child.name = name
        child.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.fbx(
        filepath=os.path.join(EXPORT_DIR, obj.name + ".fbx"),
        use_selection=True,
        global_scale=1.0,
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_ALL",
        axis_forward="-Z",
        axis_up="Y",
        bake_space_transform=True,  # identity transforms on every node in Unity
        object_types={"MESH", "EMPTY"},
        use_mesh_modifiers=True,
        mesh_smooth_type="FACE",
        add_leaf_bones=False,
        bake_anim=False,
        path_mode="STRIP",  # atlas sits next to the FBX, referenced by name
        embed_textures=False,
    )


# --- Review scene ---------------------------------------------------------------

def place(obj, cell, yaw_deg, collection):
    inst = bpy.data.objects.new(f"{obj.name}@{cell}", obj.data)
    inst.location = to_blender(Vector((cell[0], cell[1], cell[2])))
    inst.rotation_euler = (0, 0, math.radians(yaw_deg))  # Unity yaw == Blender Z rotation
    collection.objects.link(inst)


def build_preview(deck, hull):
    coll = bpy.data.collections.new("Preview")
    bpy.context.scene.collection.children.link(coll)
    for k in range(-2, 3):
        for i in (-1, 0, 1):
            place(deck, (i, 0, k), 0, coll)
        for j in (0, 1):
            place(hull, (2, j, k), 0, coll)      # starboard
            place(hull, (-2, j, k), 180, coll)   # port: same part, rotated
    return coll


def setup_render_scene():
    scene = bpy.context.scene
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    bg = world.node_tree.nodes.get("Background")
    bg.inputs[0].default_value = (0.42, 0.62, 0.85, 1.0)
    bg.inputs[1].default_value = 0.8

    sun_data = bpy.data.lights.new("Sun", "SUN")
    sun_data.energy = 3.5
    sun_data.color = (1.0, 0.93, 0.8)
    sun_data.angle = math.radians(3)
    sun = bpy.data.objects.new("Sun", sun_data)
    sun.rotation_euler = (math.radians(50), 0, math.radians(-35))
    scene.collection.objects.link(sun)

    cam = bpy.data.objects.new("ReviewCamera", bpy.data.cameras.new("ReviewCamera"))
    cam.data.lens = 35
    scene.collection.objects.link(cam)
    scene.camera = cam

    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 800
    for attr, value in (("use_raytracing", True), ("taa_render_samples", 64)):
        try:
            setattr(scene.eevee, attr, value)
        except AttributeError:
            pass
    scene.view_settings.view_transform = "AgX"
    return cam


def render_from(cam, eye_unity, target_unity, path):
    cam.location = to_blender(Vector(eye_unity))
    direction = to_blender(Vector(target_unity)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)

    atlas = make_atlas()
    mat = make_material(atlas)

    parts_coll = bpy.data.collections.new("Parts")
    bpy.context.scene.collection.children.link(parts_coll)
    parts = []
    for builder in (build_deck, build_hull_side):
        obj, spec = builder()
        register_part(obj, spec, mat, parts_coll)
        parts.append(obj)

    if not validate_parts(parts):
        sys.exit("Validation failed - nothing exported. See docs/part-contract.md.")

    for obj in parts:
        export_part(obj)
    manifest = write_manifest(parts)
    write_integration(manifest)
    write_package_json(
        EXPORT_DIR,
        name=PACKAGE_NAME,
        version=PACKAGE_VERSION,
        display_name=PACKAGE_DISPLAY_NAME,
        description=PACKAGE_DESCRIPTION,
    )

    # The parts themselves sit at the origin, overlapping preview cell
    # [0,0,0]; keep them out of the render.
    parts_coll.hide_render = True
    build_preview(*parts)
    cam = setup_render_scene()

    os.makedirs(os.path.dirname(BLEND_PATH), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)

    os.makedirs(os.path.dirname(RENDER_STEM), exist_ok=True)
    render_from(cam, (8.5, 4.0, 7.0), (0, 0.8, 0), RENDER_STEM + "_outboard.png")
    render_from(cam, (-1.2, 2.9, -5.5), (0.6, 1.0, 1.5), RENDER_STEM + "_deck.png")

    print("HULL_KIT_BUILD_OK")
    print("blend:", BLEND_PATH)
    print("exports:", EXPORT_DIR)


if __name__ == "__main__":
    main()
