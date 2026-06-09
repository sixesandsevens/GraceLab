#!/usr/bin/env bash
# GraceLab client installation helper — executed as root via sudoers.
# Called by updater.py after a verified download.
#
# Usage: do-install.sh <version> <tarball_path>
#
# Security:
#   - version must match semver pattern
#   - tarball must be inside /opt/gracelab-client/downloads/
#   - path traversal prevented by realpath check

set -euo pipefail

INSTALL_BASE="/opt/gracelab-client"
DOWNLOADS_DIR="${INSTALL_BASE}/downloads"

VERSION="${1:-}"
TARBALL="${2:-}"

# ---------------------------------------------------------------------------
# Validate arguments
# ---------------------------------------------------------------------------

if [[ -z "$VERSION" || -z "$TARBALL" ]]; then
    echo "Usage: do-install.sh <version> <tarball_path>" >&2
    exit 1
fi

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+[-a-z0-9.]*$ ]]; then
    echo "Invalid version format: $VERSION" >&2
    exit 1
fi

REAL_TARBALL="$(realpath "$TARBALL" 2>/dev/null || true)"
REAL_DOWNLOADS="$(realpath "$DOWNLOADS_DIR")"

if [[ -z "$REAL_TARBALL" || "$REAL_TARBALL" != "${REAL_DOWNLOADS}/"* ]]; then
    echo "Tarball must be inside ${DOWNLOADS_DIR}." >&2
    exit 1
fi

if [[ ! -f "$REAL_TARBALL" ]]; then
    echo "Tarball not found: $REAL_TARBALL" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Extract to release directory
# ---------------------------------------------------------------------------

RELEASE_DIR="${INSTALL_BASE}/releases/${VERSION}"

rm -rf "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR"

tar -xzf "$REAL_TARBALL" -C "$RELEASE_DIR"

# Root owns everything; scripts must be executable
chown -R root:root "$RELEASE_DIR"
find "${RELEASE_DIR}/scripts" -name "*.sh" -exec chmod 755 {} \; 2>/dev/null || true
find "${RELEASE_DIR}/updater" -name "*.sh" -exec chmod 755 {} \; 2>/dev/null || true

# ---------------------------------------------------------------------------
# Atomic symlink swap
# ---------------------------------------------------------------------------

CURRENT_LINK="${INSTALL_BASE}/current"
TMP_LINK="${CURRENT_LINK}.new"

ln -s "$RELEASE_DIR" "$TMP_LINK"
mv -Tf "$TMP_LINK" "$CURRENT_LINK"

echo "Installed gracelab-client ${VERSION} → ${RELEASE_DIR}"
