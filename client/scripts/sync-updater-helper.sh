#!/usr/bin/env bash
# Keep the root-owned updater helper (/opt/gracelab-client/updater/do-install.sh)
# in step with the current release.
#
# updater.py always runs the helper at that fixed path (it's what sudoers
# grants), and a release installs into releases/<version>/ — so without this,
# the helper stays at whatever version the station was provisioned with and
# fixes to it never reach deployed stations.
#
# Called as root by updater/do-install.sh (end of every install) and by the
# lifecycle hooks via gl_ensure_updater_helper (which is also how stations
# still running a pre-sync helper pick this up). Idempotent. NOT in sudoers.
#
# Safety:
#   - no-op if the release doesn't ship a helper (e.g. rolled back to an older
#     release) — the installed one is kept rather than removed
#   - the candidate must pass `bash -n`, so a broken helper can't brick updates
#   - replaced via rename, never rewritten in place: bash reads scripts
#     incrementally, and this runs from inside do-install.sh itself

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${SCRIPT_DIR}/../updater/do-install.sh"
DST="/opt/gracelab-client/updater/do-install.sh"

if [[ $EUID -ne 0 ]]; then
    echo "sync-updater-helper.sh must run as root" >&2
    exit 1
fi

if [[ ! -f "$SRC" ]]; then
    echo "No updater helper in this release — keeping ${DST}"
    exit 0
fi

if [[ -f "$DST" ]] && cmp -s "$SRC" "$DST"; then
    exit 0
fi

if ! bash -n "$SRC"; then
    echo "Release updater helper failed syntax check — keeping ${DST}" >&2
    exit 1
fi

mkdir -p "$(dirname "$DST")"
install -m 755 -o root -g root "$SRC" "${DST}.new"
mv -f "${DST}.new" "$DST"
echo "Updater helper updated → ${DST}"
