# Airship Design Reference

## Setting

Medieval fantasy world. Wooden, magically-powered flying airships — think the
propulsion and construction language of Final Fantasy IX's airships
(Hilda Garde, Invincible: timber hulls, sails and rigging repurposed as
magic-era flight surfaces, brass/cloth detailing) crossed with the lived-in,
hand-built feel of Studio Ghibli's *Castle in the Sky* — Dola's pirate ship
the Tiger Moth and Laputa itself. Wood and canvas over metal and glass. Visible
joinery, rope, patched repairs. Flight is magical (the game already frames
fuel as "aether" — glowing crystals, not liquid fuel), not mechanical/sci-fi.

Reference cues to keep pulling from:
- Warm wood tones, weathered paint, brass fittings, rope and canvas rigging.
- Asymmetry and repair-patchwork over pristine symmetry — the ship should
  look flown and lived-in, not showroom-new.
- Sails/fins as both a flight surface and a readable silhouette — the ship
  should be identifiable from its profile alone.
- Warm, human-scale interiors (FFIX bridge/engine room framing) rather than
  sterile sci-fi corridors.

## Layout principles

The current placeholder ship works but wasn't designed as a space — this
build should be:

- **Multi-level.** At least a lower/cargo-engine level and an upper/bridge
  level, per the deck heights already used in-game (see
  [unity-integration.md](unity-integration.md)).
- **Legible navigation.** A player should be able to build a mental map fast:
  consistent "front is bow, aft is engine" orientation, sightlines between
  connected rooms where it makes sense (e.g. glass or open archways rather
  than blind corridors), and distinct room silhouettes/lighting so rooms are
  recognizable without reading a label.
- **Not cramped.** Rooms where 2-4 players gather and act at once — bridge,
  engine room — need enough clear floor space around interactive fixtures
  that players don't block each other. Corridors should comfortably pass two
  players.
- **Few, purposeful chokepoints.** Doors/hatches between rooms are fine and
  thematic (the game already has an `AirshipDoor` interaction), but avoid
  stacking multiple in a single path between frequently-used rooms.

## Rooms

See [../GOALS.md](../GOALS.md) for the authoritative, current room list (what's
confirmed by gameplay code vs. still open). Keep that file up to date as the
layout firms up rather than duplicating the list here.
