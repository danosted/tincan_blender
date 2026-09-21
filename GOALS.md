# Current Goal

Build a proper, purpose-designed player airship for **TinCan** (the cooperative
airship-crew game in the sibling `tincan_unity` repo), replacing the current
placeholder prop — using a Blender MCP + CLI enabled workflow so the model can
be iterated on conversationally (via Claude driving Blender directly) and
scripted (via headless Blender CLI runs) rather than hand-built in one pass.

## Why

The game currently plays fine with a placeholder ship prop, but it wasn't
designed as a space — fixture placement is guesswork ("a guess from mesh
pivots, please adjust"), and there's no real navigation layout. The crew is
meant to move between distinct rooms while operating the ship together, and
that only works if the ship is actually laid out for it: multiple levels,
readable rooms, generous circulation, no cramped pinch points.

## Scope of "done" for this goal

- A multi-level airship interior in Blender, built room-by-room, that:
  - Covers at least the rooms already implied by gameplay code (see below).
  - Is easy to navigate — clear paths between rooms, no dead-ends that feel
    like backtracking, enough headroom/width that shared spaces (engine room,
    bridge) don't feel cramped with 2-4 players in them at once.
  - Stays compatible with existing fixture logic in `tincan_unity` (deck
    heights, ship scale, fixture anchor points) so gameplay code doesn't need
    to be rewritten just because the hull changed shape. See
    [docs/unity-integration.md](docs/unity-integration.md) for the concrete
    numbers pulled from the current placeholder.
- Exported and wired into `tincan_unity` as a replacement for `Airship_Prefab`,
  with fixtures (fuel motor, jerry can crate, net rack, helm/gauge) repositioned
  onto the new hull.

## Known rooms (from current gameplay code)

Pulled from `tincan_unity`'s fuel-loop feature and ship docs — these already
have gameplay logic attached, so the new hull must have a place for each:

- **Bridge / helm** — pilot's station, ship controls, fuel gauge.
- **Engine room** — the aether motor / fill port (currently "inside the aft
  cabin").
- **Cargo hold** — aether crystal (jerry can) supply crate, currently at the
  bow.
- **Living quarters** — crew rest/spawn space.
- **Kitchen / galley** — crew space.
- **Navigation room** — charting/nav station.

There's also a **deck/rail** area outside the cabin (net rack is mounted on
"the starboard rail"), so at least one exterior/open-air space exists already.

## Likely missing rooms (open — prune or confirm as the design firms up)

Given the FFIX / Castle in the Sky tone (see
[docs/airship-design.md](docs/airship-design.md)), these are common gaps worth
considering, not commitments yet:

- **Workshop / repair bay** — ship maintenance, crafting, tool storage.
- **Captain's quarters** — distinct from general living quarters.
- **Med bay / infirmary**.
- **Observation deck or crow's nest** — open-air, good for a Ghibli-style
  scenic/navigation beat.
- **Boarding ramp / gangway** — a clear "front door" for entering the ship.
- **Sail / rigging control** — if the magical propulsion involves sails
  players interact with, this may overlap with or replace part of the bridge.

## Workflow

1. Blender is MCP-enabled — Claude can drive a running Blender instance
   directly (see [README.md](README.md) for the live connection). Use this for
   iterative, conversational modeling: block out rooms, adjust layout, check
   scale against the numbers in `docs/unity-integration.md`.
2. Headless Blender CLI (`scripts/cli/`) is for repeatable, scripted steps:
   validation, export, batch operations that don't need a human in the loop.
3. Finished pieces get exported to `exports/`, committed, and published as a
   versioned artifact (git tag) that `tincan_unity` pulls via Unity's own
   Package Manager — see [docs/artifact-pipeline.md](docs/artifact-pipeline.md)
   for the full loop. This repo never writes into `tincan_unity` directly;
   that repo pulls a pinned version of an asset into itself.
