# Ship Part Contract

What a Blender mesh has to satisfy to be a shipyard part. Every rule marked
**(checked)** is enforced by [`scripts/cli/validate_part.py`](../scripts/cli/validate_part.py);
build scripts refuse to export a part that fails. Rules marked
**(Unity: unconfirmed)** are this repo's assumptions about the shipyard code
in `tincan_unity`, listed in [unity-requirements.md](unity-requirements.md)
and still waiting for confirmation from that side.

Reference implementation: [`scripts/cli/build_hull_kit.py`](../scripts/cli/build_hull_kit.py)
(`Deck_1x1x1`, `HullSide_1x1x1`).

## Axes and units

Everything in this doc, the manifest and the build scripts is written in
**Unity part-local coordinates**:

| Meaning | Unity | Blender |
|---|---|---|
| Up | +Y | +Z |
| Bow (forward) | +Z | −Y |
| Starboard (right, facing the bow) | +X | −X |

1 unit = 1 m. The ship's bow points along **+Z** in Unity (the existing
placeholder hull has the bow crate at Z ≈ +21 and the motor at Z ≈ −11.5).
The FBX exporter setting `axis_forward="-Z", axis_up="Y"` is what produces
this mapping; it is *not* the direction the bow faces. Build scripts author
geometry in Unity coordinates and convert with `to_blender()`, so numbers
read the same everywhere.

FBX export uses `bake_space_transform=True`, so every node arrives in
Unity with identity rotation and scale 1. Import with the file's scale, no
generated colliders, no rig or animation.

## Grid, footprint and pivot

- A part occupies whole **1 m cells**. Cell `[i, j, k]` spans
  x `i−0.5..i+0.5`, y `j..j+1`, z `k−0.5..k+0.5` in part space.
- The **pivot** (the object origin) is the centre of the **bottom face** of the
  origin cell `[0, 0, 0]`. That is the same point as "cell centre +
  (0, −0.5, 0)"; the grid places a part at a cell centre and the
  shipyard's pivot offset (0, −0.5, 0) brings it down to the floor of the
  cell. **(Unity: unconfirmed)**
- **Origin cell rule** for a box footprint of `size = [sx, sy, sz]`: the bottom
  layer (`j = 0`), the centre column for odd widths, and the port/aft-of-centre
  column for even widths. A 3×2×5 footprint therefore spans
  `i ∈ −1..1, j ∈ 0..1, k ∈ −2..2`. Non-box footprints list their cells
  explicitly (`part_cells`). **(Unity: unconfirmed)**
- Rotation is in **90° yaw steps about the pivot**. **(Unity: unconfirmed)**
- Every vertex, socket and collider lies inside the footprint cells, with 1 mm
  of tolerance. **(checked)** Anything that sticks out, such as an overhang, a
  railing or a figurehead, needs its own cells in the footprint. That's why
  the helm grew to 3×2×5.
- Rotation and scale are applied (identity on the object). **(checked)**

## Floors, walls and edges

- **Walkable tops sit exactly on a cell's top face** (integer y). **(checked)**
  A deck part is a **slab at the top of its cell**: `Deck_1x1x1` is a 0.1 m
  plank slab (y 0.9–1.0) with a joist underneath. Players stand in the cell
  above. The underside of the slab is the ceiling of the room below.
- **One part per cell.** Edge-type parts (hull sides, walls) put their skin
  on the **inboard face** of their own cell, so they meet a floor tile in
  the neighbouring cell flush, with no gap and no shared cell. `HullSide_1x1x1`
  has its inner face at x = −0.5. Planking and battens stand out towards
  +X, inside the same cell. **(Unity: unconfirmed)**
- **Storey pitch is 3 cells.** One deck cell plus two standing cells gives
  about 2.8 m from floor to the underside of the next deck. Corridors are 2
  cells wide so two players can pass.
- **Stairs** rise 1 m per 1 m cell, exactly 45°. Their collider is a ramp.

## Seams and tiling

Each face of a part's footprint is tagged in `part_faces`. Tags: `deck`,
`hull` and other surface names for faces that tile. `open` means nothing
attaches. `inboard` and `outboard` mark the two sides of an edge part.

- If opposite faces carry the **same tiling tag**, their vertex cross-sections
  must be identical, so a row of parts has no steps or gaps. **(checked)**
  `HullSide_1x1x1` cuts its clinker strakes mid-strake at the top and bottom
  so the pieces stack without a visible step.
- Faces stay inside one cell along any textured axis. The atlas patterns
  repeat every 1 m, so a face crossing a cell boundary would double its
  texture.
- Faces are **flat-shaded at the seams**: export uses face smoothing, and
  normals never blend across part boundaries.
- Faces that only show when a part has no neighbour, like hull end caps,
  stay in the mesh. When parts are tiled they're hidden, not removed.

## Material, UVs and budget

- **One material for every hull part: `M_HullAtlas`.** **(checked)** Its base
  map is `HullAtlas_BaseColor.png`, 1024², in four 1 m × 1 m regions at
  about 480 px/m:

  | Region | Quadrant | Pattern |
  |---|---|---|
  | `deck` | top-left | 0.2 m planks running fore-aft, staggered butt joints |
  | `hull` | top-right | 0.25 m clinker strakes, seams at y = 0.125 + 0.25k |
  | `beam` | bottom-left | heavy dark timber, grain along U |
  | `trim` | bottom-right | brass / iron / canvas / red paint swatches, for fittings |

  Each region repeats every 1 m and has a 16 px gutter of its own wrapped
  pattern.
- UVs are **world-scaled planar projections**: 1 m of surface = 1 m of
  pattern. That's what makes neighbouring parts line up. All UVs stay in
  0..1. **(checked)**
- Faces thinner than 5 cm (ledges, slab edges) have their UVs **collapsed
  to a single point** of solid seam colour. A sliver seen edge-on makes the
  GPU sample from a very blurry mip level, which reaches into neighbouring
  regions.
- **Triangle budget**: structure parts ≤ 200 tris per cell and ≤ 1,000 per
  part. Detail and station parts ≤ 1,500. **(checked)**
  `Deck_1x1x1` is 24 tris and `HullSide_1x1x1` is 56. A 300-part ship of
  structure parts is well under 100k tris. Parts of the same kind share one
  mesh, so Unity can draw them together cheaply.

## Sockets

- Named empties, children of the part: `Socket_<Kind>_<NN>`, e.g.
  `Socket_Station_01`. **(checked)** They become `ShipSocket` markers on the
  prefab.
- A socket's **facing is the empty's Blender −Y**, which arrives as the
  node's **Unity +Z**. That's the same convention as a part's bow. Its up is
  Blender +Z, Unity +Y. Point it the way the station faces, i.e. towards
  where the operating player stands. **(Unity: unconfirmed)**
- Sockets lie inside the footprint. **(checked)**

## Collider hints

- Colliders are **never** mesh colliders from the render mesh.
- Each part carries collider hints as child empties: `COL_Box_<NN>` and
  `COL_Ramp_<NN>`. Each is a cube empty whose scale gives the half-extents.
  A ramp is a box tilted about its local X axis, at 45° or less. **(checked)**
- The same boxes are listed in the manifest as `center` and `size` in
  Unity coordinates. The Unity side builds `BoxCollider`s from either
  source. **(Unity: unconfirmed)**

## Naming

- Part object and FBX: `<Kind>_<sx>x<sy>x<sz>`, e.g. `Deck_1x1x1`,
  `HullSide_1x1x1`, `Helm_3x2x5`. Variants get a suffix: `HullSide_1x1x1_Bow`.
- Blender forces object names to be unique, so a second part's `COL_Box_01`
  becomes `COL_Box_01.001` in the `.blend`. Export strips the suffix, so the
  FBX and the manifest always use the clean name.

## Manifest

`exports/<Group>/<Kit>/manifest.json` describes every part in the kit in
Unity coordinates. It is generated, never hand-edited:

```json
{
  "name": "HullSide_1x1x1",
  "file": "HullSide_1x1x1.fbx",
  "category": "structure",
  "size": [1, 1, 1],
  "cells": [[0, 0, 0]],
  "walkable_top": null,
  "faces": {"+X": "outboard", "-X": "inboard", "+Z": "hull", "-Z": "hull", "+Y": "hull", "-Y": "hull"},
  "triangles": 56,
  "sockets": [{"name": "Socket_Station_01", "kind": "Station", "position": [0, 0, 0], "forward": [0, 0, 1], "up": [0, 1, 0]}],
  "colliders": [{"name": "COL_Box_01", "type": "box", "center": [-0.445, 0.5, 0], "size": [0.11, 1, 1]}]
}
```

## Blender-side authoring checklist

Custom properties on the part's mesh object:

| Property | Example | Meaning |
|---|---|---|
| `part_size` | `[1, 1, 1]` | footprint in cells (Unity x, y, z) |
| `part_cells` | `"[[0,0,0],[1,0,0]]"` | optional explicit cells for non-box footprints |
| `part_category` | `"structure"` | `structure` or `detail`, for the budget |
| `part_walkable` | `true` | has a walkable top |
| `part_faces` | `"{\"+Z\": \"hull\", ...}"` | face tags, JSON |

Validate any `.blend` directly:

```bash
blender --background blend/airship/hull_kit.blend --python scripts/cli/validate_part.py
```

## Known open items

- **Speckles in headless review renders.** In renders from this container,
  isolated pixels pick up colours from other atlas regions along a line on
  the hull. They vanish with nearest-pixel filtering and don't depend on
  gutter width, anisotropic filtering, the battens or thin faces. The
  container has no GPU (no `/dev/dri`), so Blender renders with Mesa's
  software rasterizer, and I believe that's the cause. **Check in Unity on a
  real GPU** by looking at a hull run from about 10 m away. If the speckles
  show there too, the fix is to split the atlas regions into separate
  textures in one texture array.
- Curved hull pieces (bow, stern, bilge turn), gunwale caps and the
  deck-to-hull trim are not built yet. The straight pieces define the
  profile those need to meet.
