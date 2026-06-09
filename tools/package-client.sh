#!/usr/bin/env bash
# Create a versioned GraceLab client update package.
#
# Usage:
#   sudo ./tools/package-client.sh            # version from gracelab_client.py
#   sudo ./tools/package-client.sh --version 0.2.1
#
# Output:
#   /var/lib/gracelab/updates/gracelab-client-<version>.tar.gz
#   /var/lib/gracelab/updates/gracelab-client-<version>.tar.gz.sha256

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_CLIENT_DIR="${SCRIPT_DIR}/../client"
OUTPUT_DIR="/var/lib/gracelab/updates"

info() { printf '\e[32m[INFO]\e[0m  %s\n' "$*"; }
die()  { printf '\e[31m[ERR]\e[0m   %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "Run as root: sudo ./tools/package-client.sh"

# ---------------------------------------------------------------------------
# Determine version
# ---------------------------------------------------------------------------

VERSION=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version) VERSION="$2"; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

if [[ -z "$VERSION" ]]; then
    VERSION="$(python3 -c "
import re, sys
text = open(sys.argv[1]).read()
m = re.search(r\"CLIENT_VERSION\s*=\s*['\\\"](.+?)['\\\"]\", text)
print(m.group(1) if m else '')
" "${REPO_CLIENT_DIR}/gracelab_client.py" 2>/dev/null || true)"
fi

[[ -n "$VERSION" ]] || die "Could not detect CLIENT_VERSION. Use --version <ver>."

OUTFILE="${OUTPUT_DIR}/gracelab-client-${VERSION}.tar.gz"

info "Packaging GraceLab client v${VERSION}"
info "Source:  ${REPO_CLIENT_DIR}"
info "Output:  ${OUTFILE}"

mkdir -p "$OUTPUT_DIR"

# ---------------------------------------------------------------------------
# Stage files
# ---------------------------------------------------------------------------

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

STAGE="${TMPDIR}/pkg"
mkdir -p "${STAGE}/scripts"
mkdir -p "${STAGE}/updater"

# Core Python files
cp "${REPO_CLIENT_DIR}"/*.py "${STAGE}/"

# Config example
cp "${REPO_CLIENT_DIR}/client_config.ini.example" "${STAGE}/"

# Lifecycle + wrapper scripts
cp "${REPO_CLIENT_DIR}/scripts/"*.sh "${STAGE}/scripts/"
chmod +x "${STAGE}/scripts/"*.sh

# Updater scripts
cp "${REPO_CLIENT_DIR}/updater/"*.py "${STAGE}/updater/" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Create tarball and checksum
# ---------------------------------------------------------------------------

# Invalidate any cached checksum for the same version
rm -f "${OUTFILE}.sha256"

tar -czf "$OUTFILE" -C "$STAGE" .

CHECKSUM="$(sha256sum "$OUTFILE" | cut -d' ' -f1)"
printf '%s  %s\n' "$CHECKSUM" "$(basename "$OUTFILE")" > "${OUTFILE}.sha256"

info "Done."
info "  Package:  ${OUTFILE}"
info "  SHA256:   ${CHECKSUM}"
printf '\n'
printf 'Next step: in Settings → Client Updates, set Stable Version to: %s\n' "$VERSION"
