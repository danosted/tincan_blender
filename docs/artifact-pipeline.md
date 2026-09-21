# Artifact Pipeline

How a 3D asset goes from an idea to something `tincan_unity` can pull at a
specific, pinned version - no manual copying, no reaching into that repo.

## The four stages

1. **Iterate.** Blender MCP-driven, conversational - block out geometry,
   adjust materials, check scale, all live against a running Blender
   instance. See [README.md](../README.md) for the MCP connection.
2. **Build.** A per-asset headless script under `scripts/cli/` (e.g.
   `build_poc_crate.py`) turns the `.blend` source into a deterministic,
   reproducible output: mesh, baked textures, a review render, an FBX
   export, and a `package.json` (via `scripts/cli/upm_package.py`) - all
   written to `exports/<Group>/<Asset>/`. Re-running the same script from
   the same source should produce the same artifact.
3. **Publish.** Once a build's look is approved (the review render / a
   live-Editor screenshot looks right), commit `exports/<Group>/<Asset>/`
   and run `scripts/cli/publish_asset.py` to tag that commit as an
   immutable, versioned artifact.
4. **Consume.** `tincan_unity` pins that exact tag in its
   `Packages/manifest.json`, and Unity's own Package Manager fetches it -
   the same way it already pins every other dependency there.

## Why git tags instead of a separate artifact server

Unity's Package Manager can add a dependency straight from a git URL, scoped
to a subfolder and pinned to a revision:

```
"com.tincan.props.crate": "https://github.com/<owner>/<repo>.git?path=exports/Props/Crate#props/crate/v0.1.0"
```

That query-string `path=` plus `#<tag>` *is* "pull artifact version X" -
using tooling Unity already ships, with no registry to host or keep
running. The tradeoff: the tagged subfolder needs a `package.json` at its
root to be a valid UPM package, and the asset's `exports/` output has to
actually be committed (not gitignored) for a tag pointing at it to mean
anything.

## Naming conventions

- **Package name**: `com.tincan.<group>.<asset>`, all lowercase - e.g.
  `com.tincan.props.crate` for `exports/Props/Crate/`.
- **Git tag**: `<group>/<asset>/v<version>`, all lowercase - e.g.
  `props/crate/v0.1.0`. Scoped per-asset (not global) so multiple assets in
  this one repo version independently.
- **Version**: plain semver in `package.json` (`"0.1.0"`, no leading `v` -
  the `v` belongs on the git tag only). Bumped by hand in the asset's build
  script before a new publish; there is exactly one place per asset this is
  decided (`PACKAGE_VERSION` in e.g. `build_poc_crate.py`).

## Step by step

```bash
# 1. Build (regenerates exports/Props/Crate/, including package.json)
blender --background --python scripts/cli/build_poc_crate.py

# 2. Review the render / iterate until it looks right, then commit
git add exports/Props/Crate
git commit -m "Crate v0.1.0: <what changed>"

# 3. Publish - tags HEAD, refuses if exports/<Group>/<Asset> has
#    uncommitted changes or the version's tag already exists
python3 scripts/cli/publish_asset.py --group Props --asset Crate
#    add --push once you're ready for the tag to leave this machine

# 4. Paste the printed manifest.json line into tincan_unity's
#    Packages/manifest.json (with the user's explicit go-ahead - see the
#    standing rule on that repo). Unity resolves the rest.
```

To ship a new version of an existing asset: bump `PACKAGE_VERSION` in the
build script, rebuild, commit, publish again - `publish_asset.py` refuses to
reuse a tag, so there is no way to silently overwrite a version someone may
already be pinned to.

## What this replaces

Earlier in this project's life, an asset was pushed into `tincan_unity`
by directly copying files into that repo and running Unity batch mode
against it from here. That was reverted - see the standing rule against
writing to sibling repos without asking every time. This pipeline exists so
that "getting an asset into the game" is something `tincan_unity` does *to
itself* (bump a manifest line, let Package Manager resolve it), not
something this repo or an agent working in it does *to* `tincan_unity`.

## Validating changes to this pipeline

Don't test against `tincan_unity`. A throwaway Unity project works for
proving the mechanics (does the FBX import, does the texture wire up, does
a screenshot come out looking right) - see `scripts/cli/build_poc_crate.py`'s
docstring history for how the crate itself was validated this way. Put any
such scratch project in the session scratchpad or somewhere outside this
repo's git tree, not inside it - a live Unity Editor process left running
against a project nested in a git working tree with no `.git` boundary of
its own has been observed writing an unrelated Unity-flavored `.gitignore`
into the enclosing repo's root as an unrequested side effect.
