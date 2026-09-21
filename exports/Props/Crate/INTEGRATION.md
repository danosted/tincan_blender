# Crate Integration

Simple wooden aether-crystal supply crate. Pipeline proof-of-concept asset -
modeled, textured, and exported headlessly from Blender.

This file is generated with the assets. `manifest.json` contains the same
contract as structured data for tools and importing agents.

## Files and scale

- `Crate.fbx` — Static wooden crate, single mesh, three baked textures (base
  color + roughness + bump). (1 source unit = 1 m).
- `Crate_BaseColor.png` — Baked wood base color texture (sRGB).
- `Crate_Roughness.png` — Baked roughness texture (grayscale, non-color data).
- `Crate_Bump.png` — Baked bump/height texture (grayscale, non-color data).
  Wire into a Bump/Height shader slot if the target shader has one;
  optional otherwise.

Asset type: `static_prop`.

Coordinate system: Y-up, -Z forward; one exported unit is one meter.

## Placement

Place anywhere crates make sense (cargo hold, bow deck near the existing
`JerryCanSupply` crate). Single static mesh, no moving parts.

## Required nodes

- `Crate` — The entire prop; one static mesh.

## Setup

1. Import without a rig or animation clips.
2. Add a box collider matching the mesh bounds.
3. Assign the URP/Lit shader to the imported material and confirm
   `Crate_BaseColor.png` is on Base Map; `Crate_Roughness.png` is authored as
   a plain grayscale roughness map (not yet packed into a Metallic/Smoothness
   alpha channel — repack or wire a Smoothness slot mapped from inverted
   roughness if the target shader wants smoothness instead); `Crate_Bump.png`
   can be wired into a Bump/Height slot if the target shader has one.

## Collision

Single box collider matching the mesh bounds.

## Pipeline note

This is the first asset through the Blender → Unity pipeline
([../../../GOALS.md](../../../GOALS.md),
[../../../docs/unity-integration.md](../../../docs/unity-integration.md)).
Its geometry/texture quality is a first pass, not a target — see
`renders/crate_poc_v1.png` in the Blender repo for the review render this was
built from, and iterate there before re-exporting.
