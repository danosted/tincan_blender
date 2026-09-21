"""Shared helper for writing a Unity Package Manager (UPM) package.json
alongside an asset's exported FBX/textures.

This is what makes a build's output pullable as a versioned artifact: Unity's
Package Manager can add a dependency straight from a git URL pinned to a tag,
scoped to a subfolder - `<repo-url>?path=exports/<Group>/<Asset>#<tag>` - and
that subfolder just needs a valid package.json at its root to be treated as a
package. See docs/artifact-pipeline.md for the full publish/consume loop.

Not Blender-specific - importable from `bpy`-free contexts too (e.g. a future
standalone publish step), so it has no bpy import.
"""

import json
import os


def write_package_json(
    export_dir,
    name,
    version,
    display_name,
    description,
    unity_version="6000.4",
):
    """Write a minimal, valid UPM package.json into export_dir.

    `name` must be the full reverse-domain package id (e.g.
    "com.tincan.props.crate") - lowercase, no spaces, matching UPM's naming
    rules. `version` is a plain semver string ("1.0.0"), no leading "v" (the
    "v" belongs on the git tag, not inside the manifest).
    """
    os.makedirs(export_dir, exist_ok=True)
    payload = {
        "name": name,
        "version": version,
        "displayName": display_name,
        "description": description,
        "unity": unity_version,
    }
    path = os.path.join(export_dir, "package.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    return path
