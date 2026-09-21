# Convenience wrapper: Blender isn't on PATH on this machine, so scripts and
# muscle-memory can just call `.\tools\blender.ps1 <args>` instead of the
# full install path.
#
# Examples:
#   .\tools\blender.ps1                                            # open the GUI (MCP bridge auto-starts)
#   .\tools\blender.ps1 --background --command blender_mcp         # headless MCP bridge server
#   .\tools\blender.ps1 --background file.blend --python scripts/cli/scene_info.py

$blenderExe = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"

if (-not (Test-Path $blenderExe)) {
    Write-Error "Blender not found at '$blenderExe'. Update the path in tools/blender.ps1 if it was reinstalled elsewhere or upgraded."
    exit 1
}

& $blenderExe @args
