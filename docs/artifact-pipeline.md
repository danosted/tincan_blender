# Artifact Pipeline

How a 3D asset goes from an idea to something `tincan_unity` can pull at a
specific, pinned version - no manual copying, no reaching into that repo.

## The five stages

1. **Iterate.** Blender MCP-driven, conversational - block out geometry,
   adjust materials, check scale, all live against a running Blender
   instance. See [README.md](../README.md) for the MCP connection.
2. **Build.** A per-asset headless script under `scripts/cli/` (e.g.
   `build_poc_crate.py`) turns the `.blend` source into a deterministic,
   reproducible output: mesh, baked textures, a review render, an FBX
   export, and a `package.json` (via `scripts/cli/upm_package.py`) - all
   written to `exports/<Group>/<Asset>/`. Re-running the same script from
   the same source should produce the same artifact.
3. **Import.** Run `scripts/cli/generate_meta_files.py` to produce Unity
   `.meta` files for the build output and copy them back into
   `exports/<Group>/<Asset>/`. Not optional: a UPM git package is an
   "immutable" folder as far as Unity's concerned, and it silently ignores
   any asset in one that doesn't already have a `.meta` file - discovered
   by validating this pipeline locally before ever pointing it at
   `tincan_unity` (see [[artifact-pipeline-design]] in project memory for
   the full story). This step also guarantees GUIDs stay stable across
   rebuilds of the same asset, which is what keeps scene/prefab references
   from breaking when a pinned version bumps.
4. **Publish.** Once a build's look is approved (the review render / a
   live-Editor screenshot looks right) and its `.meta` files exist, commit
   `exports/<Group>/<Asset>/` and run `scripts/cli/publish_asset.py` to tag
   that commit as an immutable, versioned artifact.
5. **Consume.** `tincan_unity` pins that exact tag in its
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

# 2. Import - generates/refreshes .meta files (needs `unity` signed in with
#    an active license; see project memory for this devcontainer's keyring
#    setup). First run creates a persistent authoring project at
#    ~/.unity-package-authoring (outside git, regenerable, safe to delete).
python3 scripts/cli/generate_meta_files.py --group Props --asset Crate

# 3. Review the render / iterate until it looks right, then commit
git add exports/Props/Crate
git commit -m "Crate v0.1.1: <what changed>"

# 4. Publish - tags HEAD, refuses if exports/<Group>/<Asset> has
#    uncommitted changes or the version's tag already exists
python3 scripts/cli/publish_asset.py --group Props --asset Crate
#    add --push once you're ready for the tag to leave this machine

# 5. Paste the printed manifest.json line into tincan_unity's
#    Packages/manifest.json (with the user's explicit go-ahead - see the
#    standing rule on that repo). Unity resolves the rest.
```

Validated for real, not just plausible: `props/crate/v0.1.1` was pulled into
a scratch Unity project via a local `file://` URL pinned to that tag, and
`AssetDatabase.LoadAssetAtPath` confirmed the FBX and its base color texture
both load as genuine `GameObject`/`Texture2D` assets - not just present on
disk, actually usable. The crate's GUID was also confirmed stable across the
v0.1.0 -> v0.1.1 rebuild.

**Known gap, not blocking:** `generate_meta_files.py` doesn't yet set
texture type/colorspace, so every PNG (including roughness/bump maps that
should be linear/non-color) gets Unity's default sRGB Color import. Fine for
now; worth fixing before this matters for real (visibly wrong roughness
response under Unity's lighting) rather than before shipping v0.1.1.

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
