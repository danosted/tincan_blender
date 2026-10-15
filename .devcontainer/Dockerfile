FROM mcr.microsoft.com/devcontainers/base:bookworm

ARG BLENDER_VERSION=5.2.2
ARG BLENDER_MAJOR=5.2

# Libraries Blender links against even in --background (headless) mode -
# without these it fails to start at all, with no GUI ever involved.
# libegl1 specifically: a *fresh* headless Blender process can end up probing
# an EGL context (observed once a Wayland/X11 display-forwarding socket from
# the devcontainer environment became live) even with no GPU present - without
# it that probe hard-crashes (SIGABRT) instead of falling back gracefully. An
# already-running Blender process is unaffected since it keeps its
# already-created context, which is why this went unnoticed until a fresh
# `blender --background` run failed partway through this project's life.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xz-utils \
        libxi6 \
        libxrender1 \
        libxrandr2 \
        libxfixes3 \
        libxcursor1 \
        libxinerama1 \
        libsm6 \
        libgl1 \
        libegl1 \
        libxkbcommon0 \
    && rm -rf /var/lib/apt/lists/*

# Blender - official Linux build. Pin the version explicitly and bump it
# deliberately, so the container doesn't silently drift from whatever
# version people are running locally.
RUN curl -fsSL "https://download.blender.org/release/Blender${BLENDER_MAJOR}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
        -o /tmp/blender.tar.xz \
    && mkdir -p /opt/blender \
    && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 \
    && rm /tmp/blender.tar.xz \
    && ln -s /opt/blender/blender /usr/local/bin/blender

# uv - launches the blender-mcp stdio server (via `uvx`) and is generally
# useful for any Python-side CLI tooling in scripts/cli/. Installed to
# /usr/local/bin (already on PATH for every user) rather than a per-user
# ~/.local/bin, since these RUN steps execute as root but the container
# runs as the non-root `vscode` user at runtime.
ENV UV_INSTALL_DIR=/usr/local/bin
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# The base image only carries python3-minimal, which deliberately strips out
# much of the standard library (json included) to save space - fine for the
# base image's own use, but scripts/cli/*.py (publish_asset.py etc.) need a
# normal stdlib. Pull in the real python3 package instead of assuming it's
# already complete.
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*

# Shared, world-writable Blender config/extensions location so the addon
# installed below (as root, during build) is visible no matter which user
# (root or the non-root `vscode` user devcontainers run as) ends up running
# Blender at container-start time.
ENV BLENDER_USER_RESOURCES=/opt/blender-config
RUN mkdir -p "${BLENDER_USER_RESOURCES}" && chmod -R 0777 "${BLENDER_USER_RESOURCES}"

# Unity CLI (unity-cli, not the GUI Hub) - lets the container inspect/install
# Unity Editor versions, sign in, and drive `unity mcp` for live Editor
# control, mirroring the Blender setup above.
RUN curl -sL https://hub.unity3d.com/linux/keys/public | gpg --dearmor -o /usr/share/keyrings/Unity_Technologies_ApS.gpg \
    && printf 'Types: deb\nURIs: https://hub.unity3d.com/linux/repos/deb\nSuites: unstable\nComponents: main\nSigned-By: /usr/share/keyrings/Unity_Technologies_ApS.gpg\n' \
        > /etc/apt/sources.list.d/unity-cli.sources \
    && apt-get update && apt-get install -y --no-install-recommends unity-cli \
    && rm -rf /var/lib/apt/lists/*

# `unity auth login` stores its session in a D-Bus secret-service keyring,
# which this minimal base image doesn't run - without it, sign-in completes
# in the browser but fails to persist (KEYRING_WRITE_FAILED / `unity doctor`
# reports check.credential-store: fail). gnome-keyring provides that service;
# dbus-x11 provides `dbus-daemon`/`dbus-launch` to host the session bus it
# registers on. The daemon itself is started at container-start time (it's a
# running process, not something that belongs in an image layer) by
# start-keyring.sh.
# xvfb: a virtual display, so batchmode Unity can render the play-mode test
# captures (scripts/cli/character_lab.py test).
RUN apt-get update && apt-get install -y --no-install-recommends \
        gnome-keyring \
        dbus-x11 \
        xvfb \
        xauth \
    && rm -rf /var/lib/apt/lists/*

# Install and enable the official Blender MCP add-on (Blender Lab extensions
# repository) so the MCP bridge server is available the moment Blender runs
# in this container - mirrors the local dev machine setup.
RUN blender --background --online-mode --command extension repo-add lab_blender_org --url https://lab.blender.org/ \
    && blender --background --online-mode --command extension install lab_blender_org.mcp --sync --enable \
    && chmod -R a+rwX "${BLENDER_USER_RESOURCES}"

# Mount points for the Unity/keyring/Claude named volumes in devcontainer.json.
# Docker copies a mount point's ownership into a *new, empty* named volume;
# without these the volumes are created root-owned, the `vscode` user can't
# write to them, and Unity (editor, sign-in, license) silently vanishes after
# a rebuild with fresh volumes.
RUN mkdir -p /home/vscode/Unity /home/vscode/.config/unityhub /home/vscode/.config/unity3d \
        /home/vscode/.local/share/unity3d /home/vscode/.local/share/keyrings \
        /home/vscode/.claude \
    && chown -R vscode:vscode /home/vscode/Unity /home/vscode/.config /home/vscode/.local \
        /home/vscode/.claude

WORKDIR /workspace
