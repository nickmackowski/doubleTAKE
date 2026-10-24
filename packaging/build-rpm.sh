#!/usr/bin/env bash
# Builds the DoubleTake packages (RPMs):
#   doubletake-<version>-1.el9.x86_64.rpm   Red Hat Enterprise Linux 9, Rocky 9, Alma 9 (Python 3.9)
#   doubletake-<version>-1.fc.x86_64.rpm    Fedora 41 to 43 (Python 3.13 and 3.14)
# Needs rpmbuild and pip, and the internet once, to fetch the camera library for each Python.
# Usage: packaging/build-rpm.sh [output folder]
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(dirname "$HERE")"
OUT="${1:-$PROJECT/dist}"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$PROJECT/doubletake/__init__.py")"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT"

# The source: the project as it is, without caches.
tar --exclude=__pycache__ --exclude=./dist --exclude=./.git --transform "s,^\.,doubletake," -czf "$WORK/doubletake-$VERSION.tar.gz" -C "$PROJECT" .

vendor() {  # vendor <name> <python version> <packages...>: the libraries, installed for that Python
    local name="$1" py="$2"; shift 2
    local tag="cp${py/./}"
    mkdir -p "$WORK/vendor-$name/$tag"
    python3 -m pip install --quiet --root-user-action=ignore --no-deps --only-binary=:all: --python-version "$py" \
        --implementation cp --abi "$tag" \
        --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 \
        --target "$WORK/vendor-$name/$tag" "$@"
    find "$WORK/vendor-$name/$tag" -name __pycache__ -prune -exec rm -rf {} +
    rm -rf "$WORK/vendor-$name/$tag/bin"
}

build() {  # build <name> <dist> <rpm defines...>
    local name="$1" dist="$2"; shift 2
    local top="$WORK/rpm-$name"
    mkdir -p "$top"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}
    cp "$WORK/doubletake-$VERSION.tar.gz" "$top/SOURCES/"
    tar -czf "$top/SOURCES/vendor.tar.gz" -C "$WORK/vendor-$name" .
    rpmbuild --quiet -bb "$HERE/doubletake.spec" --define "_topdir $top" --define "dt_version $VERSION" \
        --define "dist $dist" "$@" --nodeps
    find "$top/RPMS" -name '*.rpm' -exec cp {} "$OUT/" \;
}

# DT_TARGETS="el9" builds only that one.
for target in ${DT_TARGETS:-el9 fc}; do
    case "$target" in
        el9)
            vendor el9 3.9 "gphoto2>=2.5" "pillow>=9" "segno>=1.5"
            build el9 .el9 --define "rhel 9" ;;
        fc)
            vendor fc 3.13 "gphoto2>=2.5" "segno>=1.5"
            vendor fc 3.14 "gphoto2>=2.5" "segno>=1.5"
            build fc .fc --define "fedora 42" ;;
        *) echo "Unknown target: $target (el9 or fc)" >&2; exit 1 ;;
    esac
done

ls -la "$OUT"/doubletake-"$VERSION"-*.rpm
