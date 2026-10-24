#!/bin/bash
# DoubleTake installer for the terminal.
#
# Put this next to the DoubleTake package (doubletake-*.rpm) and run it as the tablet's own
# user:   bash install.sh
# It installs the package, then asks the setup questions (doubletake setup).

set -u
here=$(cd "$(dirname "$0")" && pwd)

if [ -t 1 ]; then B=$'\e[1m'; G=$'\e[32m'; O=$'\e[33m'; D=$'\e[2m'; N=$'\e[0m'; else B= G= O= D= N=; fi
say()  { printf '  %s\n' "$*"; }
good() { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$O" "$N" "$*"; }
stop() { warn "$*"; exit 1; }
yes()  { local a; read -r -p "  $1 [Y/n] " a || exit 1; [[ -z "$a" || "$a" =~ ^[Yy] ]]; }

echo
say "${B}DoubleTake${N}${D}  ·  install${N}"
echo

[ "$(id -u)" = 0 ] && stop "Run this as the tablet's own user, not as root:  bash install.sh"

ID= VERSION_ID= PRETTY_NAME=
[ -r /etc/os-release ] && . /etc/os-release
major=${VERSION_ID%%.*}
case "$ID" in
  rocky|rhel|almalinux)
    [ "$major" = 9 ] || stop "$PRETTY_NAME is not supported. DoubleTake needs Rocky Linux 9, RHEL 9 or AlmaLinux 9."
    pattern='doubletake-*.el9.*.rpm' ;;
  fedora)
    pattern='doubletake-*.fc*.rpm' ;;
  *)
    stop "${PRETTY_NAME:-This system} is not supported. DoubleTake needs Rocky Linux 9, RHEL 9 or AlmaLinux 9 (Fedora: best effort)." ;;
esac
good "System: $PRETTY_NAME"

rpm=$(ls -1 "$here"/$pattern 2>/dev/null | sort -V | tail -n 1)
[ -n "$rpm" ] || stop "No DoubleTake package for this system next to install.sh (looked for $pattern in $here)."
good "Package: $(basename "$rpm")"

if ! id -nG | tr ' ' '\n' | grep -qx wheel; then
  warn "$USER is not an administrator on this tablet, so it cannot install software."
  say  "Make it one, then log out and back in, and run install.sh again:"
  say  "  su -c 'usermod -aG wheel $USER'"
  exit 1
fi

echo
say "The tablet's login password, to install DoubleTake:"
sudo -v -p "  Password for %u: " || stop "sudo did not take the password. Nothing was changed."

if [ "$ID" = rhel ] && ! sudo subscription-manager status >/dev/null 2>&1; then
  echo
  warn "This Red Hat system is not registered, so it cannot download Samba and the other parts DoubleTake needs."
  if yes "Register it now, with your Red Hat account?"; then
    sudo subscription-manager register || stop "Not registered. Nothing was installed."
  else
    stop "Register it first (sudo subscription-manager register), then run install.sh again."
  fi
fi

before=$(rpm -q --qf '%{VERSION}' doubletake 2>/dev/null) || before=""

echo
say "Installing. This downloads what DoubleTake needs, a minute or two."
echo
sudo dnf install -y "$rpm" || stop "The install did not finish (above is why). Nothing of DoubleTake's was set up."
echo
command -v doubletake >/dev/null || stop "The doubletake command is not there after the install."

if [ -n "$before" ]; then
  # An upgrade: the tablet was set up before, and keeps every answer. Only the system part
  # is looked at again, in case the new version needs something more there.
  good "DoubleTake is upgraded (it was $before). Photos, records and settings are kept."
  doubletake system-setup || exit 1
  say "Restart the tablet to start the new version. The questions again, any time: doubletake setup"
  exit 0
fi
good "DoubleTake is installed."
exec doubletake setup
