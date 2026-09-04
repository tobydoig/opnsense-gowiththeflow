#!/bin/sh
# Builds os-gowiththeflow-<version>.pkg from ../src, using this directory's
# +MANIFEST and pkg-plist. Must run on a FreeBSD/OPNsense box with pkg(8)
# (pkg create doesn't cross-build) -- run it on the target box itself, or
# on a VM with a matching OS/ABI. Not a real ports build: no poudriere,
# no ports tree required, just a straight `pkg create -m -p` staged root.
#
# Usage: sh build-pkg.sh [outdir]

set -e

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
SRC="${SCRIPT_DIR}/../src"
OUT="${1:-${SCRIPT_DIR}/../dist}"
WORK=$(mktemp -d /tmp/gowiththeflow-pkg.XXXXXX)
trap 'rm -rf "$WORK"' EXIT

VERSION=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "${SCRIPT_DIR}/+MANIFEST" | head -1)
ARCH=$(uname -m)

STAGE="${WORK}/root"
META="${WORK}/meta"
mkdir -p "$STAGE" "$META" "$OUT"

mkdir -p \
    "${STAGE}/usr/local/opnsense/mvc/app/controllers/OPNsense" \
    "${STAGE}/usr/local/opnsense/mvc/app/models/OPNsense" \
    "${STAGE}/usr/local/opnsense/mvc/app/views/OPNsense" \
    "${STAGE}/usr/local/opnsense/scripts" \
    "${STAGE}/usr/local/opnsense/service/templates/OPNsense" \
    "${STAGE}/usr/local/opnsense/service/conf/actions.d" \
    "${STAGE}/usr/local/etc/rc.d" \
    "${STAGE}/usr/local/etc/inc/plugins.inc.d" \
    "${STAGE}/usr/local/opnsense/version"

cp -R "${SRC}/opnsense/mvc/app/controllers/OPNsense/GoWithTheFlow" \
    "${STAGE}/usr/local/opnsense/mvc/app/controllers/OPNsense/"
cp -R "${SRC}/opnsense/mvc/app/models/OPNsense/GoWithTheFlow" \
    "${STAGE}/usr/local/opnsense/mvc/app/models/OPNsense/"
cp -R "${SRC}/opnsense/mvc/app/views/OPNsense/GoWithTheFlow" \
    "${STAGE}/usr/local/opnsense/mvc/app/views/OPNsense/"
cp -R "${SRC}/opnsense/scripts/gowiththeflow" \
    "${STAGE}/usr/local/opnsense/scripts/"
cp -R "${SRC}/opnsense/service/templates/OPNsense/GoWithTheFlow" \
    "${STAGE}/usr/local/opnsense/service/templates/OPNsense/"
cp "${SRC}/opnsense/service/conf/actions.d/actions_gowiththeflow.conf" \
    "${STAGE}/usr/local/opnsense/service/conf/actions.d/"
cp "${SRC}/etc/rc.d/gowiththeflow" \
    "${STAGE}/usr/local/etc/rc.d/"
cp "${SRC}/etc/inc/plugins.inc.d/gowiththeflow.inc" \
    "${STAGE}/usr/local/etc/inc/plugins.inc.d/"

# never ship a stale bytecode cache picked up from a local dev run
find "$STAGE" -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Every script actions_gowiththeflow.conf invokes by bare path (relying
# on its own shebang + the exec bit, not an explicit interpreter prefix)
# needs this set explicitly -- git tracks all of these as mode 644 (no
# executable bit), so whatever made block_host.py/block_rules.py work
# despite that was incidental state on a specific build machine, not
# anything this script actually guaranteed. Caught live: recategorize.py
# failed configd's "Execute error" the first time it shipped, for
# exactly this reason.
chmod +x \
    "${STAGE}/usr/local/etc/rc.d/gowiththeflow" \
    "${STAGE}/usr/local/opnsense/scripts/gowiththeflow/gowiththeflowd.py" \
    "${STAGE}/usr/local/opnsense/scripts/gowiththeflow/block_host.py" \
    "${STAGE}/usr/local/opnsense/scripts/gowiththeflow/block_rules.py" \
    "${STAGE}/usr/local/opnsense/scripts/gowiththeflow/recategorize.py"

sed -e "s/%%VERSION%%/${VERSION}/" -e "s/%%ARCH%%/${ARCH}/" \
    "${SCRIPT_DIR}/version.json.tmpl" > "${STAGE}/usr/local/opnsense/version/gowiththeflow"

cp "${SCRIPT_DIR}/pkg-plist" "${META}/plist"
cp "${SCRIPT_DIR}/+MANIFEST" "${META}/+MANIFEST"

pkg create -m "$META" -p "${META}/plist" -r "$STAGE" -o "$OUT" -v

echo "Built: ${OUT}/os-gowiththeflow-${VERSION}.pkg"
