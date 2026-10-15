"""Check ship parts in a .blend against the part contract (docs/part-contract.md).

A "part" is any top-level mesh object carrying a `part_size` custom property.
Its children named Socket_* / COL_Box_* / COL_Ramp_* are its sockets and
collider hints. Everything is checked in the part's own local space,
converted to Unity axes (see to_unity) so the numbers line up with the docs
and the manifest.

Run headless against a saved kit:
    blender --background blend/airship/hull_kit.blend --python scripts/cli/validate_part.py

Exits non-zero if any part fails. Build scripts import validate_parts()
directly and refuse to export on failure.
"""

import json
import math
import re
import sys

import bpy
from mathutils import Vector

EPS = 0.001  # 1 mm - tolerance for "on the cell boundary"

# Triangle budgets (see docs/part-contract.md, "Budget").
TRIS_PER_CELL = {"structure": 200, "detail": 1500}
TRIS_MAX = {"structure": 1000, "detail": 1500}

SHARED_MATERIAL = "M_HullAtlas"
# Blender forces object names to be unique across the file, so the second
# part's COL_Box_01 becomes COL_Box_01.001. That suffix is allowed here and
# stripped again on export (see canonical_name).
SOCKET_RE = re.compile(r"^Socket_[A-Z][A-Za-z]*_\d{2}(\.\d{3})?$")
COLLIDER_RE = re.compile(r"^COL_(Box|Ramp)_\d{2}(\.\d{3})?$")
MAX_RAMP_DEG = 45.0
FACE_KEYS = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")
NON_TILING_TAGS = {"open", "outboard", "inboard"}


def canonical_name(name):
    """Child name as it should appear in the FBX and manifest."""
    return re.sub(r"\.\d{3}$", "", name)


def to_unity(v):
    """Blender (Z-up, bow -Y) -> Unity (Y-up, bow +Z, left-handed).

    Matches the FBX export used by every build script (axis_forward=-Z,
    axis_up=Y) followed by Unity's own right->left-handed X flip.
    """
    return Vector((-v.x, v.z, -v.y))


def to_blender(v):
    return Vector((-v[0], -v[2], v[1]))


def axis_range(size):
    """Cell indices along one axis for a footprint `size` cells wide.

    Origin cell is the centre cell for odd sizes, and the port/aft-of-centre
    cell (the lower index) for even sizes.
    """
    lo = -((size - 1) // 2)
    return range(lo, lo + size)


def footprint_cells(obj):
    if "part_cells" in obj:
        return [tuple(c) for c in json.loads(obj["part_cells"])]
    sx, sy, sz = obj["part_size"]
    return [(i, j, k) for i in axis_range(sx) for j in range(sy) for k in axis_range(sz)]


def cell_bounds(cell):
    i, j, k = cell
    return (i - 0.5, j, k - 0.5), (i + 0.5, j + 1, k + 0.5)


def in_cell(p, cell):
    lo, hi = cell_bounds(cell)
    return all(lo[a] - EPS <= p[a] <= hi[a] + EPS for a in range(3))


def unity_verts(obj):
    return [to_unity(v.co) for v in obj.data.vertices]


def triangle_count(mesh):
    return sum(len(p.vertices) - 2 for p in mesh.polygons)


def check_cross_sections(verts, cells, faces, errors):
    """Opposite faces tagged with the same tiling tag must share a profile."""
    lo = [min(c[a] for c in cells) for a in range(3)]
    hi = [max(c[a] for c in cells) for a in range(3)]
    planes = {
        0: (lo[0] - 0.5, hi[0] + 0.5),
        1: (lo[1], hi[1] + 1),
        2: (lo[2] - 0.5, hi[2] + 0.5),
    }
    for axis, name in enumerate("XYZ"):
        neg, pos = faces.get("-" + name), faces.get("+" + name)
        if neg != pos or neg is None or neg in NON_TILING_TAGS:
            continue
        others = [a for a in range(3) if a != axis]

        def profile(plane):
            return {
                tuple(round(v[a], 4) for a in others)
                for v in verts
                if abs(v[axis] - plane) < EPS
            }

        p_neg, p_pos = profile(planes[axis][0]), profile(planes[axis][1])
        if not p_neg:
            errors.append(f"tiles along {name} ('{neg}') but has no vertices on its -{name} face")
        elif p_neg != p_pos:
            errors.append(
                f"tiles along {name} ('{neg}') but -{name}/+{name} cross-sections differ "
                f"({len(p_neg ^ p_pos)} unmatched points) - seams won't line up"
            )


def check_walkable(obj, verts, errors):
    mesh = obj.data
    tops = [
        max(to_unity(mesh.vertices[i].co).y for i in p.vertices)
        for p in mesh.polygons
        if to_unity(p.normal).y > 0.99
    ]
    if not tops:
        errors.append("marked walkable but has no upward-facing faces")
        return
    top = max(tops)
    if abs(top - round(top)) > EPS:
        errors.append(f"walkable top at y={top:.4f}, not on a cell top face")


def check_children(obj, cells, errors):
    sockets, colliders = [], []
    for child in obj.children:
        if child.type != "EMPTY":
            errors.append(f"child '{child.name}' is not an empty")
            continue
        local = child.matrix_local
        pos = to_unity(local.to_translation())
        if child.name.startswith("Socket_"):
            if not SOCKET_RE.match(child.name):
                errors.append(f"socket '{child.name}' doesn't match Socket_<Kind>_<NN>")
            if not any(in_cell(pos, c) for c in cells):
                errors.append(f"socket '{child.name}' at {tuple(round(x, 3) for x in pos)} is outside the footprint")
            sockets.append(child)
        elif child.name.startswith("COL_"):
            if not COLLIDER_RE.match(child.name):
                errors.append(f"collider '{child.name}' doesn't match COL_Box_<NN> / COL_Ramp_<NN>")
            d = child.empty_display_size
            corners = [
                to_unity(local @ Vector((sx * d, sy * d, sz * d)))
                for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)
            ] if child.empty_display_type == "CUBE" else [pos]
            if not all(any(in_cell(p, c) for c in cells) for p in corners):
                errors.append(f"collider '{child.name}' extends outside the footprint")
            if child.name.startswith("COL_Ramp_"):
                # A ramp is a box tilted about its local X; slope is the tilt.
                tilt = math.degrees(abs(local.to_euler().x))
                if tilt > MAX_RAMP_DEG + 0.01:
                    errors.append(f"ramp '{child.name}' is {tilt:.1f}° (max {MAX_RAMP_DEG}°)")
            colliders.append(child)
        else:
            errors.append(f"child '{child.name}' is neither Socket_* nor COL_*")
    return sockets, colliders


def validate_part(obj):
    errors = []
    mesh = obj.data

    if obj.parent is not None:
        errors.append("part must be a top-level object")
    if any(abs(r) > 1e-6 for r in obj.rotation_euler) or any(abs(s - 1) > 1e-6 for s in obj.scale):
        errors.append("rotation/scale not applied (must be identity)")

    cells = footprint_cells(obj)
    if (0, 0, 0) not in cells:
        errors.append("footprint doesn't include origin cell (0,0,0)")
    if any(c[1] < 0 for c in cells):
        errors.append("footprint has cells below the origin layer")

    verts = unity_verts(obj)
    outside = [v for v in verts if not any(in_cell(v, c) for c in cells)]
    if outside:
        v = outside[0]
        errors.append(
            f"{len(outside)} vertices outside footprint, e.g. ({v.x:.3f}, {v.y:.3f}, {v.z:.3f})"
        )

    category = obj.get("part_category", "structure")
    if category not in TRIS_PER_CELL:
        errors.append(f"unknown part_category '{category}'")
    else:
        tris = triangle_count(mesh)
        budget = min(TRIS_PER_CELL[category] * len(cells), TRIS_MAX[category])
        if tris > budget:
            errors.append(f"{tris} tris over the {category} budget of {budget}")

    mats = [m.name if m else None for m in mesh.materials]
    if mats != [SHARED_MATERIAL]:
        errors.append(f"materials {mats}, expected exactly ['{SHARED_MATERIAL}']")

    if not mesh.uv_layers:
        errors.append("no UV map")
    else:
        uvs = mesh.uv_layers.active.data
        if any(not (-EPS <= c <= 1 + EPS) for d in uvs for c in d.uv):
            errors.append("UVs outside 0..1 (atlas regions don't wrap)")

    faces = json.loads(obj.get("part_faces", "{}"))
    unknown = set(faces) - set(FACE_KEYS)
    if unknown:
        errors.append(f"part_faces has unknown keys {sorted(unknown)}")
    check_cross_sections(verts, cells, faces, errors)

    if obj.get("part_walkable", False):
        check_walkable(obj, verts, errors)

    check_children(obj, cells, errors)
    return errors


def find_parts(scene=None):
    scene = scene or bpy.context.scene
    return [o for o in scene.objects if o.type == "MESH" and "part_size" in o]


def validate_parts(parts=None):
    """Print a report; return True if every part passes."""
    parts = find_parts() if parts is None else parts
    bpy.context.view_layer.update()  # child matrix_local is stale until this runs
    ok = True
    for obj in parts:
        errors = validate_part(obj)
        status = "OK  " if not errors else "FAIL"
        print(f"[{status}] {obj.name}  ({triangle_count(obj.data)} tris, {len(footprint_cells(obj))} cells)")
        for e in errors:
            print(f"         - {e}")
        ok &= not errors
    if not parts:
        print("No parts found (mesh objects with a 'part_size' custom property).")
        ok = False
    return ok


if __name__ == "__main__":
    sys.exit(0 if validate_parts() else 1)
