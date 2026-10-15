"""Build the review page for an asset from its build manifests.

Every build script writes renders/review/<asset>/v<version>/manifest.json plus
one JPEG per review panel (see build_kid.py's Panels). A Unity capture run may
add renders/review/<asset>/v<version>/unity/manifest.json the same way. This
script folds the newest few versions into one self-contained HTML page:
panels grouped as the builder grouped them, each tagged with the code that
produced it, the build's checks, and a note box per panel.

Notes live in the published page's database (collection "feedback"), not in
the HTML, so republishing a new build keeps them. Claude reads them back with
the Artifact tool's read_db and answers by setting `status`/`reply` on a note.

Plain python3, no Blender needed:
    python3 scripts/cli/review_board.py --asset kid_base
Writes renders/review/<asset>/board.html; publish it with the Artifact tool
(the URL is recorded in renders/review/<asset>/ARTIFACT_URL).
"""

import argparse
import base64
import glob
import json
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review_board.html")
MAX_VERSIONS = 4  # each version embeds ~1 MB of JPEGs


def semver_key(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def load_version(vdir):
    panels, checks, meta = [], [], None
    for sub in ("", "unity"):
        path = os.path.join(vdir, sub, "manifest.json")
        if not os.path.isfile(path):
            continue
        with open(path) as f:
            m = json.load(f)
        meta = meta or m
        for p in m["panels"]:
            with open(os.path.join(vdir, sub, p["image"]), "rb") as f:
                img = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
            panels.append({**{k: v for k, v in p.items() if k != "image"}, "img": img, "stage": m["stage"]})
        checks += [{**c, "stage": m["stage"]} for c in m["checks"]]
    if meta is None:
        return None
    return {
        "version": meta["version"],
        "built_at": meta["built_at"],
        "git": meta["git"],
        "numbers": meta.get("numbers", {}),
        "exported": meta.get("exported", True),
        "panels": panels,
        "checks": checks,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", default="kid_base")
    ap.add_argument("--title", default="Kid Dailies")
    args = ap.parse_args()

    root = os.path.join(REPO_ROOT, "renders", "review", args.asset)
    vdirs = sorted(glob.glob(os.path.join(root, "v*")), key=lambda d: semver_key(os.path.basename(d)))
    versions = [v for v in (load_version(d) for d in vdirs[-MAX_VERSIONS:]) if v]
    if not versions:
        raise SystemExit(f"no manifests under {root}")
    versions.reverse()  # newest first
    board = {"asset": args.asset, "title": args.title, "versions": versions}

    with open(TEMPLATE) as f:
        html = f.read()
    data = json.dumps(board, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace("__TITLE__", args.title).replace("/*__BOARD__*/null", data)
    out = os.path.join(root, "board.html")
    with open(out, "w") as f:
        f.write(html)
    print(f"{os.path.relpath(out, REPO_ROOT)}: {len(versions)} version(s), "
          f"{sum(len(v['panels']) for v in versions)} panels, {len(html) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
