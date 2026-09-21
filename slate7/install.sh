#!/bin/sh
# AxisBridge Slate 7 installer. BusyBox ash / POSIX sh compatible.
set -eu
umask 077
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CHECK=0
OFFLINE=0
PORT=8080
BIND=0.0.0.0
for arg in "$@"; do
    case "$arg" in
        --check) CHECK=1 ;;
        --offline) OFFLINE=1 ;;
        --port=*) PORT=${arg#*=} ;;
        --bind=*) BIND=${arg#*=} ;;
        --help|-h)
            printf 'Usage: sh install.sh [--check] [--offline] [--port=8080] [--bind=0.0.0.0]\n'
            printf 'Run --check first for a read-only preflight. Existing service settings survive upgrades.\n'
            exit 0 ;;
        *) printf 'Unknown option: %s\n' "$arg" >&2; exit 1 ;;
    esac
done
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[ -f /etc/openwrt_release ] || fail 'This installer requires GL.iNet OpenWrt firmware.'
MODEL=''
for file in /tmp/sysinfo/model /tmp/sysinfo/board_name; do
    if [ -r "$file" ]; then MODEL="$MODEL $(cat "$file")"; fi
done
if [ -r /proc/device-tree/model ]; then
    MODEL="$MODEL $(tr -d '\000' < /proc/device-tree/model)"
fi
case "$MODEL" in
    *GL-BE3600*|*gl-be3600*|*GL_BE3600*|*gl_be3600*) ;;
    *) fail "Expected Slate 7 GL-BE3600; detected:$MODEL" ;;
esac
[ "$(id -u)" -eq 0 ] || fail 'Log in to the router as root.'
[ -r /lib/functions/procd.sh ] || fail 'OpenWrt procd support was not found.'
command -v uci >/dev/null 2>&1 || fail 'OpenWrt UCI is required.'
command -v sha256sum >/dev/null 2>&1 || fail 'sha256sum is required to verify this archive.'
case "$PORT" in ''|*[!0-9]*) fail 'Port must be a number between 1024 and 65535.' ;; esac
[ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || fail 'Choose a port between 1024 and 65535.'
printf 'Detected:%s\n' "$MODEL"
printf 'Firmware: '; sed -n "s/^DISTRIB_DESCRIPTION=//p" /etc/openwrt_release
(cd "$BASE" && sha256sum -c SHA256SUMS >/dev/null) || fail 'Archive checksum mismatch. Extract a fresh copy.'
FREE_KB=$(df -Pk /usr/share | awk 'END {print $4}')
case "$FREE_KB" in ''|*[!0-9]*) fail 'Could not determine writable storage.' ;; esac
printf 'Available storage: %s KiB\n' "$FREE_KB"
[ "$FREE_KB" -ge 12288 ] || fail 'At least 12 MiB free storage is required for app staging and rollback.'
if [ "$CHECK" -eq 0 ]; then
    mkdir /var/lock/axisbridge-install 2>/dev/null || fail 'Another install is running, or /var/lock/axisbridge-install is stale.'
    trap 'rmdir /var/lock/axisbridge-install 2>/dev/null || true' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
fi
PY_OK=0
if command -v python3 >/dev/null 2>&1; then
    if python3 "$BASE/check_runtime.py"; then PY_OK=1; fi
fi
if [ "$PY_OK" -eq 0 ]; then
    [ "$FREE_KB" -ge 65536 ] || fail 'Allow at least 64 MiB free before installing Python dependencies.'
    if command -v opkg >/dev/null 2>&1; then PM=opkg
    elif command -v apk >/dev/null 2>&1; then PM=apk
    else fail 'Neither opkg nor apk is available to install Python.'; fi
    if [ "$CHECK" -eq 1 ]; then
        printf 'ACTION NEEDED: compatible Python is missing; install would request python3 through %s.\n' "$PM"
        printf 'Preflight complete. No changes made. Package availability is checked during installation.\n'
        exit 2
    fi
    [ "$OFFLINE" -eq 0 ] || fail 'Offline install requires a working Python 3.10+ runtime already installed.'
    printf 'Installing python3 from this firmware\047s configured package feeds...\n'
    if [ "$PM" = opkg ]; then
        opkg update || fail 'Feed refresh failed. Restore internet/package-feed access and retry.'
        opkg install python3 || fail 'Python installation failed. Keep the firmware-matched feeds; do not force incompatible packages.'
    else
        apk update || fail 'Feed refresh failed. Restore internet/package-feed access and retry.'
        apk add python3 || fail 'Python installation failed. Check this firmware\047s package feeds.'
    fi
    python3 "$BASE/check_runtime.py" || fail 'The available Python is incompatible or incomplete; a Python 3.10+ firmware package is required.'
fi
if [ "$CHECK" -eq 1 ]; then
    python3 "$BASE/deploy.py" check --port "$PORT" --bind "$BIND"
    printf 'Preflight passed. No changes made.\n'
    exit 0
fi
python3 "$BASE/deploy.py" install --port "$PORT" --bind "$BIND"
