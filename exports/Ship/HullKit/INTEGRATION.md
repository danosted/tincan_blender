# Hull Kit Integration

Modular airship hull parts on a 1 m grid, built to
`docs/part-contract.md` in the Blender repo. Generated with the assets;
`manifest.json` holds the same information as structured data.

## Coordinates

Y-up, +Z is the bow, +X starboard, 1 unit = 1 m. Each part's pivot is
the centre of the bottom face of its origin cell `[0,0,0]`. Positions
below are part-local Unity coordinates.

## Material

All parts use one material, `M_HullAtlas`, with
`HullAtlas_BaseColor.png` (sRGB) on Base Map. Remap every FBX's
`M_HullAtlas` to the same Unity material so parts batch together.

## Parts

### Deck_1x1x1

- File: `Deck_1x1x1.fbx`, 24 triangles, cells [[0, 0, 0]]
- Walkable top: y = 1.0
- Faces: +X `deck`, -X `deck`, +Z `deck`, -Z `deck`, +Y `open`, -Y `open`
- Collider `COL_Box_01`: box, centre [0.0, 0.95, 0.0], size [1.0, 0.1, 1.0]

### HullSide_1x1x1

- File: `HullSide_1x1x1.fbx`, 56 triangles, cells [[0, 0, 0]]
- Walkable top: no
- Faces: +X `outboard`, -X `inboard`, +Z `hull`, -Z `hull`, +Y `hull`, -Y `hull`
- Collider `COL_Box_01`: box, centre [-0.445, 0.5, 0.0], size [0.11, 1.0, 1.0]

## Setup

1. Import with the file's scale, no generated colliders, no rig or animation.
2. Build colliders from each part's `colliders` list (the `COL_*` child
   nodes carry the same boxes as scaled transforms) - never a mesh collider.
3. Turn `Socket_*` child nodes into `ShipSocket` markers.
