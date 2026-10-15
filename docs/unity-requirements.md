# What the Blender side needs from `tincan_unity`

Shipyard parts are built against [part-contract.md](part-contract.md). Parts
of that contract are guesses about how the shipyard code works, because this
repo can't see `tincan_unity`. This list is what would turn the guesses into
facts. It's ordered by how much the answer changes the parts: items near the
top change geometry, items near the bottom only change import setup.

Answering in prose is fine. Pointing at the file that answers it is better.

## 1. Grid and placement (changes every part)

1. **Pivot.** When the shipyard places a part in cell `c`, where does the
   prefab root go? We assume `cellCentre(c) + (0, −0.5, 0)`, so the mesh pivot
   is the bottom-centre of its origin cell.
2. **Multi-cell footprints.** How does a `PART_` asset list its cells, and
   which one is the origin? We assume the bottom layer, centre column for odd
   widths and the port/aft-of-centre column for even widths. **Please share
   one real `PART_` asset, ideally the 3×2×5 helm, and the class that
   defines it.**
3. **Rotation.** Do parts rotate only in 90° yaw steps about the pivot? Is
   mirroring ever used? We'd rather not mirror: it flips triangle winding,
   so port pieces are rotated starboard pieces instead.
4. **Cell occupancy.** Is it one part per cell? Or can a floor tile and a
   wall or hull piece share a cell? We assume one part per cell, with edge
   pieces putting their skin on their cell's inboard face.
5. **Grid origin and deck heights.** Where is cell `[0,0,0]` relative to the
   ship's `NetworkObject` root? The current placeholder decks are at
   y = −1.82, −3.49, −3.88 and 0.98, none of them on a 1 m grid. Are
   fixtures (motor fill port, jerry can crate, net rack, helm and gauge)
   going to be re-anchored to sockets on the new parts, or must some of
   those heights survive?

## 2. Player and space (changes room sizes)

6. **Player capsule**: height, radius, step offset, max walkable slope, jump
   height. These set storey pitch (we assume 3 cells), door and corridor
   widths (we assume 2 cells for corridors), stair steepness (we assume 45°)
   and railing height.
7. **Camera**: first or third person, and how close it gets to walls. This
   decides how much close-up texture density matters (currently about
   480 px/m).

## 3. Sockets and stations

8. **`ShipSocket`**: which fields it reads from the socket node (position
   only, or also rotation?) and which direction it treats as the station's
   facing. We assume the node's +Z faces the way the station faces.
9. **Socket kinds and station sizes**: the list of `<Kind>` names
   (`Station`, ...?) and each station's footprint, so parts can reserve
   space and clearance around them.

## 4. Rendering

10. **Render pipeline and shader**: URP Lit? Is a toon or painted shader
    planned for the Ghibli / FFIX look? That changes whether we author
    roughness, normal maps, or flat-shaded colour only.
11. **Batching**: the ship moves, so static batching is out. Is the SRP
    Batcher on? Is GPU instancing allowed on the shared hull material? A ship
    has a few hundred parts and the budget assumes they share one material.
12. **Lighting**: realtime only, or baked? Baked lighting on a moving
    modular ship would need a second UV channel on every part, which we
    don't author today.
13. **Texture import settings** we should bake into the `.meta` files:
    max size, compression, filter mode, anisotropic level and mipmaps.
14. **LODs**: how far away are ships seen (other ships, the shipyard
    overview)? This decides whether parts need LOD meshes.

## 5. Import and the shipyard data

15. **The shipyard's manifest format.** You mentioned "the same as your
    existing manifest". Which file is that, and what is its schema? Our
    generated `exports/Ship/HullKit/manifest.json` should match it rather
    than invent its own.
16. **Who builds the prefabs?** Proposal: an editor importer in
    `tincan_unity` that reads a kit's `manifest.json` and, per part, builds:
    - the prefab, with `BoxCollider`s from `colliders`;
    - `ShipSocket` markers from the `Socket_*` nodes;
    - the `PART_` asset, with cells from `cells`;
    - the remap of `M_HullAtlas` onto one shared material.

    Then a new part only needs a package version bump. Is that welcome? Or do
    prefabs and `PART_` assets stay hand-made?
17. **Runtime spawning and networking**: are parts instantiated at runtime
    from `PART_` assets under the ship's `NetworkObject`, or baked into the
    ship prefab? This decides whether parts may contain any `NetworkObject`
    (we assume never).
18. **Delivery**: one UPM package per kit (`com.tincan.ship.hullkit`), pinned
    by tag as in [artifact-pipeline.md](artifact-pipeline.md)? Or one package
    for all ship parts?

## 6. One-time checks in Unity (on a real GPU)

These can't be done from this container. There's no Unity editor installed
right now and no GPU.

- [ ] Import `Deck_1x1x1.fbx` and `HullSide_1x1x1.fbx` and confirm:
  - every node has identity rotation and scale 1;
  - the hull's planking faces +X;
  - a `COL_Box_01` child exists with the manifest's centre and size.
- [ ] Place 5 hull pieces in a row and look at them from about 10 m: no
  coloured speckles. See "Known open items" in part-contract.md.
- [ ] Rotate a hull piece 180° about Y and confirm its inner face meets a deck
  tile's edge flush.
- [ ] Both FBXs remapped to one material, and they batch together.

## Source files that would answer most of this

If these files can be shared with this repo, most of the questions above
answer themselves:

- the `PART_` ScriptableObject class and one instance (the helm);
- `ShipSocket` and the shipyard placement / grid code;
- the shipyard manifest file and anything that reads it;
- the player controller's capsule and movement settings;
- the URP asset and quality settings;
- `.docs/plans/` entries about the shipyard.
