#!/bin/bash
# Make the DoubleTake installer image from Rocky Linux's own.
#
#   sudo bash build-iso.sh ~/Downloads/rocky.iso
#   sudo bash build-iso.sh --no-keyboard ~/Downloads/rocky.iso
#
# The DoubleTake package is the one next to this script, or can be named after the image.
#
# --no-keyboard: for a tablet with no keyboard to hand (one USB port, taken by the stick).
# This script asks here, on this computer, what the installer's text step would ask on the
# tablet: the Wi-Fi (such as a phone's hotspot), the tablet's password and the time zone.
# The answers go on the stick, so the tablet installs by itself: it ERASES WHATEVER TABLET IT
# STARTS after a 30-second countdown (pulling the stick out stops it). The stick holds
# the Wi-Fi password in plain text: keep it to yourself, and write the normal installer over
# it when done. The tablet's password goes on it only scrambled (hashed).
#
# Needs a Rocky, RHEL or AlmaLinux 9 computer with mkksiso (sudo dnf install lorax).
# Writes doubletake-<version>-installer.iso next to the Rocky image. Write that to a USB
# stick (Fedora Media Writer, or dd), boot the tablet from it, and follow the installer.

set -eu
# mkksiso makes the boot files as root: ask for sudo here, once.
if [ "$(id -u)" -ne 0 ]; then exec sudo bash "$0" "$@"; fi
here=$(cd "$(dirname "$0")" && pwd)
say()  { printf '  %s\n' "$*"; }
stop() { printf '  ! %s\n' "$*"; exit 1; }

nokeyboard=no
args=()
for a in "$@"; do
  case "$a" in
    --no-keyboard) nokeyboard=yes ;;
    *) args+=("$a") ;;
  esac
done
set -- "${args[@]}"
[ $# -ge 1 ] || stop "Usage: sudo bash build-iso.sh [--no-keyboard] <Rocky 9 ISO> [doubletake .el9 RPM]"
rocky=$(realpath "$1")
if [ $# -ge 2 ]; then
  rpm=$(realpath "$2")
else
  # The package that came in the kit, next to this script.
  found=( "$here"/doubletake-*.el9.*.rpm )
  [ ${#found[@]} -eq 1 ] && [ -f "${found[0]}" ] || stop "Name the DoubleTake package: there is not exactly one next to this script."
  rpm=${found[0]}
fi
[ -f "$rocky" ] || stop "No Rocky image at $1"
[ -f "$rpm" ]   || stop "No DoubleTake package at $2"
case "$(basename "$rpm")" in doubletake-*.el9.*.rpm) ;; *) stop "$2 is not the el9 DoubleTake package." ;; esac
command -v mkksiso >/dev/null || stop "mkksiso is not installed: sudo dnf install lorax"
command -v xorriso >/dev/null || stop "xorriso is not installed: sudo dnf install xorriso"

version=$(rpm -qp --qf '%{VERSION}' "$rpm" 2>/dev/null | tr '~' '-')
out="$(dirname "$rocky")/doubletake-${version}-installer.iso"
[ "$nokeyboard" = yes ] && out="$(dirname "$rocky")/doubletake-${version}-installer-nokeyboard.iso"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir "$work/doubletake"
cp "$rpm" "$work/doubletake/"
install -m 0755 "$here/doubletake-firstboot" "$work/doubletake/"
install -m 0644 "$here/doubletake-firstboot.service" "$work/doubletake/"

# --no-keyboard: the answers, asked here and written to the stick (doubletake/answers.conf).
if [ "$nokeyboard" = yes ]; then
  exec 3<&0
  echo
  say "No keyboard on the tablet: the installer's questions, answered here."
  say "(Nothing typed here is shown on the screen, kept on this computer, or sent anywhere.)"
  echo
  say "1. The Wi-Fi the tablet joins to install (it downloads about 2 GB)."
  say "   For a phone's hotspot, its name as the phone shows it (such as: Nick's iPhone)."
  while true; do
    read -r -u 3 -p "     Wi-Fi name: " wifi_name
    [ -n "$wifi_name" ] && break
  done
  read -r -s -u 3 -p "     Wi-Fi password (Enter if none): " wifi_password; echo
  if [ -n "$wifi_password" ]; then
    read -r -s -u 3 -p "     The same Wi-Fi password again: " again; echo
    [ "$again" = "$wifi_password" ] || stop "The two Wi-Fi passwords are not the same. Nothing was made."
    [ "${#wifi_password}" -ge 8 ] || stop "A Wi-Fi password has at least 8 characters. Nothing was made."
  fi
  echo
  say "2. The tablet's password (its user is named from its serial number, as usual)."
  while true; do
    read -r -s -u 3 -p "     Password: " pw1; echo
    read -r -s -u 3 -p "     The same password again: " pw2; echo
    if [ "$pw1" != "$pw2" ]; then say "   Those two are not the same. Again."; continue; fi
    if [ "${#pw1}" -lt 6 ]; then say "   At least 6 characters, please. Again."; continue; fi
    break
  done
  hash=$(printf '%s' "$pw1" | openssl passwd -6 -stdin 2>/dev/null) || hash=""
  pw1="" pw2=""
  [ "${hash:0:3}" = '$6$' ] || stop "Could not scramble the password (openssl). Nothing was made."
  echo
  say "3. The time zone."
  read -r -u 3 -p "     Time zone [America/New_York, Eastern]: " zone
  zone=${zone:-America/New_York}
  [[ "$zone" =~ ^[A-Za-z0-9_+/-]+$ ]] && [ -f "/usr/share/zoneinfo/$zone" ] || stop "\"$zone\" is not a time zone (such as America/New_York). Nothing was made."
  exec 3<&-
  for value in "$wifi_name" "$wifi_password"; do
    case "$value" in *$'\n'*|*$'\r'*) stop "A line break in an answer. Nothing was made." ;; esac
  done
  ( umask 077
    { printf 'WIFI_NAME=%s\n' "$wifi_name"
      printf 'WIFI_PASSWORD=%s\n' "$wifi_password"
      printf 'PASSWORD_HASH=%s\n' "$hash"
      printf 'ZONE=%s\n' "$zone"
    } > "$work/doubletake/answers.conf" )
  wifi_password="" hash=""
  echo
  say "Wi-Fi:      $wifi_name"
  say "Time zone:  $zone"
  say "This stick erases ANY tablet it starts, 30 seconds after the countdown shows."
  echo
fi

# The boot menu: "Install DoubleTake", starting by itself after 5 seconds, without the
# media check first.
xorriso -osirrox on -indev "$rocky" -extract /EFI/BOOT/grub.cfg "$work/grub.cfg" >/dev/null 2>&1 \
  || stop "Could not read the boot menu from $(basename "$rocky")"
# "Rocky Linux 9.8" in the menu; from 9.9, "Rocky Linux Minimal 9.9".
rocky_name=$(grep -oE "Rocky Linux (Minimal )?[0-9][0-9.]*" "$work/grub.cfg" | head -1)
rocky_version=$(printf '%s' "$rocky_name" | grep -oE "[0-9][0-9.]*$")
if [ -z "$rocky_name" ]; then
  grep -E "menuentry|search|set (default|timeout)" "$work/grub.cfg" | head -20 >&2
  stop "$(basename "$rocky") does not look like a Rocky Linux 9 image."
fi
menu=(-R "$rocky_name" "DoubleTake" -R "Test this media & install" "Check this stick and install")
grep -q 'set default="1"' "$work/grub.cfg" && menu+=(-R 'set default="1"' 'set default="0"')
grep -q 'set timeout=60' "$work/grub.cfg" && menu+=(-R 'set timeout=60' 'set timeout=5')

# The installer's DoubleTake look: its name, sidebar and colours, as an updates image the
# installer lays over its own files when it starts (inst.updates). The version stays
# Rocky's: the installer takes its package release from it ("DoubleTake on Rocky Linux
# 9.8 installation").
command -v cpio >/dev/null || stop "cpio is not installed: sudo dnf install cpio"
mkdir "$work/look"
cp -a "$here/branding/." "$work/look/"
# A Mac that copied this kit (onto a stick or a share) leaves hidden ._ files beside each
# file, and .DS_Store: the installer reads every file in its settings folder, so a
# ._doubletake.conf stops it at start ("can't decode byte ... in position 37"). Not taken.
find "$work/look" "$work/doubletake" \( -name '._*' -o -name '.DS_Store' \) -exec rm -f {} +
cat > "$work/look/.buildstamp" <<EOF
[Main]
Product=DoubleTake on Rocky Linux
Version=${rocky_version}
BugURL=https://github.com/nickmackowski/doubleTAKE/issues
IsFinal=True
UUID=$(date +%Y%m%d%H%M).x86_64
Variant=
EOF
( cd "$work/look" && find . | cpio --quiet -o -H newc | gzip -9 ) > "$work/doubletake-look.img"

say "Rocky image:  $(basename "$rocky")"
say "DoubleTake:   $(basename "$rpm")"
say "Making:       $(basename "$out")"
rm -f "$out"
mkksiso --help 2>&1 | grep -q -- '--replace' || stop "This mkksiso is too old: sudo dnf update lorax"
mkksiso --ks "$here/doubletake.ks" -a "$work/doubletake" -u "$work/doubletake-look.img" "${menu[@]}" "$rocky" "$out"
say "Done: $out"
if [ "$nokeyboard" = yes ]; then
  say "This one holds your Wi-Fi password: keep it to yourself, and write the normal"
  say "installer over the stick once the tablet is installed."
fi
