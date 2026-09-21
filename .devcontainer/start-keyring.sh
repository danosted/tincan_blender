#!/usr/bin/env bash
# Starts a session D-Bus daemon plus gnome-keyring's secret-service component
# so `unity auth login` has somewhere to persist its sign-in token - this
# minimal base image runs neither by default, which surfaces as
# KEYRING_WRITE_FAILED / `unity doctor`'s check.credential-store: fail. Run on
# every container start (postStartCommand); guarded so it doesn't spawn a
# second session if one from an earlier start is still alive. Any shell that
# needs `unity auth`/`unity mcp` must `source ~/.keyring-env` first - this is
# also appended to ~/.bashrc so interactive terminals pick it up for free.
set -euo pipefail

ENV_FILE="${HOME}/.keyring-env"

if [ -f "$ENV_FILE" ] && (
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    dbus-send --session --dest=org.freedesktop.DBus --type=method_call \
        --print-reply /org/freedesktop/DBus org.freedesktop.DBus.ListNames \
        2>/dev/null | grep -q org.freedesktop.secrets
); then
    echo "Keyring session already running (env: $ENV_FILE)"
    exit 0
fi

DBUS_ADDR=$(dbus-daemon --session --fork --print-address)
echo "export DBUS_SESSION_BUS_ADDRESS='${DBUS_ADDR}'" > "$ENV_FILE"

# shellcheck disable=SC1090
source "$ENV_FILE"
echo -n "" | gnome-keyring-daemon --unlock --components=secrets 2>&1 \
    | grep '^[A-Z_]*=' | sed 's/^/export /' >> "$ENV_FILE"

if ! grep -qF "$ENV_FILE" "${HOME}/.bashrc" 2>/dev/null; then
    printf '\n# unity auth/mcp keyring session (see .devcontainer/start-keyring.sh)\n[ -f "%s" ] && source "%s"\n' \
        "$ENV_FILE" "$ENV_FILE" >> "${HOME}/.bashrc"
fi

echo "Keyring session started (env: $ENV_FILE)"
