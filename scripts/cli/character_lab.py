"""Drive CharacterLab - the small Unity project that hosts and tests the
com.tincan.characters package - headlessly.

CharacterLab lives outside git at ~/CharacterLab (on the container's own
disk; a Unity Library on the Windows bind mount is very slow, and an Editor
running inside this repo's tree has written stray files into it before). It's
regenerable: delete it and `setup` recreates it. It references the package by
absolute file: path, so Unity writes .meta files and the generated assets
(prefab, controller, material) straight into packages/com.tincan.characters/.

Usage (plain python3; needs the `unity` CLI signed in with a license, and
`source ~/.keyring-env` in the shell):
    python3 scripts/cli/character_lab.py setup   # create/refresh the project
    python3 scripts/cli/character_lab.py build   # KidAssetBuilder.BuildAll + sync the sample back
    python3 scripts/cli/character_lab.py test    # play-mode tests + contact sheet
    python3 scripts/cli/character_lab.py all     # setup, build, test

The full loop after a Blender change:
    blender --background --python scripts/cli/build_kid.py
    python3 scripts/cli/character_lab.py all

`test` writes renders/kid_unity_v<version>_sheet.png (how URP draws the
kid) and renders/kid_unity_v<version>_tests.json (each test, pass/fail and
its message), and exits non-zero if any test failed.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACKAGE_NAME = "com.tincan.characters"
PACKAGE_DIR = os.path.join(REPO_ROOT, "packages", PACKAGE_NAME)
LAB = os.path.expanduser(os.environ.get("CHARACTER_LAB", "~/CharacterLab"))
EDITOR_VERSION = "6000.4.5f1"  # matches tincan_unity's .unity-version
TEMPLATE = "com.unity.template.urp-blank"
TOOLING_SRC = os.path.join(REPO_ROOT, "scripts", "unity")
TOOLING_DST = os.path.join(LAB, "Assets", "Editor", "TinCanLab")
SAMPLES_SRC = os.path.join(PACKAGE_DIR, "Samples~")
SAMPLES_DST = os.path.join(LAB, "Assets", "Samples")
RENDERS = os.path.join(REPO_ROOT, "renders")

# Extra packages CharacterLab needs on top of the template: the test runner,
# and NGO so the NetcodeAdapter sample is compile-checked here.
EXTRA_DEPENDENCIES = {
    "com.unity.test-framework": "1.5.1",
    "com.unity.netcode.gameobjects": "2.4.4",
}


def package_version():
    with open(os.path.join(PACKAGE_DIR, "package.json")) as f:
        return json.load(f)["version"]


def editor_binary():
    out = subprocess.run(["unity", "editors", "--installed", "--format", "json"],
                         capture_output=True, text=True, check=True).stdout
    match = next((e for e in json.loads(out)["data"] if e["version"] == EDITOR_VERSION), None)
    if not match:
        sys.exit(f"Editor {EDITOR_VERSION} not installed - run `unity install {EDITOR_VERSION}`.")
    return match["location"]


def run_unity(args, log_name, env=None, graphics=False):
    """Run the Editor in batchmode; on failure print the log's tail."""
    log = os.path.join(LAB, "Logs", log_name)
    os.makedirs(os.path.dirname(log), exist_ok=True)
    cmd = [editor_binary(), "-batchmode", "-projectPath", LAB, "-logFile", log, *args]
    if not graphics:
        cmd.append("-nographics")
    else:
        # Rendering needs a display; xvfb-run gives the Editor a virtual one.
        cmd = ["xvfb-run", "-a", "-s", "-screen 0 1280x1024x24", *cmd]
    print("$", " ".join(cmd[-8:]))
    result = subprocess.run(cmd, env={**os.environ, **(env or {})})
    return result.returncode, log


def tail(path, n=60):
    try:
        with open(path, errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return f"(no log at {path})"


def grep(path, needles):
    try:
        with open(path, errors="replace") as f:
            return [line.rstrip() for line in f if any(k in line for k in needles)]
    except OSError:
        return []


# --- setup -------------------------------------------------------------------
def setup():
    if not os.path.isdir(LAB):
        print(f"Creating CharacterLab at {LAB} ({TEMPLATE}, {EDITOR_VERSION}) ...")
        subprocess.run(["unity", "projects", "new", os.path.basename(LAB), "--path", os.path.dirname(LAB),
                        "--editor-version", EDITOR_VERSION, "--template", TEMPLATE],
                       check=True, timeout=1800)

    manifest_path = os.path.join(LAB, "Packages", "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    deps = manifest.setdefault("dependencies", {})
    deps[PACKAGE_NAME] = "file:" + PACKAGE_DIR
    for name, version in EXTRA_DEPENDENCIES.items():
        deps.setdefault(name, version)
    testables = manifest.setdefault("testables", [])
    if PACKAGE_NAME not in testables:
        testables.append(PACKAGE_NAME)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    # Authoring tooling: copied, not symlinked, so Unity's file watcher and
    # meta generation behave like any other Assets/ file.
    os.makedirs(TOOLING_DST, exist_ok=True)
    for name in os.listdir(TOOLING_SRC):
        if name.endswith(".cs"):
            shutil.copy2(os.path.join(TOOLING_SRC, name), os.path.join(TOOLING_DST, name))

    # Samples: mirror Samples~/<name> into Assets/Samples/<name>, metas
    # included, the same way Package Manager's "Import" does it.
    for name in os.listdir(SAMPLES_SRC):
        src, dst = os.path.join(SAMPLES_SRC, name), os.path.join(SAMPLES_DST, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
    print(f"CharacterLab ready: {LAB}")


# --- build -------------------------------------------------------------------
def sync_samples_back():
    """Copy what Unity generated for the samples (metas, the lab scene) back
    into the package's Samples~, so an imported sample keeps the same GUIDs
    the scene references."""
    copied = []
    for name in os.listdir(SAMPLES_SRC):
        src_root, lab_root = os.path.join(SAMPLES_SRC, name), os.path.join(SAMPLES_DST, name)
        if not os.path.isdir(lab_root):
            continue
        for dirpath, _, files in os.walk(lab_root):
            rel = os.path.relpath(dirpath, lab_root)
            for fn in files:
                if fn.endswith(".meta") or fn.endswith(".unity"):
                    dst_dir = os.path.join(src_root, rel)
                    os.makedirs(dst_dir, exist_ok=True)
                    shutil.copy2(os.path.join(dirpath, fn), os.path.join(dst_dir, fn))
                    copied.append(os.path.relpath(os.path.join(dst_dir, fn), REPO_ROOT))
    # A Samples~ folder's own meta is harmless but Unity never reads it there.
    return copied


def build():
    code, log = run_unity(["-quit", "-executeMethod", "TinCan.CharacterLab.KidAssetBuilder.BuildAll"], "build.log")
    ok_lines = grep(log, ["[KidBuilder]"])
    errors = grep(log, ["error CS", "Exception", "Shader error", "[KidBuilder] "])
    for line in ok_lines + [e for e in errors if e not in ok_lines]:
        print("  " + line)
    if code != 0 or not any("[KidBuilder] OK" in l for l in ok_lines):
        print(tail(log))
        sys.exit(f"build failed (exit {code}); full log: {log}")
    copied = sync_samples_back()
    print(f"Synced {len(copied)} sample file(s) back into Samples~")


# --- test --------------------------------------------------------------------
def test():
    version = package_version()
    results = os.path.join(LAB, "Logs", "test-results.xml")
    if os.path.exists(results):
        os.remove(results)
    capture_dir = os.path.join(LAB, "Logs", "capture")
    os.makedirs(capture_dir, exist_ok=True)
    code, log = run_unity(
        ["-runTests", "-testPlatform", "PlayMode", "-testResults", results,
         "-assemblyNames", "TinCan.Characters.Tests"],
        "test.log", env={"TINCAN_CAPTURE_DIR": capture_dir}, graphics=True)

    if not os.path.exists(results):
        print(tail(log))
        sys.exit(f"no test results (exit {code}); full log: {log}")

    cases = []
    for tc in ET.parse(results).getroot().iter("test-case"):
        msg = tc.find("./failure/message")
        out = tc.find("./output")
        cases.append({
            "name": tc.get("name"),
            "result": tc.get("result"),
            "seconds": float(tc.get("duration", 0)),
            "message": (msg.text or "").strip() if msg is not None else "",
            "log": [l for l in (out.text or "").splitlines() if "[Kid" in l] if out is not None else [],
        })
    for c in cases:
        mark = "PASS" if c["result"] == "Passed" else "FAIL"
        print(f"[{mark}] {c['name']}  {c['message']}")
        for line in c["log"]:
            print(f"         {line}")

    os.makedirs(RENDERS, exist_ok=True)
    sheet_src = os.path.join(capture_dir, "kid_unity_sheet.png")
    sheet_dst = os.path.join(RENDERS, f"kid_unity_v{version}_sheet.png")
    if os.path.exists(sheet_src):
        shutil.copyfile(sheet_src, sheet_dst)
        print(f"Sheet: {os.path.relpath(sheet_dst, REPO_ROOT)}")
    report = os.path.join(RENDERS, f"kid_unity_v{version}_tests.json")
    with open(report, "w") as f:
        json.dump({"version": version, "cases": cases}, f, indent=2)
        f.write("\n")
    if any(c["result"] != "Passed" for c in cases):
        sys.exit(f"tests failed; log: {log}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["setup", "build", "test", "all"])
    args = parser.parse_args()
    if args.step in ("setup", "all"):
        setup()
    if args.step in ("build", "all"):
        build()
    if args.step in ("test", "all"):
        test()


if __name__ == "__main__":
    main()
