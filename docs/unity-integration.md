# Unity Integration Notes

Concrete constraints pulled from the sibling `tincan_unity` repo (the game),
current as of `.docs/plans/ship-tasks-prototype.md` there. Re-check that file
if these ever look stale — this is a snapshot, not a live source of truth.

## Current placeholder ship

`Assets/Prefabs/Airship/Parts/FuelSystem.prefab`, nested under the
`Airship_Prefab` root (shares the airship's `NetworkObject` — any replacement
hull needs fixtures attached the same way, as children of the ship's own
`NetworkObject` root, not a separate one).

## Deck heights (ship-local Y, meters)

These come from real playtesting fixes ("the crate and rack got tall trigger
`InteractVolume` children and correct deck heights"), so treat them as load
-bearing for where floors should land in the new hull:

| Deck | Local Y |
|---|---|
| Bow deck | -1.82 |
| Mid deck | -3.49 |
| Cabin floor | -3.88 |
| Aft top deck | 0.98 |

## Approximate hull length

Existing fixture Z positions span roughly from the aft cabin (motor at
ship-local Z ≈ -11.5) to the bow (jerry can crate at ship-local Z ≈ +21) — a
current hull length on the order of **~30-35 m** in ship-local space. The
flying-can spawn clearance is tuned for "the current hull and sails" at 60 m
ahead of the ship, so a significantly longer/wider hull would need that
retuned (`Assets/Settings/FlyingCanConfig.asset`).

## Existing fixture anchor points (ship-local)

Useful as a starting layout even though these are described as placeholder
guesses in the source ("please adjust in the Editor"):

| Fixture | Room | Ship-local position |
|---|---|---|
| `MotorFillPort` (aether motor) | Engine room, aft cabin | (1, -2.4, -11.5) |
| `JerryCanSupply` crate (aether crystals) | Cargo, bow | (1, -1.9, 21) |
| `NetRack` | Starboard rail (exterior deck) | (5.2, -2.2, 0) |
| `FuelGauge` / helm | Bridge, near `Control_Station` | (2.6, 3.1, -8.4) / (1.1, 2.15, -7.8) |

## Scale convention

Equipment wrappers use a **uniform 2.5 scale** to match the existing fuel
gauge. Match this for new equipment-scale props; the hull itself is
independent of this (it's a per-prop convention, not a whole-scene one).

## Asset pipeline convention (already established in `tincan_unity`)

- Imported art lives at `Assets/Models/<Group>/<Asset>/` — FBX + textures +
  a `manifest.json`/`INTEGRATION.md` per asset folder.
- **Raw models never go directly in scenes.** Wrap them in a prefab under
  `Assets/Prefabs/` first.
- Follow this same shape when staging exports from this repo: export to
  `exports/<Group>/<Asset>/`, then copy into `tincan_unity/Assets/Models/...`
  and wrap in a prefab there.
