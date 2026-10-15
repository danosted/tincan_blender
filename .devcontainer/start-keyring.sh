#!/usr/bin/env bash
# Starts a session D-Bus daemon plus gnome-keyring's secret-service component
# so `unity auth login` has somewhere to persist its sign-in token - this
# minimal base image runs neither by default, which surfaces as
# KEYRING_WRITE_FAILED / `unity doctor`'s check.credential-store: fail.
#
# Idempotent and self-healing: run on every container start (postStartCommand)
# AND from ~/.bashrc in every new terminal. It only acts when the persistent
# "login" keyring isn't actually unlocked. Any shell that needs
# `unity auth`/`unity mcp` must `source ~/.keyring-env` first (the .bashrc hook
# does both).
#
# Two failure modes this guards against, both seen as KEYRING_WRITE_FAILED
# even though the keyring file itself is persisted in a named volume:
# - The daemon started from postStartCommand was killed when that command's
#   session ended, because it wasn't detached. setsid fixes that.
# - D-Bus then auto-activates a *fresh* gnome-keyring-daemon on the next
#   secrets request. That one never receives the unlock password, so "login"
#   stays locked while org.freedesktop.secrets still looks present. So the
#   check below asks whether "login" is unlocked, not whether the service exists.
set -uo pipefail

ENV_FILE="${HOME}/.keyring-env"
QUIET="${1:-}"

say() { [ "$QUIET" = "--quiet" ] || echo "$@"; }

login_unlocked() {
    dbus-send --session --print-reply --dest=org.freedesktop.secrets \
        /org/freedesktop/secrets/collection/login \
        org.freedesktop.DBus.Properties.Get \
        string:org.freedesktop.Secret.Collection string:Locked 2>/dev/null \
        | grep -q 'boolean false'
}

bus_alive() {
    dbus-send --session --dest=org.freedesktop.DBus --type=method_call \
        --print-reply /org/freedesktop/DBus org.freedesktop.DBus.ListNames >/dev/null 2>&1
}

# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

if [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ] && bus_alive && login_unlocked; then
    say "Keyring already unlocked (env: $ENV_FILE)"
    exit 0
fi

if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] || ! bus_alive; then
    DBUS_ADDR=$(dbus-daemon --session --fork --print-address)
    export DBUS_SESSION_BUS_ADDRESS="$DBUS_ADDR"
fi
echo "export DBUS_SESSION_BUS_ADDRESS='${DBUS_SESSION_BUS_ADDRESS}'" > "$ENV_FILE"

# The password must be a newline, not empty input: with nothing on stdin the
# daemon treats it as "no password given" and never creates the persistent
# login keyring, so `unity auth login` fails with KEYRING_WRITE_FAILED (only
# the in-memory "session" collection exists, and it isn't the default alias).
# --replace takes over from a locked, D-Bus-activated daemon if one is running.
printf '\n' | setsid gnome-keyring-daemon --replace --unlock --components=secrets 2>/dev/null \
    | grep '^[A-Z_]*=' | sed 's/^/export /' >> "$ENV_FILE"

# shellcheck disable=SC1090
source "$ENV_FILE"
for _ in 1 2 3 4 5; do
    login_unlocked && break
    sleep 0.5
done

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
if ! grep -qF "keyring-heal" "${HOME}/.bashrc" 2>/dev/null; then
    printf '\n# keyring-heal: re-unlock the unity auth keyring if needed, then load its env\n[ -f "%s" ] && bash "%s" --quiet\n[ -f "%s" ] && source "%s"\n' \
        "$SELF" "$SELF" "$ENV_FILE" "$ENV_FILE" >> "${HOME}/.bashrc"
fi

if login_unlocked; then
    say "Keyring unlocked (env: $ENV_FILE)"
else
    echo "WARNING: login keyring is still locked - unity auth login will fail" >&2
    exit 1
fi
