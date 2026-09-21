"""Publish a built asset as a versioned, pullable artifact.

"Publishing" means tagging the current commit so Unity's Package Manager can
pull this exact asset version straight from a git URL - no extra server, no
manual copying into tincan_unity. See docs/artifact-pipeline.md for the full
loop this is one step of.

Tag shape: <group>/<asset>/v<version>, lowercased - e.g. props/crate/v0.1.0.
Scoped per-asset so multiple assets in this one repo can version
independently. The version comes from the asset's own exports/<Group>/<Asset>
/package.json (written by the asset's build script via upm_package.py), so
there is exactly one place a version number is decided.

Usage (run from the repo root, plain python3 - no Blender/bpy needed):
    python3 scripts/cli/publish_asset.py --group Props --asset Crate
    python3 scripts/cli/publish_asset.py --group Props --asset Crate --push

Refuses to tag with uncommitted changes under the asset's export directory -
a tag only means something if it points at a commit that actually contains
that exact build. Run the asset's build script and `git commit` the result
first.
"""

import argparse
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run(*args, check=True):
    return subprocess.run(
        args, cwd=REPO_ROOT, check=check, capture_output=True, text=True
    )


def load_package_json(export_dir):
    path = os.path.join(export_dir, "package.json")
    if not os.path.isfile(path):
        sys.exit(
            f"No package.json at {path} - run this asset's build script first "
            "(it should call upm_package.write_package_json)."
        )
    with open(path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True, help="Asset group, e.g. Props")
    parser.add_argument("--asset", required=True, help="Asset name, e.g. Crate")
    parser.add_argument(
        "--push", action="store_true", help="Also push the new tag to origin"
    )
    args = parser.parse_args()

    export_dir = os.path.join(REPO_ROOT, "exports", args.group, args.asset)
    export_rel = os.path.relpath(export_dir, REPO_ROOT)
    package = load_package_json(export_dir)
    version = package["version"]

    tag = f"{args.group.lower()}/{args.asset.lower()}/v{version}"

    dirty = run("git", "status", "--porcelain", "--", export_rel)
    if dirty.stdout.strip():
        sys.exit(
            f"{export_rel} has uncommitted changes - commit the build output "
            f"before publishing, so tag '{tag}' actually points at it:\n"
            f"{dirty.stdout}"
        )

    existing = run("git", "tag", "--list", tag)
    if existing.stdout.strip():
        sys.exit(
            f"Tag '{tag}' already exists. Bump \"version\" in "
            f"{export_rel}/package.json, rebuild, commit, and try again."
        )

    head = run("git", "rev-parse", "--short", "HEAD").stdout.strip()
    run("git", "tag", "-a", tag, "-m", f"{package['displayName']} v{version}")
    print(f"Tagged {tag} at {head}")

    remote_url = None
    remote = run("git", "remote", "get-url", "origin", check=False)
    if remote.returncode == 0:
        remote_url = remote.stdout.strip()

    if args.push:
        if not remote_url:
            sys.exit("No 'origin' remote configured - can't --push. Tag created locally only.")
        run("git", "push", "origin", tag)
        print(f"Pushed {tag} to origin")
    else:
        print("Not pushed (pass --push to push it to origin).")

    url_base = remote_url or "<this repo's git URL>"
    print()
    print("Unity-side Packages/manifest.json entry:")
    print(f'  "{package["name"]}": "{url_base}?path=exports/{args.group}/{args.asset}#{tag}"')


if __name__ == "__main__":
    main()
