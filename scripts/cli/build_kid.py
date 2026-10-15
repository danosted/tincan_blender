"""Build the base crew kid - body, Humanoid rig, skin weights, scarf bones,
UVs, baked PBR textures and the idle / walk / run / grab clips - headlessly,
and export it into the com.tincan.characters package.

Everything is generated here, so the same source always produces the same
FBX and textures (no Mixamo or hand-painting round trip):
  geometry   lofted tubes (MeshBuilder), each face tagged with a surface region
  UVs        per tube in metres, seams at the back / underside, packed into one
             atlas at uniform texel density (pack_uvs)
  materials  one procedural Principled material per region (REGIONS), baked by
             Cycles into a URP Lit set (bake_textures):
               T_Kid_BaseColor  sRGB albedo
               T_Kid_Normal     tangent space, OpenGL (+Y), MikkTSpace
               T_Kid_MaskMap    R metallic, G AO, A smoothness (linear)
             then two final materials sample those maps: M_Kid_Body and
             M_Kid_PlayerTint (the vest, tinted per player in Unity)
  rig/anim   25 Unity-Humanoid-named bones, clips baked from pose functions

Coordinates are Blender's (Z up, metres). The kid faces -Y, so their left
hand is +X; with axis_forward="-Z", axis_up="Y" that arrives in Unity facing
+Z with the left hand at -X, which is what a Unity Humanoid expects.

Run headless, no GUI or MCP connection needed:
    blender --background --python scripts/cli/build_kid.py
    python3 scripts/cli/review_board.py --asset kid_base   # then republish the board

Outputs (paths relative to the repo root):
    blend/characters/kid_base.blend                       - editable source
    packages/com.tincan.characters/Runtime/Models/SK_KidBase.fbx
    packages/com.tincan.characters/Runtime/Textures/T_Kid_*.png
    renders/review/kid_base/v<version>/                   - review panels + manifest
    renders/kid_base_v<version>_sheet.png                 - contact sheet (history)
    renders/kid_base_v<version>_report.json               - checks + numbers

The script refuses to export when a check fails; the report says which one.
"""

import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACKAGE_DIR = os.environ.get(
    "TINCAN_CHARACTERS_PACKAGE",
    os.path.join(REPO_ROOT, "packages", "com.tincan.characters"),
)
BLEND_PATH = os.path.join(REPO_ROOT, "blend", "characters", "kid_base.blend")
FBX_PATH = os.path.join(PACKAGE_DIR, "Runtime", "Models", "SK_KidBase.fbx")

# Bumped with the package version (packages/com.tincan.characters/package.json).
KID_VERSION = "0.2.0"
RENDER_STEM = os.path.join(REPO_ROOT, "renders", f"kid_base_v{KID_VERSION}")

FPS = 24
TARGET_HEIGHT = 1.30
TRI_BUDGET = 6000

# --- Surface regions ---------------------------------------------------------
# Every face belongs to one region. A region is a small PBR description that
# bake_textures() turns into a procedural Blender material and bakes into the
# texture atlas: base colour (sRGB), roughness, metallic, and a bump "detail"
# recipe (see region_material()). Colours are sRGB, converted when written.
#   detail: (kind, object-space scale, bump strength, colour variation)
REGIONS = {
    "skin":     {"base": (0.93, 0.74, 0.60), "rough": 0.55, "metal": 0.0, "detail": ("pores", 180, 0.04, 0.05)},
    "hair":     {"base": (0.30, 0.19, 0.12), "rough": 0.45, "metal": 0.0, "detail": ("strands", 55, 0.35, 0.25)},
    "eyes":     {"base": (0.08, 0.06, 0.06), "rough": 0.12, "metal": 0.0, "detail": None},
    "shirt":    {"base": (0.90, 0.86, 0.76), "rough": 0.80, "metal": 0.0, "detail": ("weave", 220, 0.18, 0.06)},
    # Tinted by the player colour at runtime (its own material slot), so keep it a light neutral.
    "vest":     {"base": (0.86, 0.86, 0.86), "rough": 0.85, "metal": 0.0, "detail": ("felt", 260, 0.12, 0.08)},
    "trousers": {"base": (0.30, 0.33, 0.40), "rough": 0.85, "metal": 0.0, "detail": ("twill", 160, 0.22, 0.10)},
    "leather":  {"base": (0.36, 0.23, 0.14), "rough": 0.42, "metal": 0.0, "detail": ("leather", 70, 0.20, 0.22)},
    "brass":    {"base": (0.86, 0.68, 0.36), "rough": 0.30, "metal": 1.0, "detail": ("pores", 120, 0.05, 0.12)},
    "scarf":    {"base": (0.78, 0.22, 0.16), "rough": 0.92, "metal": 0.0, "detail": ("knit", 140, 0.30, 0.10)},
}
REGION_ORDER = list(REGIONS)
# Final material slots after the bake. The vest gets its own so Unity can
# tint it per player with a plain URP Lit material (no custom shader).
MATERIAL_SLOTS = ["M_Kid_Body", "M_Kid_PlayerTint"]
TINT_REGIONS = {"vest"}

TEX_SIZE = 1024
# Stand-in player colour for Blender previews (sRGB); Unity sets the real one.
PREVIEW_PLAYER_COLOR = (0.25, 0.55, 0.85)
UV_MARGIN = 8 / TEX_SIZE  # pixels between islands, in UV units
TEXTURES = {  # file stem -> what it holds; written to the package's Runtime/Textures
    "T_Kid_BaseColor": "sRGB albedo",
    "T_Kid_Normal": "tangent-space normal, OpenGL (+Y)",
    "T_Kid_MaskMap": "R metallic, G ambient occlusion, B unused, A smoothness",
}


def srgb_to_linear(c):
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


def region_vertex_color(region):
    """(linear rgb, player mask) for the vertex colour layer the toon shader
    reads. Review-only geometry passes a plain sRGB tuple instead of a name."""
    if not isinstance(region, str):
        return srgb_to_linear(region), 0.0
    return srgb_to_linear(REGIONS[region]["base"]), 1.0 if region in TINT_REGIONS else 0.0


def region_index(region):
    return REGION_ORDER.index(region) if isinstance(region, str) else 0


# --- Skeleton ----------------------------------------------------------------
# (name, parent, head, tail). Names are Unity Humanoid auto-mapping names, so
# the avatar maps without a hand-written HumanDescription.
BONES = [
    ("Hips", None, (0, 0, 0.62), (0, 0, 0.70)),
    ("Spine", "Hips", (0, 0, 0.70), (0, 0, 0.82)),
    ("Chest", "Spine", (0, 0, 0.82), (0, 0, 0.98)),
    ("Neck", "Chest", (0, 0, 0.98), (0, 0, 1.04)),
    ("Head", "Neck", (0, 0, 1.04), (0, 0, 1.30)),
]
for side, sx in (("Left", 1), ("Right", -1)):
    BONES += [
        (f"{side}Shoulder", "Chest", (0.03 * sx, 0, 0.93), (0.14 * sx, 0, 0.93)),
        (f"{side}UpperArm", f"{side}Shoulder", (0.14 * sx, 0, 0.93), (0.35 * sx, 0, 0.93)),
        (f"{side}LowerArm", f"{side}UpperArm", (0.35 * sx, 0, 0.93), (0.54 * sx, 0, 0.93)),
        (f"{side}Hand", f"{side}LowerArm", (0.54 * sx, 0, 0.93), (0.63 * sx, 0, 0.93)),
        (f"{side}UpperLeg", "Hips", (0.075 * sx, 0, 0.60), (0.075 * sx, -0.005, 0.33)),
        (f"{side}LowerLeg", f"{side}UpperLeg", (0.075 * sx, -0.005, 0.33), (0.075 * sx, 0, 0.07)),
        (f"{side}Foot", f"{side}LowerLeg", (0.075 * sx, 0, 0.07), (0.075 * sx, -0.08, 0.025)),
        (f"{side}Toes", f"{side}Foot", (0.075 * sx, -0.08, 0.025), (0.075 * sx, -0.13, 0.025)),
    ]
# Scarf tail: not part of the Humanoid avatar; driven by SpringBoneChain in Unity.
SCARF_BONES = [f"Scarf_{i}" for i in range(4)]
_scarf_pts = [(0.0, 0.075, 0.975), (0.0, 0.10, 0.90), (0.0, 0.115, 0.83), (0.0, 0.125, 0.76), (0.0, 0.13, 0.69)]
for i, name in enumerate(SCARF_BONES):
    BONES.append((name, "Chest" if i == 0 else SCARF_BONES[i - 1], _scarf_pts[i], _scarf_pts[i + 1]))

HUMANOID_REQUIRED = [
    "Hips", "Spine", "Chest", "Neck", "Head",
    "LeftUpperArm", "LeftLowerArm", "LeftHand", "RightUpperArm", "RightLowerArm", "RightHand",
    "LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "RightUpperLeg", "RightLowerLeg", "RightFoot",
]


def build_armature(collection):
    arm_data = bpy.data.armatures.new("Rig")
    arm = bpy.data.objects.new("Rig", arm_data)
    collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for name, parent, head, tail in BONES:
        b = arm_data.edit_bones.new(name)
        b.head, b.tail = Vector(head), Vector(tail)
        b.roll = 0.0
        if parent:
            b.parent = arm_data.edit_bones[parent]
            b.use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    arm_data.display_type = "STICK"
    return arm


# --- Mesh builder ------------------------------------------------------------
def ellipse_perimeter(a, b):
    """Ramanujan's approximation."""
    h = ((a - b) / (a + b)) ** 2 if a + b > 0 else 0.0
    return math.pi * (a + b) * (1 + 3 * h / (10 + math.sqrt(4 - 3 * h)))


class MeshBuilder:
    """Accumulates lofted tubes into one mesh with per-face regions, per-corner
    UVs and per-vertex bone weights.

    UVs are laid out in metres (u around the tube, v along it), each tube and
    each cap its own island, so after pack_uvs() scales them all by the same
    factor every island has the same texel density."""

    def __init__(self):
        self.verts, self.faces, self.face_regions, self.face_uvs, self.face_islands = [], [], [], [], []
        self.weights = []
        self.n_islands = 0

    def _vert(self, p, w):
        self.verts.append(tuple(p))
        self.weights.append(w)
        return len(self.verts) - 1

    def loft(self, centers, axes_u, axes_v, radii, weights, region_fn, segments=12,
             cap_start=None, cap_end=None, seam=math.pi / 2):
        """Tube through `centers` with an elliptical ring per centre.

        axes_u/axes_v: per-ring unit vectors spanning the ring plane.
        radii: per-ring (ru, rv). weights: per-ring {bone: w}.
        region_fn(face_centre) -> region name (a key of REGIONS).
        cap_start/cap_end: None for a flat cap at the ring centre, or a point
        (a pole) to close the tube to.
        seam: ring angle (from axis_u towards axis_v) where the UV seam runs;
        the default, +axis_v, is the back of a tube whose axis_v is +Y.
        """
        rings, ring_pts = [], []
        for c, u, v, (ru, rv), w in zip(centers, axes_u, axes_v, radii, weights):
            c, u, v = Vector(c), Vector(u), Vector(v)
            ring, pts = [], []
            for s in range(segments):
                a = seam + 2 * math.pi * s / segments
                p = c + u * (ru * math.cos(a)) + v * (rv * math.sin(a))
                ring.append(self._vert(p, w))
                pts.append(p)
            rings.append(ring)
            ring_pts.append(pts)

        # Side island: u = arc length around the ring (centred, so tapering
        # tubes give trapezoids rather than stretch), v = mean surface distance
        # between consecutive rings.
        island = self._island()
        vs = [0.0]
        for p0, p1 in zip(ring_pts, ring_pts[1:]):
            vs.append(vs[-1] + sum((a - b).length for a, b in zip(p0, p1)) / segments)
        perims = [ellipse_perimeter(ru, rv) for ru, rv in radii]

        def uv(ri, s):
            return ((s / segments - 0.5) * perims[ri], vs[ri])

        for ri, (r0, r1) in enumerate(zip(rings, rings[1:])):
            for s in range(segments):
                s1 = (s + 1) % segments
                self._face([r0[s], r0[s1], r1[s1], r1[s]], region_fn,
                           [uv(ri, s), uv(ri, s + 1), uv(ri + 1, s + 1), uv(ri + 1, s)], island)

        # Caps: planar projection onto their end ring's plane, one island each.
        for ri, pole, flip in ((0, cap_start, False), (len(rings) - 1, cap_end, True)):
            c, u, v = Vector(centers[ri]), Vector(axes_u[ri]), Vector(axes_v[ri])
            pole = Vector(pole) if pole is not None else c
            pi = self._vert(pole, weights[ri])
            island = self._island()

            def puv(p):
                d = p - c
                return (d.dot(u), d.dot(v))

            for s in range(segments):
                s1 = (s + 1) % segments
                a, b = (rings[ri][s], rings[ri][s1]) if flip else (rings[ri][s1], rings[ri][s])
                pa, pb = (ring_pts[ri][s], ring_pts[ri][s1]) if flip else (ring_pts[ri][s1], ring_pts[ri][s])
                self._face([pi, a, b], region_fn, [puv(pole), puv(pa), puv(pb)], island)

    def _island(self):
        self.n_islands += 1
        return self.n_islands - 1

    def _face(self, idx, region_fn, uvs, island):
        centre = sum((Vector(self.verts[i]) for i in idx), Vector()) / len(idx)
        self.faces.append(idx)
        self.face_regions.append(region_fn(centre))
        self.face_uvs.append(uvs)
        self.face_islands.append(island)

    def to_object(self, name, collection):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(self.verts, [], self.faces)
        assert not mesh.validate(), f"{name}: from_pydata produced invalid geometry"
        assert len(mesh.polygons) == len(self.faces)

        # Per-corner data is written while loop order still matches self.faces;
        # the normal recalculation below may flip faces, and bmesh carries the
        # corner data along when it does.
        uv_layer = mesh.uv_layers.new(name="UVMap")
        uvs = np.array([c for f in self.face_uvs for c in f], dtype=np.float32)
        uv_layer.data.foreach_set("uv", uvs.ravel())

        col = mesh.color_attributes.new("Col", "FLOAT_COLOR", "CORNER")
        cols = np.zeros((len(mesh.loops), 4), dtype=np.float32)
        for poly in mesh.polygons:
            rgb, mask = region_vertex_color(self.face_regions[poly.index])
            cols[poly.loop_start:poly.loop_start + poly.loop_total] = (*rgb, mask)
        col.data.foreach_set("color", cols.ravel())
        mesh.color_attributes.active_color = col

        for attr_name, values in (("region", [region_index(r) for r in self.face_regions]),
                                  ("island", self.face_islands)):
            a = mesh.attributes.new(attr_name, "INT", "FACE")
            a.data.foreach_set("value", values)
        mesh.polygons.foreach_set("material_index", [region_index(r) for r in self.face_regions])

        # Faces were wound by hand; make every normal point out of its tube.
        import bmesh
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(mesh)
        bm.free()
        for p in mesh.polygons:
            p.use_smooth = True
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)
        groups = {}
        for vi, w in enumerate(self.weights):
            for bone, val in w.items():
                if val <= 0:
                    continue
                if bone not in groups:
                    groups[bone] = obj.vertex_groups.new(name=bone)
                groups[bone].add([vi], val, "REPLACE")
        return obj


def blend(a, b, t):
    """Weights {a: 1-t, b: t}, clamped, dropping zeros."""
    t = max(0.0, min(1.0, t))
    w = {}
    if t < 1:
        w[a] = 1 - t
    if t > 0:
        w[b] = w.get(b, 0) + t
    return w


def ramp(x, lo, hi):
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def solid(region):
    return lambda _c: region


X, Y, Z = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))


def build_body(collection):
    mb = MeshBuilder()

    # Torso: crotch to neck base. Trousers below the belt, vest above.
    prof = [  # (z, half-width, half-depth)
        (0.53, 0.095, 0.075), (0.58, 0.115, 0.083), (0.64, 0.12, 0.085), (0.70, 0.115, 0.08),
        (0.76, 0.115, 0.078), (0.82, 0.125, 0.082), (0.88, 0.135, 0.083), (0.93, 0.13, 0.078),
        (0.965, 0.085, 0.06), (0.985, 0.05, 0.045),
    ]

    def torso_w(z):
        if z < 0.70:
            return blend("Hips", "Spine", ramp(z, 0.64, 0.74))
        if z < 0.86:
            return blend("Spine", "Chest", ramp(z, 0.76, 0.86))
        return {"Chest": 1.0}

    def torso_col(c):
        if c.z < 0.665:
            return "trousers"
        if c.z < 0.685:  # belt, with a buckle at the front
            return "brass" if abs(c.x) < 0.03 and c.y < 0 else "leather"
        return "vest"

    mb.loft([(0, 0, z) for z, _, _ in prof], [X] * len(prof), [Y] * len(prof),
            [(w, d) for _, w, d in prof], [torso_w(z) for z, _, _ in prof], torso_col, segments=16)

    # Neck.
    nz = [0.96, 1.0, 1.05]
    mb.loft([(0, 0.005, z) for z in nz], [X] * 3, [Y] * 3, [(0.038, 0.036)] * 3,
            [blend("Chest", "Neck", ramp(z, 0.97, 1.0)) for z in nz], solid("skin"), segments=10)

    # Head: an ellipsoid with a hair cap. Big head, kid proportions (~4.5 heads).
    hc, hr = Vector((0, -0.005, 1.165)), Vector((0.128, 0.135, 0.142))
    n_rings = 12
    thetas = [math.pi * (i + 0.5) / n_rings for i in range(n_rings)]
    centers = [hc + Z * (-hr.z * math.cos(t)) for t in thetas]
    radii = [(hr.x * math.sin(t), hr.y * math.sin(t)) for t in thetas]

    def head_col(c):
        rel = c - hc
        if rel.z > 0.045 or (rel.y > 0.02 and rel.z > -0.06) or (rel.y > -0.07 and rel.z > 0.085):
            return "hair"
        return "skin"

    mb.loft(centers, [X] * n_rings, [Y] * n_rings, radii, [{"Head": 1.0}] * n_rings, head_col,
            segments=16, cap_start=hc - Z * hr.z, cap_end=hc + Z * hr.z)

    # Eyes: small dark ovals sitting on the face.
    for sx in (1, -1):
        ec = Vector((0.045 * sx, -0.122, 1.15))
        ez = [ec.z - 0.022, ec.z, ec.z + 0.022]
        mb.loft([(ec.x, ec.y, z) for z in ez], [X] * 3, [Y] * 3,
                [(0.012, 0.012), (0.018, 0.016), (0.012, 0.012)], [{"Head": 1.0}] * 3, solid("eyes"),
                segments=8)

    # Ears.
    for sx in (1, -1):
        ex = [0.118 * sx, 0.142 * sx]
        mb.loft([(x, 0.0, 1.15) for x in ex], [Y, Y], [Z, Z], [(0.02, 0.03), (0.012, 0.022)],
                [{"Head": 1.0}] * 2, solid("skin"), segments=8)

    for side, sx in (("Left", 1), ("Right", -1)):
        # Arm: shoulder to wrist along +-X. Rolled sleeves, bare forearms.
        ax = [0.09, 0.14, 0.20, 0.27, 0.33, 0.37, 0.43, 0.49, 0.54]
        ar = [0.05, 0.047, 0.045, 0.043, 0.041, 0.039, 0.037, 0.034, 0.031]

        def arm_w(x, side=side):
            if x < 0.25:
                return blend(f"{side}Shoulder", f"{side}UpperArm", ramp(x, 0.12, 0.20))
            return blend(f"{side}UpperArm", f"{side}LowerArm", ramp(x, 0.32, 0.38))

        def arm_col(c):
            return "shirt" if abs(c.x) < 0.42 else "skin"

        mb.loft([(x * sx, 0, 0.93) for x in ax], [Y] * len(ax), [Z] * len(ax),
                [(r, r) for r in ar], [arm_w(x) for x in ax], arm_col, segments=10, seam=-math.pi / 2)

        # Hand: a flat mitten.
        hx = [0.53, 0.57, 0.61, 0.645]
        mb.loft([(x * sx, -0.005, 0.925) for x in hx], [Y] * 4, [Z] * 4,
                [(0.03, 0.022), (0.036, 0.024), (0.034, 0.022), (0.02, 0.014)],
                [{f"{side}Hand": 1.0}] * 4, solid("skin"), segments=10, seam=-math.pi / 2)

        # Leg: hip to ankle. Trousers tucked into boots.
        lz = [0.63, 0.56, 0.48, 0.40, 0.34, 0.29, 0.22, 0.15, 0.08, 0.05]
        lr = [0.068, 0.064, 0.058, 0.052, 0.048, 0.047, 0.045, 0.047, 0.046, 0.045]

        def leg_w(z, side=side):
            if z > 0.45:
                return blend("Hips", f"{side}UpperLeg", 1 - ramp(z, 0.54, 0.62))
            if z > 0.12:
                return blend(f"{side}UpperLeg", f"{side}LowerLeg", 1 - ramp(z, 0.30, 0.37))
            return blend(f"{side}LowerLeg", f"{side}Foot", 1 - ramp(z, 0.05, 0.10))

        def leg_col(c):
            return "trousers" if c.z > 0.19 else "leather"

        mb.loft([(0.075 * sx, 0, z) for z in lz], [X] * len(lz), [Y] * len(lz),
                [(r, r * 0.95) for r in lr], [leg_w(z) for z in lz], leg_col, segments=12)

        # Boot: heel to toe along -Y, flat-ish sole at z = 0.
        fy = [0.05, 0.02, -0.03, -0.08, -0.12, -0.15]
        fr = [(0.042, 0.04), (0.048, 0.045), (0.05, 0.042), (0.05, 0.036), (0.045, 0.03), (0.03, 0.02)]

        def foot_w(y, side=side):
            return blend(f"{side}Foot", f"{side}Toes", ramp(-y, 0.06, 0.10))

        mb.loft([(0.075 * sx, y, r[1] + 0.001) for y, r in zip(fy, fr)], [X] * 6, [Z] * 6, fr,
                [foot_w(y) for y in fy], solid("leather"), segments=10, seam=-math.pi / 2)

    return mb.to_object("Body", collection)


def build_scarf(collection):
    mb = MeshBuilder()
    # Wrap: a flattened ring around the neck, on the chest so it follows the torso.
    # Lofted once around a closed circle; the two end caps meet and hide each other.
    segs, ring_c, R = 20, Vector((0, 0.005, 0.975)), 0.058
    angles = [2 * math.pi * i / segs for i in range(segs + 1)]
    centers = [ring_c + Vector((R * math.cos(a), R * 1.05 * math.sin(a), 0)) for a in angles]
    radial = [Vector((math.cos(a), math.sin(a), 0)) for a in angles]
    mb.loft(centers, radial, [Z] * len(angles), [(0.022, 0.026)] * len(angles),
            [blend("Chest", "Neck", 0.35)] * len(angles), solid("scarf"), segments=8)

    # Tail: a flat ribbon down the back, skinned along the scarf chain.
    pts = [Vector(p) for p in _scarf_pts]
    centers, us, vs, rad, wts = [], [], [], [], []
    samples = 13
    for k in range(samples):
        t = k / (samples - 1) * (len(pts) - 1)
        i = min(int(t), len(pts) - 2)
        f = t - i
        c = pts[i].lerp(pts[i + 1], f)
        d = (pts[i + 1] - pts[i]).normalized()
        centers.append(c + Vector((0, 0.012, 0)))
        us.append(X)
        vs.append(d.cross(X).normalized())
        rad.append((0.034, 0.006))
        bi = min(i, len(SCARF_BONES) - 1)
        if f > 0.7 and bi + 1 < len(SCARF_BONES):
            wts.append(blend(SCARF_BONES[bi], SCARF_BONES[bi + 1], (f - 0.7) / 0.6))
        elif f < 0.3 and bi > 0:
            wts.append(blend(SCARF_BONES[bi - 1], SCARF_BONES[bi], 0.5 + f / 0.6))
        else:
            wts.append({SCARF_BONES[bi]: 1.0})
    mb.loft(centers, us, vs, rad, wts, solid("scarf"), segments=8)
    return mb.to_object("Scarf", collection)


def skin(obj, arm):
    obj.parent = arm
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm


# --- UVs, materials, bake ----------------------------------------------------
TEXTURE_DIR = os.path.join(PACKAGE_DIR, "Runtime", "Textures")


def pack_uvs(meshes):
    """Pack every island of every mesh into one 0..1 atlas. The islands are in
    metres, so one uniform scale keeps texel density equal across them;
    rotation is allowed, per-island rescaling is not."""
    bpy.ops.object.select_all(action="DESELECT")
    for m in meshes:
        m.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(udim_source="CLOSEST_UDIM", rotate=True, rotate_method="CARDINAL",
                            scale=True, merge_overlap=False, margin_method="FRACTION",
                            margin=UV_MARGIN, shape_method="CONCAVE")
    bpy.ops.object.mode_set(mode="OBJECT")


def sock(sockets, identifier):
    """A node socket by identifier. Mix nodes have one A/B/Result per data
    type, so looking them up by name returns the (hidden) float ones."""
    return next(x for x in sockets if x.identifier == identifier)


def _detail_nodes(nt, kind, scale):
    """A 0..1 height field for a surface-detail recipe, in object space so it
    runs across UV seams without a visible break."""
    n = nt.nodes
    coord = n.new("ShaderNodeTexCoord")
    mapping = n.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    nt.links.new(coord.outputs["Object"], mapping.inputs["Vector"])
    vec = mapping.outputs["Vector"]

    def noise(sc, detail=4.0, rough=0.5):
        t = n.new("ShaderNodeTexNoise")
        t.inputs["Scale"].default_value = sc
        t.inputs["Detail"].default_value = detail
        t.inputs["Roughness"].default_value = rough
        nt.links.new(vec, t.inputs["Vector"])
        return t.outputs["Fac"]

    def wave(direction, sc, distortion=0.0):
        t = n.new("ShaderNodeTexWave")
        t.wave_type = "BANDS"
        t.bands_direction = direction
        t.inputs["Scale"].default_value = sc
        t.inputs["Distortion"].default_value = distortion
        nt.links.new(vec, t.inputs["Vector"])
        return t.outputs["Fac"]

    def mix(a, b, op="MULTIPLY"):
        m = n.new("ShaderNodeMath")
        m.operation = op
        nt.links.new(a, m.inputs[0])
        nt.links.new(b, m.inputs[1])
        return m.outputs[0]

    if kind == "pores":
        return noise(1.0, 2.0)
    if kind == "strands":  # clumped strands running down the head
        return mix(wave("Z", 0.2, 6.0), noise(0.6, 2.0), "ADD")
    if kind == "weave":  # plain weave: two perpendicular thread sets
        return mix(wave("X", 1.0), wave("Z", 1.0), "MULTIPLY")
    if kind == "felt":
        return noise(1.0, 8.0, 0.7)
    if kind == "twill":
        return mix(wave("DIAGONAL", 1.0), noise(0.3, 2.0), "ADD")
    if kind == "leather":  # creased grain
        v = n.new("ShaderNodeTexVoronoi")
        v.feature = "DISTANCE_TO_EDGE"
        v.inputs["Scale"].default_value = 1.0
        nt.links.new(vec, v.inputs["Vector"])
        return mix(v.outputs["Distance"], noise(0.4, 3.0), "ADD")
    if kind == "knit":  # rows of stitches along the scarf
        return mix(wave("Z", 1.0, 2.0), wave("X", 2.0), "MULTIPLY")
    raise ValueError(kind)


def region_material(name):
    """Procedural Principled material for one region. The nodes labelled
    BASE / ROUGH / METAL hold the values bake_textures() bakes, and
    BAKE_TARGET is the image node a bake writes into."""
    spec = REGIONS[name]
    mat = bpy.data.materials.new(f"Region_{name}")
    mat.use_nodes = True
    nt = mat.node_tree
    n = nt.nodes
    bsdf = n["Principled BSDF"]

    base = n.new("ShaderNodeMix")
    base.data_type = "RGBA"
    base.blend_type = "MIX"
    base.label = "BASE"
    lin = srgb_to_linear(spec["base"])
    sock(base.inputs, "A_Color").default_value = (*lin, 1)
    rough = n.new("ShaderNodeMath")
    rough.operation = "MULTIPLY_ADD"
    rough.label = "ROUGH"
    metal = n.new("ShaderNodeValue")
    metal.label = "METAL"
    metal.outputs[0].default_value = spec["metal"]

    detail = spec["detail"]
    if detail:
        kind, scale, strength, var = detail
        h = _detail_nodes(nt, kind, scale)
        # Colour: blend towards a `var` darker base in the recesses (low h).
        ramp = n.new("ShaderNodeMapRange")
        ramp.inputs["To Min"].default_value = 1.0
        ramp.inputs["To Max"].default_value = 0.0
        nt.links.new(h, ramp.inputs["Value"])
        nt.links.new(ramp.outputs["Result"], sock(base.inputs, "Factor_Float"))
        sock(base.inputs, "B_Color").default_value = (*(c * (1 - var) for c in lin), 1)
        # Roughness: a little rougher in the recesses.
        nt.links.new(h, rough.inputs[0])
        rough.inputs[1].default_value = -0.08
        rough.inputs[2].default_value = spec["rough"] + 0.04
        bump = n.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = strength
        bump.inputs["Distance"].default_value = 0.006
        nt.links.new(h, bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    else:
        sock(base.inputs, "Factor_Float").default_value = 0.0
        rough.inputs[0].default_value = 0.0
        rough.inputs[2].default_value = spec["rough"]

    nt.links.new(sock(base.outputs, "Result_Color"), bsdf.inputs["Base Color"])
    nt.links.new(rough.outputs[0], bsdf.inputs["Roughness"])
    nt.links.new(metal.outputs[0], bsdf.inputs["Metallic"])
    target = n.new("ShaderNodeTexImage")
    target.label = "BAKE_TARGET"
    n.active = target
    return mat


def _labelled(mat, label):
    return next(nd for nd in mat.node_tree.nodes if nd.label == label)


def bake_textures(meshes):
    """Bake the region materials into the URP Lit texture set. Returns
    {stem: float32 HxWx4 array, linear}; save_textures() writes them."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.render.bake.margin = 8
    scene.render.bake.margin_type = "EXTEND"
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.light_settings.distance = 0.25  # AO reach, metres

    region_mats = [region_material(r) for r in REGION_ORDER]
    for m in meshes:
        m.data.materials.clear()  # also resets every face's material_index...
        for mat in region_mats:
            m.data.materials.append(mat)
        regions = np.zeros(len(m.data.polygons), dtype=np.int32)
        m.data.attributes["region"].data.foreach_get("value", regions)
        m.data.polygons.foreach_set("material_index", regions)  # ...so restore it

    bpy.ops.object.select_all(action="DESELECT")
    for m in meshes:
        m.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]

    def run(pass_name, bake_type, samples, wire=None):
        img = bpy.data.images.new(f"bake_{pass_name}", TEX_SIZE, TEX_SIZE, alpha=False, float_buffer=True)
        img.colorspace_settings.name = "Non-Color"
        saved = []
        for mat in region_mats:
            nt = mat.node_tree
            _labelled(mat, "BAKE_TARGET").image = img
            if wire:  # route one labelled value straight to an emission output
                out = nt.nodes["Material Output"]
                old = out.inputs["Surface"].links[0].from_socket
                emit = nt.nodes.new("ShaderNodeEmission")
                node = _labelled(mat, wire)
                out_sock = sock(node.outputs, "Result_Color") if node.bl_idname == "ShaderNodeMix" else node.outputs[0]
                nt.links.new(out_sock, emit.inputs["Color"])
                nt.links.new(emit.outputs[0], out.inputs["Surface"])
                saved.append((nt, out, old, emit))
        scene.cycles.samples = samples
        bpy.ops.object.bake(type=bake_type, normal_space="TANGENT", normal_r="POS_X",
                            normal_g="POS_Y", normal_b="POS_Z", use_clear=True)
        for nt, out, old, emit in saved:
            nt.links.new(old, out.inputs["Surface"])
            nt.nodes.remove(emit)
        px = np.array(img.pixels[:], dtype=np.float32).reshape(TEX_SIZE, TEX_SIZE, 4)
        bpy.data.images.remove(img)
        print(f"bake {pass_name}: min {px[..., :3].min(axis=(0, 1)).round(3)} max {px[..., :3].max(axis=(0, 1)).round(3)}")
        return px

    base = run("base", "EMIT", 1, wire="BASE")
    rough = run("rough", "EMIT", 1, wire="ROUGH")[..., 0]
    metal = run("metal", "EMIT", 1, wire="METAL")[..., 0]
    normal = run("normal", "NORMAL", 4)
    ao = run("ao", "AO", 64)[..., 0]

    mask = np.zeros((TEX_SIZE, TEX_SIZE, 4), dtype=np.float32)
    mask[..., 0] = metal
    mask[..., 1] = ao
    mask[..., 3] = 1.0 - rough  # smoothness
    base[..., 3] = 1.0
    normal[..., 3] = 1.0
    for mat in region_mats:
        bpy.data.materials.remove(mat)
    return {"T_Kid_BaseColor": base, "T_Kid_Normal": normal, "T_Kid_MaskMap": mask}


def linear_to_srgb(a):
    a = np.clip(a, 0.0, 1.0)
    return np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(a, 1 / 2.4) - 0.055)


def save_textures(maps):
    """Write 8-bit PNGs. Only BaseColor is stored as sRGB; the normal and mask
    maps hold data and must be imported as linear (Non-Color / sRGB off)."""
    os.makedirs(TEXTURE_DIR, exist_ok=True)
    paths = {}
    for stem, px in maps.items():
        px = px.copy()
        if stem == "T_Kid_BaseColor":
            px[..., :3] = linear_to_srgb(px[..., :3])
        has_alpha = stem == "T_Kid_MaskMap"
        img = bpy.data.images.new(stem, TEX_SIZE, TEX_SIZE, alpha=has_alpha)
        img.colorspace_settings.name = "Non-Color"  # write the values as given
        img.pixels.foreach_set(np.clip(px, 0, 1).ravel())
        path = os.path.join(TEXTURE_DIR, stem + ".png")
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        bpy.data.images.remove(img)
        paths[stem] = path
    return paths


def build_final_materials(meshes, texture_paths):
    """The materials the .blend keeps and the FBX names: Principled BSDF fed by
    the baked maps, wired the way URP Lit reads them, so the Blender render is
    a fair preview of Unity. Faces are reassigned from region slots to
    MATERIAL_SLOTS by their stored region."""
    imgs = {}
    for stem, path in texture_paths.items():
        img = bpy.data.images.load(path, check_existing=True)
        img.colorspace_settings.name = "sRGB" if stem == "T_Kid_BaseColor" else "Non-Color"
        img.filepath = bpy.path.relpath(path, start=os.path.dirname(BLEND_PATH))
        imgs[stem] = img

    def make(name, tint):
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        nt = mat.node_tree
        n = nt.nodes
        bsdf = n["Principled BSDF"]
        uv = n.new("ShaderNodeUVMap")
        uv.uv_map = "UVMap"

        def tex(stem):
            t = n.new("ShaderNodeTexImage")
            t.image = imgs[stem]
            t.label = stem
            nt.links.new(uv.outputs["UV"], t.inputs["Vector"])
            return t

        base = tex("T_Kid_BaseColor")
        col = base.outputs["Color"]
        if tint:  # stands in for URP's _BaseColor tint (the player colour)
            mul = n.new("ShaderNodeMix")
            mul.data_type = "RGBA"
            mul.blend_type = "MULTIPLY"
            mul.label = "PLAYER_TINT"
            sock(mul.inputs, "Factor_Float").default_value = 1.0
            sock(mul.inputs, "B_Color").default_value = (*srgb_to_linear(PREVIEW_PLAYER_COLOR), 1)
            nt.links.new(col, sock(mul.inputs, "A_Color"))
            col = sock(mul.outputs, "Result_Color")
        nt.links.new(col, bsdf.inputs["Base Color"])

        mask = tex("T_Kid_MaskMap")
        sep = n.new("ShaderNodeSeparateColor")
        nt.links.new(mask.outputs["Color"], sep.inputs["Color"])
        nt.links.new(sep.outputs["Red"], bsdf.inputs["Metallic"])
        inv = n.new("ShaderNodeMath")
        inv.operation = "SUBTRACT"
        inv.inputs[0].default_value = 1.0
        nt.links.new(mask.outputs["Alpha"], inv.inputs[1])  # smoothness -> roughness
        nt.links.new(inv.outputs[0], bsdf.inputs["Roughness"])

        nrm = tex("T_Kid_Normal")
        nmap = n.new("ShaderNodeNormalMap")
        nmap.uv_map = "UVMap"
        nt.links.new(nrm.outputs["Color"], nmap.inputs["Color"])
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        return mat

    mats = [make(MATERIAL_SLOTS[0], False), make(MATERIAL_SLOTS[1], True)]
    tint_ids = {REGION_ORDER.index(r) for r in TINT_REGIONS}
    for m in meshes:
        regions = np.zeros(len(m.data.polygons), dtype=np.int32)
        m.data.attributes["region"].data.foreach_get("value", regions)
        m.data.materials.clear()
        for mat in mats:
            m.data.materials.append(mat)
        m.data.polygons.foreach_set("material_index", [1 if r in tint_ids else 0 for r in regions])
    return mats


# --- Animation ---------------------------------------------------------------
# Poses are written as rotations about armature (rest) axes, relative to the
# parent: X = the kid's left/right axis (pitch), Y = back/forward axis, Z = up.
# to_local() converts them into each pose bone's own rest frame.
#
# Sign cheat sheet (the kid faces -Y):
#   leg/arm hanging down, rotate about X:  negative = swings forward
#   spine pointing up, rotate about X:     positive = leans forward
#   left arm (+X) about Y: +deg = down;    right arm: -deg = down
#   elbow (T-pose frame) about Z:          left -deg / right +deg = forearm forward

def rot(axis, deg):
    return Quaternion(Vector(axis), math.radians(deg))


def arm_down(side, deg):
    return rot(Y, deg if side == "Left" else -deg)


def elbow(side, deg):
    return rot(Z, -deg if side == "Left" else deg)


def pose_idle(t):
    p = 2 * math.pi * t
    breathe = math.sin(p)
    pose = {
        "Spine": rot(X, 1.0 * breathe),
        "Chest": rot(X, -1.5 * breathe),
        "Head": rot(X, 1.0 * math.sin(p + 0.6)) @ rot(Z, 2.0 * math.sin(p * 0.5 * 2)),
    }
    for side in ("Left", "Right"):
        pose[f"{side}UpperArm"] = arm_down(side, 74 + 1.5 * breathe) @ rot(X, -3)
        pose[f"{side}LowerArm"] = elbow(side, 12)
    return pose, Vector((0, 0, -0.004 * (1 - breathe)))


def _gait(t, thigh_amp, knee_amp, knee_base, arm_amp, elbow_deg, lean, bob, twist):
    p = 2 * math.pi * t
    pose = {
        "Spine": rot(X, lean * 0.5) @ rot(Z, twist * math.sin(p)),
        "Chest": rot(X, lean * 0.5) @ rot(Z, -twist * 0.6 * math.sin(p)),
        "Head": rot(X, -lean * 0.8),
    }
    for side, ph in (("Left", 0.0), ("Right", math.pi)):
        s, c = math.sin(p + ph), math.cos(p + ph)
        thigh = -thigh_amp * s
        knee = knee_base + knee_amp * max(0.0, c) ** 1.5
        pose[f"{side}UpperLeg"] = rot(X, thigh)
        pose[f"{side}LowerLeg"] = rot(X, knee)
        pose[f"{side}Foot"] = rot(X, -0.35 * knee - 0.3 * min(0.0, thigh))
        pose[f"{side}UpperArm"] = rot(X, arm_amp * s) @ arm_down(side, 76)
        pose[f"{side}LowerArm"] = elbow(side, elbow_deg + 0.3 * elbow_deg * max(0.0, -s))
    hips = Vector((0, 0, -bob * 0.5 * (1 + math.cos(2 * p))))
    return pose, hips


def pose_walk(t):
    return _gait(t, thigh_amp=24, knee_amp=38, knee_base=5, arm_amp=20, elbow_deg=14,
                 lean=3, bob=0.018, twist=5)


def pose_run(t):
    return _gait(t, thigh_amp=40, knee_amp=75, knee_base=18, arm_amp=38, elbow_deg=70,
                 lean=12, bob=0.035, twist=8)


def pose_grab(t):
    # Reach forward, hands at chest height, shoulder-width apart. Unity's
    # Humanoid IK lands the hands on the StationGrip targets from here.
    p = 2 * math.pi * t
    pose = {
        "Spine": rot(X, 6),
        "Chest": rot(X, 4 + 0.8 * math.sin(p)),
        "Head": rot(X, -6),
    }
    for side in ("Left", "Right"):
        # About Z, a forward-pointing arm swings toward the midline with a
        # negative angle on the left (+X) side and positive on the right.
        inward = rot(Z, -14 if side == "Left" else 14)
        pose[f"{side}UpperArm"] = inward @ rot(X, -68) @ arm_down(side, 78)
        pose[f"{side}LowerArm"] = elbow(side, 40)
    return pose, Vector((0, 0, 0))


# (action name, pose fn, frames per loop). Action names become the FBX take
# names; CharacterImportRules.cs renames them to A_Kid_<Name> clips.
CLIPS = [
    ("Idle", pose_idle, 48),
    ("Walk", pose_walk, 24),
    ("Run", pose_run, 16),
    ("Grab", pose_grab, 24),
]
# Clips whose hips height is solved from the feet, with the flight-arc height
# (metres) of each stride; the hand-written hip bob in _gait() is ignored for these.
GROUNDED = {"Walk": 0.0, "Run": 0.04}
FOOT_JOINTS = ["LeftFoot", "RightFoot", "LeftToes", "RightToes"]
FOOT_REST_Z = 0.025  # the toe joints' rest height; the lowest joint in the rest pose
# Loop speeds the controller blend tree is tuned for (metres / second).
WALK_SPEED, RUN_SPEED = 1.3, 3.4


def to_local(arm, bone_name, q_arm):
    rest = arm.data.bones[bone_name].matrix_local.to_quaternion()
    return rest.inverted() @ q_arm @ rest


def bake_clips(arm):
    if arm.animation_data is None:
        arm.animation_data_create()
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
    actions = []
    hips_rest = arm.data.bones["Hips"].matrix_local.to_quaternion()
    for name, fn, frames in CLIPS:
        act = bpy.data.actions.new(name)
        act.use_fake_user = True
        arm.animation_data.action = act
        flight = GROUNDED.get(name)
        for f in range(frames + 1):  # last frame == first, so the loop closes
            t = f / frames
            pose, hips_offset = fn(t)
            for pb in arm.pose.bones:
                pb.rotation_quaternion = to_local(arm, pb.name, pose[pb.name]) if pb.name in pose else Quaternion()
                pb.location = (0, 0, 0)
            if flight is not None:
                # Auto-ground: drop the hips until the lowest foot joint sits
                # at its rest height, then add the clip's flight arc.
                bpy.context.view_layer.update()
                low = min(arm.pose.bones[n].head.z for n in FOOT_JOINTS)
                hips_offset = Vector((0, 0, FOOT_REST_Z - low + flight * max(0.0, math.sin(4 * math.pi * t))))
            arm.pose.bones["Hips"].location = hips_rest.inverted() @ hips_offset
            for pb in arm.pose.bones:
                pb.keyframe_insert("rotation_quaternion", frame=f)
            arm.pose.bones["Hips"].keyframe_insert("location", frame=f)
        act.frame_range = (0, frames)
        act.use_frame_range = True
        actions.append(act)
    clear_pose(arm)
    return actions


# --- Validation --------------------------------------------------------------
def evaluated_bounds(objs):
    dg = bpy.context.evaluated_depsgraph_get()
    lo, hi = Vector((1e9,) * 3), Vector((-1e9,) * 3)
    for o in objs:
        eo = o.evaluated_get(dg)
        m = eo.to_mesh()
        for v in m.vertices:
            w = eo.matrix_world @ v.co
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
        eo.to_mesh_clear()
    return lo, hi


def pose_bone_heads(arm, action, frame, names):
    """Armature-space head positions of `names` at `frame` of `action`."""
    arm.animation_data.action = bpy.data.actions[action]
    bpy.context.scene.frame_set(frame)
    out = {n: arm.matrix_world @ arm.pose.bones[n].head for n in names}
    clear_pose(arm)
    return out


def clear_pose(arm):
    """Unassign the action AND reset the bones: unassigning alone leaves the
    last evaluated pose on them, which would be saved and exported as the
    model's default pose (Unity builds the avatar from it)."""
    arm.animation_data.action = None
    for pb in arm.pose.bones:
        pb.rotation_quaternion = Quaternion()
        pb.location = (0, 0, 0)
        pb.scale = (1, 1, 1)
    bpy.context.scene.frame_set(0)


def validate(arm, meshes, actions):
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    lo, hi = evaluated_bounds(meshes)
    height = hi.z - lo.z
    check("height", abs(height - TARGET_HEIGHT) < 0.04, f"{height:.3f} m (target {TARGET_HEIGHT})")
    check("feet_on_ground", abs(lo.z) < 0.01, f"lowest point z = {lo.z:.4f}")
    names = {b.name for b in arm.data.bones}
    missing = [n for n in HUMANOID_REQUIRED if n not in names]
    check("humanoid_bones", not missing, f"missing: {missing}" if missing else f"{len(names)} bones")
    tris = sum(sum(len(p.vertices) - 2 for p in m.data.polygons) for m in meshes)
    check("tri_budget", tris <= TRI_BUDGET, f"{tris} tris (budget {TRI_BUDGET})")
    for m in meshes:
        bad_sum, over4, unweighted = 0, 0, 0
        gnames = {g.index: g.name for g in m.vertex_groups}
        for v in m.data.vertices:
            ws = [g.weight for g in v.groups if g.weight > 0 and gnames[g.group] in names]
            if not ws:
                unweighted += 1
            elif abs(sum(ws) - 1) > 1e-3:
                bad_sum += 1
            if len(ws) > 4:
                over4 += 1
        check(f"weights_{m.name}", not (bad_sum or over4 or unweighted),
              f"unweighted={unweighted} sum!=1={bad_sum} >4 influences={over4}")
    check("clips", [a.name for a in actions] == [c[0] for c in CLIPS], [a.name for a in actions])

    # Grab: both hands in front of the chest, about shoulder width apart, so
    # Unity's hand IK only has to correct a few centimetres.
    hands = pose_bone_heads(arm, "Grab", 0, ["LeftHand", "RightHand"])
    lh, rh = hands["LeftHand"], hands["RightHand"]
    apart = (lh - rh).length
    check("grab_hands_forward", lh.y < -0.2 and rh.y < -0.2, f"wrist y L={lh.y:.3f} R={rh.y:.3f} (want < -0.2)")
    check("grab_hands_apart", 0.2 < apart < 0.45, f"{apart:.3f} m between wrists (want 0.2..0.45)")
    check("grab_hands_height", all(0.7 < h.z < 1.0 for h in (lh, rh)), f"wrist z L={lh.z:.3f} R={rh.z:.3f} (want 0.7..1.0)")
    check("grab_symmetric", abs(lh.x + rh.x) < 0.01 and abs(lh.y - rh.y) < 0.01, f"L={tuple(round(v, 3) for v in lh)} R={tuple(round(v, 3) for v in rh)}")

    # Walk / run: the lowest foot point should stay near the floor all cycle,
    # so the in-place clips don't float or sink.
    for clip in ("Walk", "Run"):
        frames = dict((c[0], c[2]) for c in CLIPS)[clip]
        lows = []
        for f in range(frames):
            pts = pose_bone_heads(arm, clip, f, ["LeftToes", "RightToes", "LeftFoot", "RightFoot"])
            lows.append(min(p.z for p in pts.values()))
        lo_z, hi_z = min(lows), max(lows)
        lift = GROUNDED[clip] + 0.005
        ok = lo_z > FOOT_REST_Z - 0.005 and hi_z < FOOT_REST_Z + lift
        check(f"{clip.lower()}_ground_contact", ok,
              f"lowest joint z {lo_z:.3f}..{hi_z:.3f} (want {FOOT_REST_Z - 0.005:.3f}..{FOOT_REST_Z + lift:.3f})")
    clear_pose(arm)
    posed = [pb.name for pb in arm.pose.bones
             if pb.rotation_quaternion.angle > 1e-6 or Vector(pb.location).length > 1e-6]
    check("rest_pose_clean", not posed and arm.animation_data.action is None,
          f"posed bones: {posed}" if posed else "armature at rest, no action assigned")
    return checks, {"height_m": round(height, 4), "tris": tris, "bounds_min": list(lo), "bounds_max": list(hi)}


def validate_surfaces(meshes, texture_paths, maps):
    """UV and material checks: atlas bounds, overlap, texel density, slots."""
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    size = TEX_SIZE // 2  # coverage raster; half-res keeps it quick
    cover = np.zeros((size, size), dtype=np.int16)
    lo_uv, hi_uv = np.array([1e9, 1e9]), np.array([-1e9, -1e9])
    island_uv, island_world = {}, {}
    for m in meshes:
        me = m.data
        uvs = np.zeros(len(me.loops) * 2, dtype=np.float32)
        me.uv_layers["UVMap"].data.foreach_get("uv", uvs)
        uvs = uvs.reshape(-1, 2)
        lo_uv, hi_uv = np.minimum(lo_uv, uvs.min(0)), np.maximum(hi_uv, uvs.max(0))
        islands = np.zeros(len(me.polygons), dtype=np.int32)
        me.attributes["island"].data.foreach_get("value", islands)
        me.calc_loop_triangles()
        for tri in me.loop_triangles:
            t = uvs[list(tri.loops)]
            uv_area = abs(np.cross(t[1] - t[0], t[2] - t[0])) / 2
            key = (m.name, int(islands[tri.polygon_index]))
            island_uv[key] = island_uv.get(key, 0.0) + uv_area
            island_world[key] = island_world.get(key, 0.0) + tri.area
            # Rasterise at pixel centres; count how many triangles cover each pixel.
            px = t * size
            x0, y0 = np.floor(px.min(0)).astype(int)
            x1, y1 = np.ceil(px.max(0)).astype(int)
            xs, ys = np.meshgrid(np.arange(max(x0, 0), min(x1, size)) + 0.5,
                                 np.arange(max(y0, 0), min(y1, size)) + 0.5)
            if xs.size == 0:
                continue
            (ax, ay), (bx, by), (cx, cy) = px
            d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(d) < 1e-12:
                continue
            w0 = ((by - cy) * (xs - cx) + (cx - bx) * (ys - cy)) / d
            w1 = ((cy - ay) * (xs - cx) + (ax - cx) * (ys - cy)) / d
            inside = (w0 > 0) & (w1 > 0) & (w0 + w1 < 1)
            cover[ys[inside].astype(int), xs[inside].astype(int)] += 1
    check("uv_in_atlas", lo_uv.min() >= 0 and hi_uv.max() <= 1,
          f"uv range {lo_uv.round(4).tolist()}..{hi_uv.round(4).tolist()}")
    overlap = int((cover > 1).sum())
    check("uv_no_overlap", overlap <= 4, f"{overlap} overlapping pixels at {size}px")
    used = float((cover > 0).mean())

    # Texel density per island in pixels per metre at TEX_SIZE. Tiny islands
    # (caps of a few mm2) are skipped: their density is all rounding.
    dens = {k: math.sqrt(island_uv[k] / island_world[k]) * TEX_SIZE
            for k in island_uv if island_world[k] > 2e-4}
    vals = sorted(dens.values())
    med = vals[len(vals) // 2]
    worst = max(dens, key=lambda k: abs(dens[k] / med - 1))
    spread = abs(dens[worst] / med - 1)
    check("texel_density", spread < 0.15,
          f"median {med:.0f} px/m, worst island {worst[0]}#{worst[1]} {dens[worst]:.0f} px/m ({spread:.0%} off)")

    for m in meshes:
        names = [mat.name for mat in m.data.materials]
        check(f"materials_{m.name}", names == MATERIAL_SLOTS, names)
    # The maps must carry the regions' signal, not just exist: a mis-wired
    # bake writes plausible-looking files of flat grey.
    base = maps["T_Kid_BaseColor"][..., :3]
    used_px = base.reshape(-1, 3)[base.reshape(-1, 3).max(1) > 0.01]
    chroma = float((used_px.max(1) - used_px.min(1)).max()) if len(used_px) else 0.0
    check("basecolor_has_colour", chroma > 0.3, f"max chroma {chroma:.2f} (want > 0.3)")
    metal_px = int((maps["T_Kid_MaskMap"][..., 0] > 0.5).sum())
    want_metal = any(r["metal"] > 0.5 for r in REGIONS.values())
    check("metallic_regions_baked", (metal_px > 50) == want_metal, f"{metal_px} metallic texels")
    nrm = maps["T_Kid_Normal"][..., :2]
    tilt = float(np.percentile(np.abs(nrm - 0.5), 99))
    check("normal_has_detail", tilt > 0.03, f"99th pct tangent tilt {tilt:.3f} (want > 0.03)")
    missing = [k for k in TEXTURES if not os.path.isfile(texture_paths.get(k, ""))]
    check("textures_written", not missing, f"missing: {missing}" if missing else f"{len(TEXTURES)} x {TEX_SIZE}px")
    numbers = {"texel_density_px_per_m": round(med), "uv_coverage": round(used, 3)}
    return checks, numbers


# --- Review panels -----------------------------------------------------------
# Every review image is a "panel" with a stable id (feedback is pinned to it
# across versions), a group, and the code that produced it. The panels and a
# manifest go to renders/review/kid_base/v<version>/; scripts/cli/review_board.py
# turns them into the review page.
PANEL = 360
REVIEW_DIR = os.path.join(REPO_ROOT, "renders", "review", "kid_base", f"v{KID_VERSION}")
THIS_FILE = os.path.relpath(os.path.abspath(__file__), REPO_ROOT)


def source_of(obj):
    """{file, line, symbol} for a function, or for a module-level name (found
    by its `NAME = ` assignment line)."""
    if callable(obj):
        return {"file": THIS_FILE, "line": obj.__code__.co_firstlineno, "symbol": obj.__name__ + "()"}
    with open(__file__) as f:
        for i, line in enumerate(f, 1):
            if line.startswith(f"{obj} = "):
                return {"file": THIS_FILE, "line": i, "symbol": obj}
    return {"file": THIS_FILE, "line": 1, "symbol": obj}


class Panels:
    def __init__(self):
        self.items = []
        os.makedirs(REVIEW_DIR, exist_ok=True)

    def path(self, pid):
        return os.path.join(REVIEW_DIR, pid + ".jpg")

    def add(self, pid, group, label, source, note=None):
        self.items.append({"id": pid, "group": group, "label": label,
                           "image": os.path.relpath(self.path(pid), REVIEW_DIR),
                           "source": source, **({"note": note} if note else {})})


def _vc_material(name, emission=False):
    """Review-only material showing the vertex colours (floor, pole, bone sticks)."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "Col"
    if emission:
        out = nt.nodes["Material Output"]
        emit = nt.nodes.new("ShaderNodeEmission")
        nt.links.new(attr.outputs["Color"], emit.inputs["Color"])
        nt.links.new(emit.outputs[0], out.inputs["Surface"])
    else:
        bsdf = nt.nodes["Principled BSDF"]
        nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
        bsdf.inputs["Roughness"].default_value = 0.8
    return mat


def setup_review_scene(arm, meshes):
    scene = bpy.context.scene
    scene.render.resolution_x = PANEL
    scene.render.resolution_y = int(PANEL * 1.25)
    scene.render.film_transparent = False
    scene.render.fps = FPS
    scene.render.image_settings.file_format = "JPEG"
    scene.render.image_settings.quality = 88
    # No tonemapping, to compare like for like with an untonemapped URP camera.
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.eevee.taa_render_samples = 32
    # Workbench (rig panels): vertex colours + outline, as in v0.1.
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "VERTEX"
    scene.display.shading.show_object_outline = True

    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (*srgb_to_linear((0.42, 0.45, 0.50)), 1)
    bg.inputs["Strength"].default_value = 0.6

    coll = bpy.data.collections.new("Review")
    scene.collection.children.link(coll)

    # Key, fill and rim lights in a classic three-point setup around the kid.
    for name, kind, energy, rot_deg, loc in (
        ("Key", "SUN", 3.2, (50, 0, -35), (0, 0, 0)),
        ("Rim", "SUN", 2.0, (60, 0, 160), (0, 0, 0)),
    ):
        ld = bpy.data.lights.new(name, kind)
        ld.energy = energy
        lo = bpy.data.objects.new(name, ld)
        lo.rotation_euler = [math.radians(v) for v in rot_deg]
        coll.objects.link(lo)

    # Floor grid (0.25 m squares) + a 1.3 m height pole for scale.
    floor = bpy.data.meshes.new("ReviewFloor")
    verts, faces, cols = [], [], []
    n, s = 8, 0.25
    for i in range(-n, n):
        for j in range(-n, n):
            b = len(verts)
            verts += [(i * s, j * s, 0), ((i + 1) * s, j * s, 0), ((i + 1) * s, (j + 1) * s, 0), (i * s, (j + 1) * s, 0)]
            faces.append((b, b + 1, b + 2, b + 3))
            cols.append(0.30 if (i + j) % 2 else 0.38)
    floor.from_pydata(verts, [], faces)
    attr = floor.color_attributes.new("Col", "FLOAT_COLOR", "CORNER")
    data = []
    for f, c in zip(floor.polygons, cols):
        data += [c, c, c * 1.03, 1.0] * len(f.loop_indices)
    attr.data.foreach_set("color", data)
    fo = bpy.data.objects.new("ReviewFloor", floor)
    coll.objects.link(fo)
    vc = _vc_material("ReviewVC")
    floor.materials.append(vc)

    pole = MeshBuilder()
    for k in range(13):
        z0, z1 = k * 0.1, (k + 1) * 0.1 if k < 12 else 1.30
        col = (0.9, 0.2, 0.2) if k % 2 else (0.95, 0.95, 0.95)
        pole.loft([(0.45, 0.25, z0), (0.45, 0.25, z1)], [X, X], [Y, Y], [(0.012, 0.012)] * 2,
                  [{}] * 2, solid(col), segments=6)
    po = pole.to_object("ReviewPole", coll)
    po.data.materials.clear()
    po.data.materials.append(vc)

    # Bone overlay: a thin stick per deform bone, parented to that bone so it
    # follows every pose. Workbench draws them in front; EEVEE panels hide them.
    stick_mat = _vc_material("ReviewStick", emission=True)
    sticks = []
    for bone in arm.data.bones:
        mb = MeshBuilder()
        length = bone.length
        mb.loft([(0, 0, 0), (0, length, 0)], [X, X], [Z, Z], [(0.006, 0.006), (0.003, 0.003)],
                [{}] * 2, solid((0.1, 0.9, 1.0) if not bone.name.startswith("Scarf") else (1.0, 0.85, 0.1)),
                segments=4)
        so = mb.to_object(f"Stick_{bone.name}", coll)
        so.data.materials.clear()
        so.data.materials.append(stick_mat)
        so.parent = arm
        so.parent_type = "BONE"
        so.parent_bone = bone.name
        # parent_bone places the child at the bone's tail; pull it back to the head.
        so.matrix_parent_inverse = Matrix.Translation((0, -length, 0))
        so.show_in_front = True
        sticks.append(so)

    cam_data = bpy.data.cameras.new("ReviewCam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = 1.75
    cam = bpy.data.objects.new("ReviewCam", cam_data)
    coll.objects.link(cam)
    scene.camera = cam
    return cam, sticks


def aim_camera(cam, yaw_deg, target=Vector((0, 0, 0.68)), dist=5.0, pitch_deg=8, ortho=1.75):
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    # yaw 0 = looking at the kid's face (camera on -Y).
    eye = target + Vector((math.sin(yaw) * math.cos(pitch), -math.cos(yaw) * math.cos(pitch), math.sin(pitch))) * dist
    cam.location = eye
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = (target - eye).to_track_quat("-Z", "Y")
    cam.data.ortho_scale = ortho


def render_panel(path, engine, sticks):
    scene = bpy.context.scene
    scene.render.engine = engine
    for so in sticks:
        so.hide_render = engine != "BLENDER_WORKBENCH"
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def load_png(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    return px


def save_jpg(px, path, size=512):
    """Save a display-referred float RGBA array (bottom row first) as JPEG."""
    h, w = px.shape[:2]
    if w != size:  # nearest-neighbour downscale is enough for a review image
        ys = (np.arange(size) * h / size).astype(int)
        xs = (np.arange(size) * w / size).astype(int)
        px = px[ys][:, xs]
    img = bpy.data.images.new(os.path.basename(path), px.shape[1], px.shape[0], alpha=False)
    out = np.clip(px, 0, 1).astype(np.float32)
    out[..., 3] = 1.0
    img.pixels.foreach_set(out.ravel())
    img.filepath_raw = path
    img.file_format = "JPEG"
    img.save(filepath=path, quality=88)
    bpy.data.images.remove(img)


def uv_layout_image(meshes, base_srgb, size=1024):
    """The packed UV wireframe drawn over the base colour atlas."""
    img = base_srgb.copy()
    img[..., :3] *= 0.55
    for m in meshes:
        uvs = np.zeros(len(m.data.loops) * 2, dtype=np.float32)
        m.data.uv_layers["UVMap"].data.foreach_get("uv", uvs)
        uvs = uvs.reshape(-1, 2)
        for poly in m.data.polygons:
            idx = list(range(poly.loop_start, poly.loop_start + poly.loop_total))
            for i0, i1 in zip(idx, idx[1:] + idx[:1]):
                a, b = uvs[i0] * (size - 1), uvs[i1] * (size - 1)
                n = int(max(abs(b - a)) + 1)
                t = np.linspace(0, 1, n)[:, None]
                pts = np.rint(a + (b - a) * t).astype(int)
                img[np.clip(pts[:, 1], 0, size - 1), np.clip(pts[:, 0], 0, size - 1), :3] = (1.0, 0.86, 0.35)
    return img


def checker_material():
    """UV checker (32 x 32 over the atlas) to judge stretching and texel density."""
    mat = bpy.data.materials.new("ReviewChecker")
    mat.use_nodes = True
    nt = mat.node_tree
    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.uv_map = "UVMap"
    chk = nt.nodes.new("ShaderNodeTexChecker")
    chk.inputs["Scale"].default_value = 32
    chk.inputs["Color1"].default_value = (0.9, 0.9, 0.9, 1)
    chk.inputs["Color2"].default_value = (0.08, 0.35, 0.55, 1)
    nt.links.new(uv.outputs["UV"], chk.inputs["Vector"])
    nt.links.new(chk.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    return mat


def render_review(arm, meshes, maps, cam, sticks):
    """Render every review panel; returns the Panels list."""
    P = Panels()
    EEVEE, WB = "BLENDER_EEVEE", "BLENDER_WORKBENCH"
    scene = bpy.context.scene

    # Look: the baked PBR materials, as Unity should show them.
    for yaw, pid, label in ((0, "front", "Front"), (90, "side", "Side (left)"),
                            (180, "back", "Back"), (35, "three_quarter", "Three-quarter")):
        aim_camera(cam, yaw)
        render_panel(P.path(f"look.{pid}"), EEVEE, sticks)
        P.add(f"look.{pid}", "Look", label, source_of(build_final_materials))
    aim_camera(cam, 20, target=Vector((0, 0, 1.08)), ortho=0.6)
    render_panel(P.path("look.face"), EEVEE, sticks)
    P.add("look.face", "Look", "Face close-up", source_of(build_body))
    aim_camera(cam, 25, target=Vector((0, 0, 0.67)), ortho=0.4, pitch_deg=4)
    render_panel(P.path("look.belt"), EEVEE, sticks)
    P.add("look.belt", "Look", "Belt and buckle (brass, metallic)", source_of(build_body))

    # Rig: bone overlay on vertex colours.
    for yaw, pid, label in ((0, "front", "Rest pose, front"), (90, "side", "Rest pose, side")):
        aim_camera(cam, yaw)
        render_panel(P.path(f"rig.{pid}"), WB, sticks)
        P.add(f"rig.{pid}", "Rig", label, source_of("BONES"))

    # Clips.
    for name, fn, frames in CLIPS:
        arm.animation_data.action = bpy.data.actions[name]
        yaw = 90 if name in ("Walk", "Run") else 35
        for ph in (0.0, 0.25, 0.5, 0.75):
            f = int(round(ph * frames))
            scene.frame_set(f)
            aim_camera(cam, yaw)
            pid = f"{name.lower()}.f{f:02d}"
            render_panel(P.path(pid), EEVEE, sticks)
            P.add(pid, name, f"Frame {f} of {frames} ({f / FPS:.2f} s)", source_of(fn))
    clear_pose(arm)

    # Textures, exactly as written (display-referred previews).
    base = maps["T_Kid_BaseColor"].copy()
    base[..., :3] = linear_to_srgb(base[..., :3])
    save_jpg(base, P.path("tex.basecolor"))
    P.add("tex.basecolor", "Textures", "T_Kid_BaseColor (sRGB)", source_of("REGIONS"))
    save_jpg(maps["T_Kid_Normal"], P.path("tex.normal"))
    P.add("tex.normal", "Textures", "T_Kid_Normal (tangent, OpenGL)", source_of(_detail_nodes))
    mask = maps["T_Kid_MaskMap"]
    for ch, pid, label in ((0, "metallic", "MaskMap R: metallic"), (1, "ao", "MaskMap G: ambient occlusion"),
                           (3, "smoothness", "MaskMap A: smoothness")):
        grey = np.repeat(mask[..., ch:ch + 1], 4, axis=2)
        save_jpg(grey, P.path(f"tex.{pid}"))
        P.add(f"tex.{pid}", "Textures", label, source_of(bake_textures))
    save_jpg(uv_layout_image(meshes, base), P.path("uv.layout"), size=768)
    P.add("uv.layout", "UVs", "Packed UV layout over base colour", source_of(pack_uvs))

    # UV checker on the model.
    chk = checker_material()
    saved = {m.name: list(m.data.materials) for m in meshes}
    for m in meshes:
        for i in range(len(m.data.materials)):
            m.data.materials[i] = chk
    for yaw, pid, label in ((0, "front", "Checker, front"), (35, "three_quarter", "Checker, three-quarter")):
        aim_camera(cam, yaw)
        render_panel(P.path(f"uv.checker_{pid}"), EEVEE, sticks)
        P.add(f"uv.checker_{pid}", "UVs", label, source_of(MeshBuilder.loft))
    for m in meshes:
        for i, mat in enumerate(saved[m.name]):
            m.data.materials[i] = mat
    return P


# Tiny 3x5 bitmap font so panel labels need no external tools.
_FONT = {
    "A": "010101111101101", "B": "110101110101110", "C": "011100100100011", "D": "110101101101110",
    "E": "111100110100111", "F": "111100110100100", "G": "011100101101011", "H": "101101111101101",
    "I": "111010010010111", "J": "001001001101010", "K": "101101110101101", "L": "100100100100111",
    "M": "101111111101101", "N": "110101101101101", "O": "010101101101010", "P": "110101110100100",
    "Q": "010101101110011", "R": "110101110101101", "S": "011100010001110", "T": "111010010010010",
    "U": "101101101101111", "V": "101101101101010", "W": "101101111111101", "X": "101101010101101",
    "Y": "101101010010010", "Z": "111001010100111", "0": "111101101101111", "1": "010110010010111",
    "2": "110001010100111", "3": "110001010001110", "4": "101101111001001", "5": "111100110001110",
    "6": "011100111101111", "7": "111001010010010", "8": "111101111101111", "9": "111101111001110",
    ".": "000000000000010", "_": "000000000000111", "-": "000000111000000", ":": "000010000010000",
    "/": "001001010100100", "(": "010100100100010", ")": "010001001001010", " ": "000000000000000",
    "=": "000111000111000",
}


def draw_text(img, text, x, y, scale=2, color=(1, 1, 1)):
    """Draw text with its top-left at (x, y) in image-top coordinates."""
    h = img.shape[0]
    for ch in text.upper():
        bits = _FONT.get(ch, _FONT[" "])
        for r in range(5):
            for c in range(3):
                if bits[r * 3 + c] == "1":
                    y0 = h - (y + (r + 1) * scale)
                    img[y0:y0 + scale, x + c * scale:x + (c + 1) * scale, :3] = color
        x += 4 * scale


def build_sheet(panels):
    """One PNG contact sheet of the Look row and the clip rows, for git
    history and quick diffs; the review page is the main way to look."""
    rows = [[p for p in panels.items if p["group"] == g] for g in ["Look"] + [c[0] for c in CLIPS]]
    rows = [r[:4] for r in rows if r]
    imgs = [[(load_png(os.path.join(REVIEW_DIR, p["image"])), f'{p["id"]}  {p["source"]["symbol"]}') for p in row]
            for row in rows]
    ph, pw = imgs[0][0][0].shape[:2]
    label_h = 18
    n_rows, n_cols = len(imgs), max(len(r) for r in imgs)
    sheet = np.ones((n_rows * (ph + label_h), n_cols * pw, 4), dtype=np.float32) * 0.12
    for ri, row in enumerate(imgs):
        for ci, (px, lab) in enumerate(row):
            y_top = ri * (ph + label_h)
            y0 = sheet.shape[0] - (y_top + label_h + ph)
            sheet[y0:y0 + ph, ci * pw:(ci + 1) * pw] = px
            draw_text(sheet, lab, ci * pw + 4, y_top + 3)
    sheet[..., 3] = 1.0

    out = RENDER_STEM + "_sheet.png"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img = bpy.data.images.new("Sheet", sheet.shape[1], sheet.shape[0], alpha=True)
    img.pixels.foreach_set(sheet.ravel())
    img.filepath_raw = out
    img.file_format = "PNG"
    img.save()
    return out


# --- Export ------------------------------------------------------------------
def export_fbx(arm, meshes):
    os.makedirs(os.path.dirname(FBX_PATH), exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    for o in [arm, *meshes]:
        o.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.export_scene.fbx(
        filepath=FBX_PATH,
        use_selection=True,
        object_types={"ARMATURE", "MESH"},
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_ALL",
        axis_forward="-Z",
        axis_up="Y",
        bake_space_transform=False,  # breaks armatures; Unity's bakeAxisConversion handles it
        use_mesh_modifiers=False,  # the Armature modifier must not be applied
        mesh_smooth_type="FACE",
        colors_type="LINEAR",
        add_leaf_bones=False,
        primary_bone_axis="Y",
        secondary_bone_axis="X",
        use_armature_deform_only=True,
        armature_nodetype="NULL",
        bake_anim=True,
        bake_anim_use_all_bones=True,
        bake_anim_use_nla_strips=False,
        bake_anim_use_all_actions=True,
        bake_anim_force_startend_keying=True,
        bake_anim_step=1.0,
        bake_anim_simplify_factor=0.0,
        path_mode="STRIP",
        embed_textures=False,
        use_metadata=False,  # keeps the file byte-stable across rebuilds
    )


def git_state():
    import subprocess
    try:
        sha = subprocess.check_output(["git", "-C", REPO_ROOT, "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "-C", REPO_ROOT, "status", "--porcelain", "--", THIS_FILE], text=True).strip())
        return {"sha": sha, "dirty": dirty}
    except Exception:
        return {"sha": None, "dirty": True}


def main():
    import datetime
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps = FPS
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0

    coll = bpy.data.collections.new("Kid")
    scene.collection.children.link(coll)
    arm = build_armature(coll)
    body = build_body(coll)
    scarf = build_scarf(coll)
    meshes = [body, scarf]
    for m in meshes:
        skin(m, arm)

    pack_uvs(meshes)
    maps = bake_textures(meshes)  # rest pose, before any action exists
    texture_paths = save_textures(maps)
    build_final_materials(meshes, texture_paths)
    actions = bake_clips(arm)

    checks, numbers = validate(arm, meshes, actions)
    s_checks, s_numbers = validate_surfaces(meshes, texture_paths, maps)
    checks += s_checks
    numbers.update(s_numbers)
    report = {
        "version": KID_VERSION,
        "fbx": os.path.relpath(FBX_PATH, REPO_ROOT),
        "textures": {k: os.path.relpath(v, REPO_ROOT) for k, v in texture_paths.items()},
        "numbers": numbers,
        "checks": checks,
        "clips": {name: {"frames": frames, "seconds": frames / FPS} for name, _, frames in CLIPS},
        "speeds": {"walk": WALK_SPEED, "run": RUN_SPEED},
    }

    os.makedirs(os.path.dirname(BLEND_PATH), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH, relative_remap=True)

    # Review scene objects are added after saving so the .blend stays clean.
    cam, sticks = setup_review_scene(arm, meshes)
    panels = render_review(arm, meshes, maps, cam, sticks)
    report["sheet"] = os.path.relpath(build_sheet(panels), REPO_ROOT)

    failed = [c for c in checks if not c["ok"]]
    if not failed:
        # Reload the clean file so the review objects never reach the FBX.
        bpy.ops.wm.open_mainfile(filepath=BLEND_PATH)
        arm = bpy.data.objects["Rig"]
        export_fbx(arm, [bpy.data.objects["Body"], bpy.data.objects["Scarf"]])
    report["exported"] = not failed

    with open(RENDER_STEM + "_report.json", "w") as f:
        json.dump(report, f, indent=2, default=str)
        f.write("\n")

    manifest = {
        "asset": "kid_base",
        "version": KID_VERSION,
        "stage": "blender",
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "git": git_state(),
        "builder": THIS_FILE,
        "panels": panels.items,
        "checks": checks,
        "numbers": numbers,
        "exported": report["exported"],
    }
    with open(os.path.join(REVIEW_DIR, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, default=str)
        f.write("\n")

    for c in checks:
        print(f"[{'PASS' if c['ok'] else 'FAIL'}] {c['check']}: {c['detail']}")
    print(f"Sheet:  {report['sheet']}")
    print(f"Review: {os.path.relpath(REVIEW_DIR, REPO_ROOT)} ({len(panels.items)} panels)")
    if failed:
        print("NOT exported - fix the failing checks above.")
        sys.exit(1)
    print(f"Exported {report['fbx']}")


if __name__ == "__main__":
    main()
