"""Proof-of-concept pipeline run: model + texture + render + export a simple
wooden aether-crystal crate, entirely headlessly.

This exists to validate the full chain end to end - Blender modeling,
procedural texturing, a review render for visual feedback, and a
Unity-import-ready FBX - before trusting the pipeline with real assets. It is
deliberately simple (one mesh, three baked textures, no rig) so any failure is
a pipeline problem, not an asset-complexity problem.

Run headless, no GUI or MCP connection needed:
    blender --background --python scripts/cli/build_poc_crate.py

Outputs (paths relative to the repo root):
    blend/props/crate.blend                  - editable source
    renders/crate_poc_v1.png                 - review render (visual feedback)
    exports/Props/Crate/Crate.fbx             - Unity-ready export
    exports/Props/Crate/Crate_BaseColor.png   - baked base color texture
    exports/Props/Crate/Crate_Roughness.png   - baked roughness texture
    exports/Props/Crate/Crate_Bump.png        - baked bump/height texture

v2 note: v1's wood texture was too subtle to read (flat orange/plastic) and
the plank seams were purely painted, not geometric, so they didn't line up
with the mesh. v2 adds real plank-seam grooves to the mesh, fixes the UV
unwrap (per-face axis projection instead of smart_project, so painted seams
land exactly on the geometric ones and wrap seamlessly around all four
sides), and rewrites the wood shader with stronger grain contrast, per-plank
color variation, and a bump map. Tuned interactively against a live Blender
MCP session before being folded back in here - see renders/crate_poc_v1.png
(v1, bad) vs renders/crate_poc_v1.png after a rerun (v2, current) for the
before/after.
"""

import math
import os
import random
import sys

import bmesh
import bpy

# Blender's --python runner doesn't add the script's own directory to
# sys.path the way plain `python script.py` does, so this needs doing by
# hand to import the sibling upm_package module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upm_package import write_package_json

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BLEND_PATH = os.path.join(REPO_ROOT, "blend", "props", "crate.blend")
RENDER_PATH = os.path.join(REPO_ROOT, "renders", "crate_poc_v1.png")
EXPORT_DIR = os.path.join(REPO_ROOT, "exports", "Props", "Crate")

# UPM package identity for this asset - see docs/artifact-pipeline.md. Bump
# PACKAGE_VERSION by hand before publishing a new tagged version; it has no
# relation to the "v1"/"v2" render-iteration notes in this file's docstring,
# which just narrate this script's own history.
PACKAGE_NAME = "com.tincan.props.crate"
PACKAGE_VERSION = "0.1.1"
PACKAGE_DISPLAY_NAME = "Crate (Aether Crystal Supply)"
PACKAGE_DESCRIPTION = "Wooden aether-crystal supply crate prop."
FBX_PATH = os.path.join(EXPORT_DIR, "Crate.fbx")

TEXTURE_SIZE = 512
PLANK_ROWS = 3


def build_crate_mesh():
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = "Crate"
    obj.scale = (0.6, 0.6, 0.6)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.location = (0.0, 0.0, 0.3)  # sit the crate on the ground plane (z=0)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()

    # Cut real plank-seam grooves into the 4 side faces (before the lid inset
    # and edge bevel, so the top plank row's edge stays on the cube boundary
    # and the bevel below only touches the original 12 cube edges).
    side_faces = [f for f in bm.faces if abs(f.normal.z) < 0.2 and len(f.verts) == 4]
    vertical_edges = set()
    for face in side_faces:
        zs = [v.co.z for v in face.verts]
        height = max(zs) - min(zs)
        for e in face.edges:
            if abs(e.verts[0].co.z - e.verts[1].co.z) > height * 0.5:
                vertical_edges.add(e)

    sub = bmesh.ops.subdivide_edges(
        bm, edges=list(vertical_edges), cuts=PLANK_ROWS - 1, use_grid_fill=True
    )
    plank_faces = list({g for g in sub["geom"] if isinstance(g, bmesh.types.BMFace)})
    bm.faces.ensure_lookup_table()
    bmesh.ops.inset_individual(bm, faces=plank_faces, thickness=0.012, depth=-0.02)

    # Recess the top face slightly, suggesting a lid.
    bm.faces.ensure_lookup_table()
    top_faces = [f for f in bm.faces if f.normal.z > 0.9]
    if top_faces:
        result = bmesh.ops.inset_region(bm, faces=top_faces, thickness=0.03, depth=0.0)
        inset_verts = list({v for f in result["faces"] for v in f.verts})
        bmesh.ops.translate(bm, vec=(0, 0, -0.012), verts=inset_verts)

    # Soften only the original outer cube edges (the vertical ones are no
    # longer full-length after the plank subdivision above, so this filter
    # naturally excludes the new plank-seam edges).
    bm.edges.ensure_lookup_table()
    outer_edges = [e for e in bm.edges if e.calc_length() > 0.25]
    bmesh.ops.bevel(bm, geom=outer_edges, offset=0.01, segments=2, affect="EDGES")

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return obj


def uv_unwrap(obj):
    """Per-face planar projection along the dominant normal axis.

    Deliberately not smart_project: that scatters islands at arbitrary
    rotation/scale, so a V-coordinate-driven plank pattern (see
    make_wood_textures) wouldn't line up with the mesh's actual plank seams
    or wrap continuously around the 4 side faces. Projecting side faces onto
    world Z for V guarantees both.
    """
    mesh = obj.data
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    uv_layer = mesh.uv_layers.active.data

    xs = [v.co.x for v in mesh.vertices]
    ys = [v.co.y for v in mesh.vertices]
    zs = [v.co.z for v in mesh.vertices]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    zmin, zmax = min(zs), max(zs)
    w, d, h = xmax - xmin, ymax - ymin, zmax - zmin

    for poly in mesh.polygons:
        n = poly.normal
        axis = max(range(3), key=lambda i: abs(n[i]))
        sign = 1.0 if n[axis] >= 0 else -1.0
        for loop_index in poly.loop_indices:
            vert_index = mesh.loops[loop_index].vertex_index
            co = mesh.vertices[vert_index].co
            if axis == 2:  # top/bottom faces -> XY plane
                u, v = (co.x - xmin) / w, (co.y - ymin) / d
            elif axis == 0:  # +-X faces -> YZ plane, V = height
                u = ((co.y - ymin) / d) if sign > 0 else (1.0 - (co.y - ymin) / d)
                v = (co.z - zmin) / h
            else:  # axis == 1, +-Y faces -> XZ plane, V = height
                u = (1.0 - (co.x - xmin) / w) if sign > 0 else ((co.x - xmin) / w)
                v = (co.z - zmin) / h
            uv_layer[loop_index].uv = (u, v)


def _clamp01(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def make_wood_textures(size=TEXTURE_SIZE, seed=42, plank_rows=PLANK_ROWS):
    """Procedurally paint plank-banded wood textures, pixel by pixel.

    Not a Cycles bake - this avoids depending on UV completeness/bake
    device availability in a headless container, at the cost of the bands
    not being pixel-perfect-aligned to the mesh's physical plank seams (the
    UV unwrap above gets them close enough that it reads correctly).

    Grain runs along the plank length (the V axis, i.e. across each board's
    height), not across it, matching how growth rings actually read on a
    sawn board. A bump map rides alongside base color/roughness so the seams
    and grain read under lighting instead of just being flat paint.
    """
    random.seed(seed)
    base_img = bpy.data.images.new("Crate_BaseColor", size, size, alpha=False)
    rough_img = bpy.data.images.new("Crate_Roughness", size, size, alpha=False)
    bump_img = bpy.data.images.new("Crate_Bump", size, size, alpha=False)

    # Per-plank random tint so boards don't all read as one painted surface.
    plank_tint = [(random.random() - 0.5) * 0.14 for _ in range(plank_rows)]

    base_px = [0.0] * (size * size * 4)
    rough_px = [0.0] * (size * size * 4)
    bump_px = [0.0] * (size * size * 4)

    seam_half_width = 0.018  # in V (0..1) units

    for y in range(size):
        v = y / size
        row_pos = (v * plank_rows) % 1.0
        row_idx = min(int(v * plank_rows), plank_rows - 1)
        seam_dist_v = min(row_pos, 1.0 - row_pos) * (1.0 / plank_rows)

        if seam_dist_v > seam_half_width:
            seam_shadow = 1.0
            seam_bump = 0.65
        else:
            t = seam_dist_v / seam_half_width
            seam_shadow = 0.18 + 0.82 * t
            seam_bump = 0.05 + 0.35 * t

        tint = plank_tint[row_idx]

        for x in range(size):
            u = x / size
            turb = math.sin(u * 2.3 + row_idx * 1.7) * 0.6 + math.sin(u * 5.1 + 2.0) * 0.3
            grain = (
                math.sin(row_pos * 13.0 + turb * 2.2) * 0.45
                + math.sin(row_pos * 48.0 + turb) * 0.12
            ) * 0.095
            jitter = (random.random() - 0.5) * 0.035

            idx = (y * size + x) * 4
            base_px[idx + 0] = _clamp01((0.46 + grain + jitter + tint) * seam_shadow)
            base_px[idx + 1] = _clamp01((0.29 + grain * 0.75 + jitter * 0.7 + tint * 0.7) * seam_shadow)
            base_px[idx + 2] = _clamp01((0.15 + grain * 0.5 + jitter * 0.5 + tint * 0.4) * seam_shadow)
            base_px[idx + 3] = 1.0

            r = _clamp01(
                0.68 + grain * 0.5 + jitter * 1.0 - (0.0 if seam_dist_v >= seam_half_width else 0.15)
            )
            rough_px[idx + 0] = r
            rough_px[idx + 1] = r
            rough_px[idx + 2] = r
            rough_px[idx + 3] = 1.0

            b = _clamp01(seam_bump + grain * 0.9 + jitter * 0.6)
            bump_px[idx + 0] = b
            bump_px[idx + 1] = b
            bump_px[idx + 2] = b
            bump_px[idx + 3] = 1.0

    base_img.pixels.foreach_set(base_px)
    rough_img.pixels.foreach_set(rough_px)
    bump_img.pixels.foreach_set(bump_px)
    base_img.update()
    rough_img.update()
    bump_img.update()
    return base_img, rough_img, bump_img


def save_textures(base_img, rough_img, bump_img):
    os.makedirs(EXPORT_DIR, exist_ok=True)

    base_img.filepath_raw = os.path.join(EXPORT_DIR, "Crate_BaseColor.png")
    base_img.file_format = "PNG"
    base_img.save()

    rough_img.filepath_raw = os.path.join(EXPORT_DIR, "Crate_Roughness.png")
    rough_img.file_format = "PNG"
    rough_img.colorspace_settings.name = "Non-Color"
    rough_img.save()

    bump_img.filepath_raw = os.path.join(EXPORT_DIR, "Crate_Bump.png")
    bump_img.file_format = "PNG"
    bump_img.colorspace_settings.name = "Non-Color"
    bump_img.save()


def build_material(obj, base_img, rough_img, bump_img):
    mat = bpy.data.materials.new("M_Crate")
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]

    tex_base = nt.nodes.new("ShaderNodeTexImage")
    tex_base.image = base_img
    tex_base.location = (-600, 300)

    tex_rough = nt.nodes.new("ShaderNodeTexImage")
    tex_rough.image = rough_img
    tex_rough.image.colorspace_settings.name = "Non-Color"
    tex_rough.location = (-600, 0)

    tex_bump = nt.nodes.new("ShaderNodeTexImage")
    tex_bump.image = bump_img
    tex_bump.image.colorspace_settings.name = "Non-Color"
    tex_bump.location = (-600, -300)

    bump_node = nt.nodes.new("ShaderNodeBump")
    bump_node.inputs["Strength"].default_value = 0.5
    bump_node.inputs["Distance"].default_value = 0.02
    bump_node.location = (-300, -300)

    nt.links.new(tex_base.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(tex_rough.outputs["Color"], bsdf.inputs["Roughness"])
    nt.links.new(tex_bump.outputs["Color"], bump_node.inputs["Height"])
    nt.links.new(bump_node.outputs["Normal"], bsdf.inputs["Normal"])

    obj.data.materials.append(mat)


def build_studio(target_obj):
    bpy.ops.mesh.primitive_plane_add(size=6, location=(0, 0, 0))
    floor = bpy.context.active_object
    floor.name = "Floor"
    floor_mat = bpy.data.materials.new("M_Floor")
    floor_mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
        0.05, 0.05, 0.06, 1.0,
    )
    floor_mat.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.9
    floor.data.materials.append(floor_mat)

    bpy.ops.object.camera_add(location=(1.6, -1.9, 1.15))
    cam = bpy.context.active_object
    cam.name = "ReviewCamera"
    direction = target_obj.location - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    bpy.ops.object.light_add(type="AREA", location=(2.0, -1.6, 2.2))
    key = bpy.context.active_object
    key.name = "KeyLight"
    key.data.energy = 400
    key.data.size = 0.6

    bpy.ops.object.light_add(type="AREA", location=(-2.2, -0.8, 1.2))
    fill = bpy.context.active_object
    fill.name = "FillLight"
    fill.data.energy = 100
    fill.data.size = 1.2

    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.03, 0.03, 0.035, 1.0)


def render_review(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 800
    scene.render.resolution_y = 800
    scene.render.resolution_percentage = 100
    try:
        scene.eevee.use_raytracing = True
    except AttributeError:
        pass
    try:
        scene.eevee.taa_render_samples = 64
    except AttributeError:
        pass
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def export_fbx(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.fbx(
        filepath=path,
        use_selection=True,
        global_scale=1.0,
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_ALL",
        axis_forward="-Z",
        axis_up="Y",
        object_types={"MESH"},
        use_mesh_modifiers=True,
        mesh_smooth_type="FACE",
        add_leaf_bones=False,
        bake_anim=False,
        path_mode="COPY",
        embed_textures=False,
    )


def main():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    crate = build_crate_mesh()
    uv_unwrap(crate)
    base_img, rough_img, bump_img = make_wood_textures()
    save_textures(base_img, rough_img, bump_img)
    build_material(crate, base_img, rough_img, bump_img)
    build_studio(crate)

    os.makedirs(os.path.dirname(BLEND_PATH), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)

    render_review(RENDER_PATH)
    export_fbx(crate, FBX_PATH)
    package_json_path = write_package_json(
        EXPORT_DIR,
        name=PACKAGE_NAME,
        version=PACKAGE_VERSION,
        display_name=PACKAGE_DISPLAY_NAME,
        description=PACKAGE_DESCRIPTION,
    )

    print("POC_BUILD_OK")
    print("blend:", BLEND_PATH)
    print("render:", RENDER_PATH)
    print("fbx:", FBX_PATH)
    print("package.json:", package_json_path)


if __name__ == "__main__":
    main()
