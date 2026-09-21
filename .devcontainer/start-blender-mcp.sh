#!/usr/bin/env bash
# Starts the Blender MCP bridge server in the background, headless, so it's
# already listening on :9876 the moment the container is up - no GUI, no
# manual "open Blender" step. Run on every container start (postStartCommand)
# so a container restart doesn't leave the bridge down; guarded so it doesn't
# spawn a second instance if one's already listening.
set -euo pipefail

if (exec 3<>/dev/tcp/127.0.0.1/9876) 2>/dev/null; then
    exec 3<&- 3>&-
    echo "Blender MCP bridge already running on :9876"
    exit 0
fi

nohup blender --background --online-mode --command blender_mcp \
    > /tmp/blender-mcp-bridge.log 2>&1 &
disown

echo "Blender MCP bridge starting in the background (log: /tmp/blender-mcp-bridge.log)"
