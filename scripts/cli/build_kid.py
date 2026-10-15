"""Build the base crew kid - blockout body, Humanoid rig, skin weights, scarf
bones and the idle / walk / run / grab clips - headlessly, and export it into
the com.tincan.characters package.

This is phases 1, 2 and 5 of the character pipeline plan in one reproducible
script: the slice proves the pipeline, not the art, so the body is a vertex-
coloured blockout built from lofted tubes. Everything is generated here, so
the same source always produces the same FBX (no Mixamo round trip).

Coordinates are Blender's (Z up, metres). The kid faces -Y, so their left
hand is +X; with axis_forward="-Z", axis_up="Y" that arrives in Unity facing
+Z with the left hand at -X, which is what a Unity Humanoid expects.

Vertex colours carry the look (the TinCan/Toon shader reads them):
    RGB = linear base colour, A = player-colour mask (1 = tinted by _PlayerColor)

Run headless, no GUI or MCP connection needed:
    blender --background --python scripts/cli/build_kid.py

Outputs (paths relative to the repo root):
    blend/characters/kid_base.blend                       - editable source
    packages/com.tincan.characters/Runtime/Models/SK_KidBase.fbx
    renders/kid_base_v<version>_sheet.png                 - review contact sheet
    renders/kid_base_v<version>_report.json               - checks + numbers

The script refuses to export when a check in validate() fails; the report
says which one.
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
KID_VERSION = "0.1.0"
RENDER_STEM = os.path.join(REPO_ROOT, "renders", f"kid_base_v{KID_VERSION}")

FPS = 24
TARGET_HEIGHT = 1.30
TRI_BUDGET = 6000

# --- Palette (sRGB, converted to linear when written) ------------------------
SKIN = (0.93, 0.74, 0.60)
HAIR = (0.30, 0.19, 0.12)
SHIRT = (0.90, 0.86, 0.76)
VEST = (0.85, 0.85, 0.85)  # tinted by _PlayerColor at runtime (mask = 1)
TROUSERS = (0.30, 0.33, 0.40)
BOOTS = (0.36, 0.23, 0.14)
EYES = (0.08, 0.06, 0.06)
SCARF = (0.78, 0.22, 0.16)


def srgb_to_linear(c):
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


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
class MeshBuilder:
    """Accumulates lofted tubes into one mesh with per-face colours and
    per-vertex bone weights."""

    def __init__(self):
        self.verts, self.faces, self.face_colors, self.weights = [], [], [], []

    def _vert(self, p, w):
        self.verts.append(tuple(p))
        self.weights.append(w)
        return len(self.verts) - 1

    def loft(self, centers, axes_u, axes_v, radii, weights, color_fn, segments=12,
             cap_start=None, cap_end=None):
        """Tube through `centers` with an elliptical ring per centre.

        axes_u/axes_v: per-ring unit vectors spanning the ring plane.
        radii: per-ring (ru, rv). weights: per-ring {bone: w}.
        color_fn(face_centre) -> ((r, g, b) sRGB, mask).
        cap_start/cap_end: None for a flat cap at the ring centre, or a point
        (a pole) to close the tube to.
        """
        rings = []
        for c, u, v, (ru, rv), w in zip(centers, axes_u, axes_v, radii, weights):
            c, u, v = Vector(c), Vector(u), Vector(v)
            ring = []
            for s in range(segments):
                a = 2 * math.pi * s / segments
                ring.append(self._vert(c + u * (ru * math.cos(a)) + v * (rv * math.sin(a)), w))
            rings.append(ring)
        for r0, r1 in zip(rings, rings[1:]):
            for s in range(segments):
                s1 = (s + 1) % segments
                self._face([r0[s], r0[s1], r1[s1], r1[s]], color_fn)
        start = cap_start if cap_start is not None else centers[0]
        end = cap_end if cap_end is not None else centers[-1]
        p0 = self._vert(start, weights[0])
        p1 = self._vert(end, weights[-1])
        for s in range(segments):
            s1 = (s + 1) % segments
            self._face([p0, rings[0][s1], rings[0][s]], color_fn)
            self._face([p1, rings[-1][s], rings[-1][s1]], color_fn)

    def _face(self, idx, color_fn):
        centre = sum((Vector(self.verts[i]) for i in idx), Vector()) / len(idx)
        self.faces.append(idx)
        self.face_colors.append(color_fn(centre))

    def to_object(self, name, collection):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(self.verts, [], self.faces)
        mesh.validate()
        # Faces were wound by hand; make every normal point out of its tube.
        import bmesh
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(mesh)
        bm.free()
        attr = mesh.color_attributes.new("Col", "FLOAT_COLOR", "CORNER")
        cols = np.zeros((len(mesh.loops), 4), dtype=np.float32)
        for poly in mesh.polygons:
            rgb, mask = self.face_colors[poly.index]
            lin = srgb_to_linear(rgb)
            for li in poly.loop_indices:
                cols[li] = (*lin, mask)
        attr.data.foreach_set("color", cols.ravel())
        mesh.color_attributes.active_color = attr
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


def solid(rgb, mask=0.0):
    return lambda _c: (rgb, mask)


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
            return (TROUSERS, 0.0)
        if c.z < 0.685:  # belt
            return (BOOTS, 0.0)
        return (VEST, 1.0)

    mb.loft([(0, 0, z) for z, _, _ in prof], [X] * len(prof), [Y] * len(prof),
            [(w, d) for _, w, d in prof], [torso_w(z) for z, _, _ in prof], torso_col, segments=16)

    # Neck.
    nz = [0.96, 1.0, 1.05]
    mb.loft([(0, 0.005, z) for z in nz], [X] * 3, [Y] * 3, [(0.038, 0.036)] * 3,
            [blend("Chest", "Neck", ramp(z, 0.97, 1.0)) for z in nz], solid(SKIN), segments=10)

    # Head: an ellipsoid with a hair cap. Big head, kid proportions (~4.5 heads).
    hc, hr = Vector((0, -0.005, 1.165)), Vector((0.128, 0.135, 0.142))
    n_rings = 12
    thetas = [math.pi * (i + 0.5) / n_rings for i in range(n_rings)]
    centers = [hc + Z * (-hr.z * math.cos(t)) for t in thetas]
    radii = [(hr.x * math.sin(t), hr.y * math.sin(t)) for t in thetas]

    def head_col(c):
        rel = c - hc
        if rel.z > 0.045 or (rel.y > 0.02 and rel.z > -0.06) or (rel.y > -0.07 and rel.z > 0.085):
            return (HAIR, 0.0)
        return (SKIN, 0.0)

    mb.loft(centers, [X] * n_rings, [Y] * n_rings, radii, [{"Head": 1.0}] * n_rings, head_col,
            segments=16, cap_start=hc - Z * hr.z, cap_end=hc + Z * hr.z)

    # Eyes: small dark ovals sitting on the face.
    for sx in (1, -1):
        ec = Vector((0.045 * sx, -0.122, 1.15))
        ez = [ec.z - 0.022, ec.z, ec.z + 0.022]
        mb.loft([(ec.x, ec.y, z) for z in ez], [X] * 3, [Y] * 3,
                [(0.012, 0.012), (0.018, 0.016), (0.012, 0.012)], [{"Head": 1.0}] * 3, solid(EYES),
                segments=8)

    # Ears.
    for sx in (1, -1):
        ex = [0.118 * sx, 0.142 * sx]
        mb.loft([(x, 0.0, 1.15) for x in ex], [Y, Y], [Z, Z], [(0.02, 0.03), (0.012, 0.022)],
                [{"Head": 1.0}] * 2, solid(SKIN), segments=8)

    for side, sx in (("Left", 1), ("Right", -1)):
        # Arm: shoulder to wrist along +-X. Rolled sleeves, bare forearms.
        ax = [0.09, 0.14, 0.20, 0.27, 0.33, 0.37, 0.43, 0.49, 0.54]
        ar = [0.05, 0.047, 0.045, 0.043, 0.041, 0.039, 0.037, 0.034, 0.031]

        def arm_w(x, side=side):
            if x < 0.25:
                return blend(f"{side}Shoulder", f"{side}UpperArm", ramp(x, 0.12, 0.20))
            return blend(f"{side}UpperArm", f"{side}LowerArm", ramp(x, 0.32, 0.38))

        def arm_col(c):
            return (SHIRT if abs(c.x) < 0.42 else SKIN, 0.0)

        mb.loft([(x * sx, 0, 0.93) for x in ax], [Y] * len(ax), [Z] * len(ax),
                [(r, r) for r in ar], [arm_w(x) for x in ax], arm_col, segments=10)

        # Hand: a flat mitten.
        hx = [0.53, 0.57, 0.61, 0.645]
        mb.loft([(x * sx, -0.005, 0.925) for x in hx], [Y] * 4, [Z] * 4,
                [(0.03, 0.022), (0.036, 0.024), (0.034, 0.022), (0.02, 0.014)],
                [{f"{side}Hand": 1.0}] * 4, solid(SKIN), segments=10)

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
            return (TROUSERS if c.z > 0.19 else BOOTS, 0.0)

        mb.loft([(0.075 * sx, 0, z) for z in lz], [X] * len(lz), [Y] * len(lz),
                [(r, r * 0.95) for r in lr], [leg_w(z) for z in lz], leg_col, segments=12)

        # Boot: heel to toe along -Y, flat-ish sole at z = 0.
        fy = [0.05, 0.02, -0.03, -0.08, -0.12, -0.15]
        fr = [(0.042, 0.04), (0.048, 0.045), (0.05, 0.042), (0.05, 0.036), (0.045, 0.03), (0.03, 0.02)]

        def foot_w(y, side=side):
            return blend(f"{side}Foot", f"{side}Toes", ramp(-y, 0.06, 0.10))

        mb.loft([(0.075 * sx, y, r[1] + 0.001) for y, r in zip(fy, fr)], [X] * 6, [Z] * 6, fr,
                [foot_w(y) for y in fy], solid(BOOTS), segments=10)

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
            [blend("Chest", "Neck", 0.35)] * len(angles), solid(SCARF), segments=8)

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
    mb.loft(centers, us, vs, rad, wts, solid(SCARF), segments=8)
    return mb.to_object("Scarf", collection)


def skin(obj, arm):
    obj.parent = arm
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm


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


# --- Review contact sheet ----------------------------------------------------
PANEL = 360


def setup_review_scene(arm, meshes):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "VERTEX"
    scene.display.shading.show_object_outline = True
    scene.display.shading.show_cavity = False
    scene.render.resolution_x = PANEL
    scene.render.resolution_y = int(PANEL * 1.25)
    scene.render.film_transparent = False
    scene.world = scene.world or bpy.data.worlds.new("World")
    scene.render.fps = FPS

    coll = bpy.data.collections.new("Review")
    scene.collection.children.link(coll)

    # Floor grid (0.25 m squares) + a 1.3 m height pole for scale.
    floor = bpy.data.meshes.new("ReviewFloor")
    verts, faces, cols = [], [], []
    n, s = 8, 0.25
    for i in range(-n, n):
        for j in range(-n, n):
            b = len(verts)
            verts += [(i * s, j * s, 0), ((i + 1) * s, j * s, 0), ((i + 1) * s, (j + 1) * s, 0), (i * s, (j + 1) * s, 0)]
            faces.append((b, b + 1, b + 2, b + 3))
            cols.append(0.55 if (i + j) % 2 else 0.65)
    floor.from_pydata(verts, [], faces)
    attr = floor.color_attributes.new("Col", "FLOAT_COLOR", "CORNER")
    data = []
    for f, c in zip(floor.polygons, cols):
        data += [c, c, c * 1.02, 1.0] * len(f.loop_indices)
    attr.data.foreach_set("color", data)
    fo = bpy.data.objects.new("ReviewFloor", floor)
    coll.objects.link(fo)

    pole = MeshBuilder()
    for k in range(13):
        z0, z1 = k * 0.1, (k + 1) * 0.1 if k < 12 else 1.30
        col = (0.9, 0.2, 0.2) if k % 2 else (0.95, 0.95, 0.95)
        pole.loft([(0.45, 0.25, z0), (0.45, 0.25, z1)], [X, X], [Y, Y], [(0.012, 0.012)] * 2,
                  [{}] * 2, solid(col), segments=6)
    po = pole.to_object("ReviewPole", coll)
    po.data.color_attributes.active_color = po.data.color_attributes["Col"]

    # Bone overlay: a thin stick per deform bone, parented to that bone so it
    # follows every pose (a renderable stand-in for the armature display).
    sticks = []
    for b in arm.data.bones:
        mb = MeshBuilder()
        length = b.length
        mb.loft([(0, 0, 0), (0, length, 0)], [X, X], [Z, Z], [(0.006, 0.006), (0.003, 0.003)],
                [{}] * 2, solid((0.1, 0.9, 1.0) if not b.name.startswith("Scarf") else (1.0, 0.85, 0.1)),
                segments=4)
        so = mb.to_object(f"Stick_{b.name}", coll)
        so.parent = arm
        so.parent_type = "BONE"
        so.parent_bone = b.name
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


def aim_camera(cam, yaw_deg, target=Vector((0, 0, 0.68)), dist=5.0, pitch_deg=8):
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    # yaw 0 = looking at the kid's face (camera on -Y).
    eye = target + Vector((math.sin(yaw) * math.cos(pitch), -math.cos(yaw) * math.cos(pitch), math.sin(pitch))) * dist
    cam.location = eye
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = (target - eye).to_track_quat("-Z", "Y")


def render_panel(path):
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def load_png(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    return px


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


def build_sheet(arm, cam, tmp_dir):
    """Rows: rest pose turnaround, then each clip at a few phases."""
    panels = []  # rows of (path, label)

    rest_row = []
    for yaw, label in ((0, "REST FRONT"), (90, "REST SIDE (L)"), (180, "REST BACK"), (35, "REST 3/4")):
        aim_camera(cam, yaw)
        path = os.path.join(tmp_dir, f"rest_{yaw}.png")
        render_panel(path)
        rest_row.append((path, f"{label}  BONES()"))
    panels.append(rest_row)

    for name, fn, frames in CLIPS:
        act = bpy.data.actions[name]
        arm.animation_data.action = act
        row = []
        phases = [0.0, 0.25, 0.5, 0.75]
        yaw = 90 if name in ("Walk", "Run") else 35
        for ph in phases:
            f = int(round(ph * frames))
            bpy.context.scene.frame_set(f)
            aim_camera(cam, yaw)
            path = os.path.join(tmp_dir, f"{name}_{f}.png")
            render_panel(path)
            row.append((path, f"{name} F{f}/{frames}  POSE_{name}()"))
        panels.append(row)
    clear_pose(arm)

    imgs = [[(load_png(p), lab) for p, lab in row] for row in panels]
    ph, pw = imgs[0][0][0].shape[:2]
    label_h = 18
    rows, cols = len(imgs), max(len(r) for r in imgs)
    sheet = np.ones((rows * (ph + label_h), cols * pw, 4), dtype=np.float32) * 0.12
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


def main():
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
    for m in (body, scarf):
        skin(m, arm)
    actions = bake_clips(arm)

    checks, numbers = validate(arm, [body, scarf], actions)
    report = {
        "version": KID_VERSION,
        "fbx": os.path.relpath(FBX_PATH, REPO_ROOT),
        "numbers": numbers,
        "checks": checks,
        "clips": {name: {"frames": frames, "seconds": frames / FPS} for name, _, frames in CLIPS},
        "speeds": {"walk": WALK_SPEED, "run": RUN_SPEED},
    }

    os.makedirs(os.path.dirname(BLEND_PATH), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)

    # Review scene objects are added after saving so the .blend stays clean.
    import tempfile
    cam, _ = setup_review_scene(arm, [body, scarf])
    with tempfile.TemporaryDirectory() as tmp:
        report["sheet"] = os.path.relpath(build_sheet(arm, cam, tmp), REPO_ROOT)

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

    for c in checks:
        print(f"[{'PASS' if c['ok'] else 'FAIL'}] {c['check']}: {c['detail']}")
    print(f"Sheet:  {report['sheet']}")
    if failed:
        print("NOT exported - fix the failing checks above.")
        sys.exit(1)
    print(f"Exported {report['fbx']}")


if __name__ == "__main__":
    main()
