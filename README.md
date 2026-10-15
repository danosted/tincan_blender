# Blender — TinCan 3D Asset Pipeline

Blender MCP + CLI enabled project for building 3D models for **TinCan**, the
cooperative airship-crew game in the sibling `tincan_unity` repo.

**Current goal:** see [GOALS.md](GOALS.md) — building a proper, multi-level,
navigable player airship to replace the current placeholder ship prop.
Setting/style reference: [docs/airship-design.md](docs/airship-design.md).
Concrete scale/fixture constraints pulled from the game:
[docs/unity-integration.md](docs/unity-integration.md).
Shipyard parts (modular 1 m grid hull pieces) follow
[docs/part-contract.md](docs/part-contract.md); open questions for the Unity
side are in [docs/unity-requirements.md](docs/unity-requirements.md).

## Blender MCP setup

Two pieces, connected over `localhost:9876`:

1. **Blender add-on** (`MCP`, from the official `lab.blender.org` extensions
   repository) — runs inside Blender. Auto-starts its bridge server when
   Blender opens (Blender Preferences → Add-ons → MCP, if you ever need to
   check/change host, port, or auto-start).
2. **MCP server** (`blender-mcp`, from
   `projects.blender.org/lab/blender_mcp`) — the process Claude Code actually
   talks to over stdio; it relays to the add-on's TCP bridge. Needs `uv` on
   `PATH` to run via `uvx`.

Registered as a **project-scoped MCP server** in [.mcp.json](.mcp.json), so it
works the same regardless of machine/OS — no per-host `claude mcp add` needed.
Claude Code will ask you to approve it the first time you run `claude` in this
repo (`⏸ Pending approval` in `claude mcp list` until then — normal, not a
problem).

**To use it locally:** install `uv` once (`winget install astral-sh.uv` /
`curl -LsSf https://astral.sh/uv/install.sh | sh` — see
[uv's docs](https://docs.astral.sh/uv/) for other platforms), make sure the
Blender MCP add-on above is installed and enabled, then just open Blender
normally (the bridge auto-starts) and ask Claude to work in the scene —
inspect objects, run `bpy` code, build/adjust geometry, etc.

**To use it in the devcontainer:** nothing to do — see below, it's already
wired up.

If `claude mcp list` ever shows the `blender` server disconnected, it almost
always means Blender isn't running (nothing listening on port 9876).

## Devcontainer (OS-independent setup)

`.devcontainer/` builds a Linux container with Blender, `uv`, and the MCP
add-on already installed and enabled — so the whole workflow above works
identically whether you're on Windows, macOS, or Linux. Open this folder in
VS Code and "Reopen in Container" (or use the
[Dev Containers CLI](https://github.com/devcontainers/cli) /
[GitHub Codespaces](https://docs.github.com/en/codespaces)).

What it sets up:
- Blender (official Linux build, version pinned in
  [.devcontainer/Dockerfile](.devcontainer/Dockerfile)) plus the headless
  runtime libraries it needs even without a GUI.
- The MCP add-on, installed and enabled at image build time via Blender's own
  extension CLI (`blender --command extension ...`) — same add-on as the local
  setup above, just provisioned non-interactively.
- `uv`, on `PATH` for every user (not a per-user install), so `.mcp.json`'s
  plain `uvx` command works unchanged inside the container.
- The MCP bridge server itself, headless (`blender --background --command
  blender_mcp`), auto-started on container start by
  [.devcontainer/start-blender-mcp.sh](.devcontainer/start-blender-mcp.sh).
  There's no GUI in the container at all — Claude drives the scene entirely
  through the MCP tools (`bpy` execution, etc.), the same way it would in
  headless CLI mode locally. Open the resulting `.blend` file in your own
  local Blender install to actually look at it.

## CLI (headless Blender)

For scripted, repeatable steps that don't need a human driving — validation,
export, batch operations. On the host, Blender isn't on `PATH`, so use the
wrapper:

```powershell
.\tools\blender.ps1 --background path\to\file.blend --python scripts\cli\scene_info.py
```

In the devcontainer, `blender` is already on `PATH` directly:

```bash
blender --background path/to/file.blend --python scripts/cli/scene_info.py
```

`scripts/cli/` holds these headless automation scripts. `scene_info.py` is a
starting example (prints the collection/object tree of a `.blend`). Add more
here as real needs come up (export, validation, thumbnailing) — don't
pre-build a pipeline for exports that don't exist yet.

## Layout

```
.devcontainer/    OS-independent container setup (Blender + MCP + uv, pre-provisioned)
.mcp.json         project-scoped MCP server registration (works on host and in-container)
blend/            .blend source files (tracked in git)
exports/          staged export output (FBX/glTF), gitignored — regenerate, don't hand-edit
scripts/cli/      headless Blender automation (blender --background --python ...)
tools/            local dev convenience scripts (e.g. the Blender wrapper)
docs/             design reference + integration notes
GOALS.md          current project goal, kept up to date as work progresses
```

## Getting exports into the game

Follow `tincan_unity`'s existing asset convention (see
[docs/unity-integration.md](docs/unity-integration.md)): export to
`exports/<Group>/<Asset>/`, then copy into
`tincan_unity/Assets/Models/<Group>/<Asset>/` and wrap in a prefab there.
Raw models never go directly into a Unity scene.
