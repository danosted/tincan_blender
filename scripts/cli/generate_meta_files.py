"""Generate Unity .meta files for a built asset and copy them back into
exports/<Group>/<Asset>/, so they get committed alongside the assets they
describe.

Why this exists: Unity refuses to auto-generate .meta files for assets
resolved via a UPM git package (an "immutable" package folder) - unlike the
normal mutable Assets/ folder, packages are expected to ship with their own
stable, pre-generated GUIDs already checked in. Without this step, a
published package resolves fine but every asset inside it is silently
ignored ("has no meta file, but it's in an immutable folder"). See
docs/artifact-pipeline.md.

This is the one-time "authoring" import: it drives a small, persistent,
purely-mechanical Unity project (AUTHORING_PROJECT below - not part of the
product, not tincan_unity, safe to delete and let this script recreate) that
exists only to host imports and produce their .meta files. Re-running this
against the same exports/<Group>/<Asset> path reuses the same Assets/ path
inside that project, so Unity preserves the existing GUIDs across rebuilds
instead of randomizing them - that stability is the entire point.

Usage (plain python3, no bpy - needs the `unity` CLI on PATH, signed in,
with an active license; see project memory / docs/artifact-pipeline.md for
the keyring setup this devcontainer needs for that):
    python3 scripts/cli/generate_meta_files.py --group Props --asset Crate

Run this AFTER the asset's build script and BEFORE `git add`/publish_asset.py
- the .meta files it produces need to be committed together with the assets
they belong to.
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AUTHORING_PROJECT = os.path.expanduser("~/.unity-package-authoring")
EDITOR_VERSION = "6000.4.5f1"  # matches tincan_unity's .unity-version
AUTHORING_TEMPLATE = "com.unity.template.3d"  # lightest core template - render pipeline is irrelevant here

# Files that aren't meant to be Unity assets - left un-meta'd on purpose.
SKIP_NAMES = {"package.json", "manifest.json", "INTEGRATION.md"}


def run(*args, check=True, timeout=None, retry_busy=0):
    cmd = ["unity"] + list(args)
    attempts = retry_busy + 1
    for attempt in range(attempts):
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)
        # The Editor can report itself reachable (`unity command` listing
        # succeeds) while still settling from startup - actual command
        # execution 503s with "Server Busy" for a bit longer in that window.
        if result.returncode != 0 and "Server Busy" in result.stdout and attempt < attempts - 1:
            time.sleep(5)
            continue
        break
    if check and result.returncode != 0:
        sys.exit(f"Command failed: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}")
    return result


def unity_reachable(project_path):
    result = run("command", "--project-path", project_path, "--format", "json", check=False, timeout=15)
    if result.returncode != 0:
        return False
    try:
        return json.loads(result.stdout).get("success", False)
    except (json.JSONDecodeError, ValueError):
        return False


def ensure_authoring_project():
    if not os.path.isdir(AUTHORING_PROJECT):
        print(f"Creating authoring project at {AUTHORING_PROJECT} ...")
        parent = os.path.dirname(AUTHORING_PROJECT)
        name = os.path.basename(AUTHORING_PROJECT)
        run(
            "projects", "new", name,
            "--path", parent,
            "--editor-version", EDITOR_VERSION,
            "--template", AUTHORING_TEMPLATE,
            timeout=600,
        )

    if unity_reachable(AUTHORING_PROJECT):
        return

    print("Launching headless Editor for the authoring project ...")
    editors = json.loads(
        run("editors", "--installed", "--format", "json").stdout
    )["data"]
    match = next((e for e in editors if e["version"] == EDITOR_VERSION), None)
    if not match:
        sys.exit(f"Editor {EDITOR_VERSION} not installed - run `unity install {EDITOR_VERSION}` first.")
    unity_bin = match["location"]  # already the full binary path

    run("pipeline", "install", "--project-path", AUTHORING_PROJECT, timeout=120)

    log_path = os.path.join(AUTHORING_PROJECT, "editor.log")
    subprocess.Popen(
        [unity_bin, "-batchmode", "-projectPath", AUTHORING_PROJECT, "-logFile", log_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    for _ in range(60):
        if unity_reachable(AUTHORING_PROJECT):
            return
        time.sleep(5)
    sys.exit(f"Authoring project's Editor never became reachable - check {log_path}")


def importable_files(export_dir):
    for path in sorted(glob.glob(os.path.join(export_dir, "*"))):
        if os.path.isdir(path):
            continue
        if os.path.basename(path) in SKIP_NAMES:
            continue
        if path.endswith(".meta"):
            continue
        yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True)
    parser.add_argument("--asset", required=True)
    args = parser.parse_args()

    export_dir = os.path.join(REPO_ROOT, "exports", args.group, args.asset)
    if not os.path.isdir(export_dir):
        sys.exit(f"No such export dir: {export_dir} - run the asset's build script first.")

    ensure_authoring_project()

    dest_rel_dir = f"Assets/{args.group}/{args.asset}"
    generated = []
    for src in importable_files(export_dir):
        filename = os.path.basename(src)
        dest_path = f"{dest_rel_dir}/{filename}"
        run(
            "command", "import_asset",
            "--source", src,
            "--path", dest_path,
            "--confirm", "true",
            "--project-path", AUTHORING_PROJECT,
            "--timeout", "60",
            "--format", "json",
            retry_busy=6,
        )
        meta_src = os.path.join(AUTHORING_PROJECT, dest_rel_dir, filename + ".meta")
        meta_dest = os.path.join(export_dir, filename + ".meta")
        if not os.path.isfile(meta_src):
            sys.exit(f"Import reported success but no .meta was produced at {meta_src}")
        shutil.copyfile(meta_src, meta_dest)
        generated.append(meta_dest)

    print(f"Generated {len(generated)} .meta file(s) in {export_dir}:")
    for path in generated:
        print(" ", os.path.relpath(path, REPO_ROOT))


if __name__ == "__main__":
    main()
