#!/usr/bin/env bash
# DoubleTake - setup for the tablet (Fedora, or Red Hat Enterprise Linux 9).
#
# What this does:
#   1. Installs any missing system packages with dnf (asks for your password if so).
#   2. Creates a private Python environment and installs the camera library into it.
#   3. Stops the desktop from grabbing the camera when it is plugged in.
#   4. Installs the program in ~/DoubleTake and the "doubletake" command.
#   5. Moves photos and records from an earlier version's ~/FieldPhotos, if asked.
#   6. Sets up the NAS (Samba and SFTP, read-only, off until switched on), with a
#      small helper in /usr/local/libexec that the screens may run without a password.
#
# Photos live in ~/Pictures/DoubleTake and records in ~/Documents/DoubleTake.
# Installing a new version replaces only ~/DoubleTake, never those two.
# It does not touch disks, partitions, the camera's card, or any remote server.
# Safe to run more than once. To upgrade, run it from the unpacked download.

set -Eeuo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${DOUBLETAKE_APP_DIR:-$HOME/DoubleTake}"
APP_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/doubletake"
VENV="$APP_HOME/venv"
BIN="$HOME/.local/bin"
ASSUME_YES=0
# For testing this script on a machine that is not the tablet.
SKIP_SYSTEM="${DOUBLETAKE_SKIP_SYSTEM:-0}"

for arg in "$@"; do
    case "$arg" in
        -y|--yes) ASSUME_YES=1 ;;
        -h|--help)
            sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

say()  { printf '\n==> %s\n' "$*"; }
fail() { printf '\nSetup stopped: %s\n' "$*" >&2; exit 1; }

trap 'fail "a step failed (line $LINENO). Nothing else was changed after that point."' ERR

if [[ $EUID -eq 0 && "$SKIP_SYSTEM" != "1" ]]; then
    fail "run this as your normal user, not with sudo. It asks for your password when it needs it."
fi

if [[ "$SKIP_SYSTEM" != "1" ]]; then
    [[ -r /etc/os-release ]] || fail "cannot tell which operating system this is."
    # shellcheck disable=SC1091
    source /etc/os-release
    case " ${ID:-} ${ID_LIKE:-} " in
        *" fedora "*|*" rhel "*) ;;
        *) fail "this script is written for Fedora and Red Hat Enterprise Linux. This machine reports '${PRETTY_NAME:-unknown}'." ;;
    esac
    echo "DoubleTake setup on ${PRETTY_NAME}"
else
    echo "DoubleTake setup (system steps skipped)"
fi
echo "Installing from: $PROJECT"
echo "Program folder:  $APP_DIR"

if pgrep -f "doubletake gui" >/dev/null 2>&1 || pgrep -f "fieldstation gui" >/dev/null 2>&1; then
    fail "the screens are open. Press Exit on the home screen first, then run setup.sh again."
fi

if [[ $ASSUME_YES -ne 1 ]]; then
    read -r -p "Install the camera tools and the doubletake command? [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] || { echo "Nothing was changed."; exit 0; }
fi

if [[ "$SKIP_SYSTEM" != "1" ]]; then
    say "Checking system packages"
    # Only ask dnf for what is actually missing. A Red Hat system that is not
    # registered has no software repositories, and usually has all of this already.
    missing=()
    have_python=1
    command -v python3 >/dev/null 2>&1 || { missing+=(python3); have_python=0; }
    if [[ $have_python -eq 1 ]] && ! python3 -c "import venv, ensurepip" >/dev/null 2>&1; then
        missing+=(python3-pip)
        have_python=0
    fi
    # libgphoto2 brings the rules that let a normal user talk to a camera over USB.
    have_rules=1
    rpm -q libgphoto2 >/dev/null 2>&1 || { missing+=(libgphoto2); have_rules=0; }

    if [[ ${#missing[@]} -eq 0 ]]; then
        echo "Everything the engine needs is already installed."
    else
        echo "Installing: ${missing[*]} (you may be asked for your password)"
        if sudo dnf install -y "${missing[@]}"; then
            have_python=1
            have_rules=1
        else
            echo
            echo "dnf could not install them. On Red Hat Enterprise Linux this usually means the"
            echo "system is not registered. To register it:  sudo subscription-manager register"
            if [[ $have_python -ne 1 ]]; then
                fail "Python 3 with its venv module is needed and could not be installed."
            fi
            if [[ $have_rules -ne 1 ]]; then
                echo "Carrying on without libgphoto2. The camera may not be reachable by a normal"
                echo "user until it is installed. 'doubletake doctor' will show whether it is."
            fi
        fi
    fi
fi

# DoubleTake was called Field Station before 0.8.1. Its settings and its own folder (the Python
# environment, the screens' browser profile) move to the new names first, so nothing has to
# be downloaded again. The photos, records and the rest move further down.
OLD_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/fieldstation"
NEW_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/doubletake"
OLD_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/fieldstation"
RENAMED=0
if [[ -d "$OLD_CONFIG" && ! -e "$NEW_CONFIG" ]]; then
    say "Moving the settings over from Field Station, DoubleTake's earlier name"
    mv "$OLD_CONFIG" "$NEW_CONFIG"
    RENAMED=1
fi
if [[ -d "$OLD_HOME" && ! -e "$APP_HOME" ]]; then
    mv "$OLD_HOME" "$APP_HOME"
    RENAMED=1
fi

say "Creating the Python environment in $VENV"
mkdir -p "$APP_HOME" "$BIN"
if [[ ! -x "$VENV/bin/python" ]]; then
    python3 -m venv "$VENV"
fi

# Libraries already in place are kept, so an upgrade also works offline in the field.
say "Installing the camera library"
if ! "$VENV/bin/python" -c "import gphoto2" >/dev/null 2>&1; then
    "$VENV/bin/python" -m pip install --quiet --upgrade pip || true
    if ! "$VENV/bin/python" -m pip install --quiet "gphoto2>=2.5"; then
        fail "could not download the camera library. Check that the tablet is online, then run setup.sh again."
    fi
fi
"$VENV/bin/python" -c "import gphoto2 as gp; print('Camera library', gp.__version__, 'with libgphoto2', gp.gp_library_version(gp.GP_VERSION_SHORT)[0])"

say "Installing the image library for the photo viewer"
if "$VENV/bin/python" -c "import PIL" >/dev/null 2>&1 || "$VENV/bin/python" -m pip install --quiet "pillow>=9"; then
    "$VENV/bin/python" -c "import PIL; print('Image library', PIL.__version__)"
else
    echo "Could not download the image library. Backups still work; the photo viewer shows no pictures"
    echo "until setup.sh is run again with the tablet online."
fi

say "Installing the QR code library, for sending photos to a phone"
if "$VENV/bin/python" -c "import segno" >/dev/null 2>&1 || "$VENV/bin/python" -m pip install --quiet "segno>=1.5"; then
    "$VENV/bin/python" -c "import segno; print('QR code library', segno.__version__)"
else
    echo "Could not download the QR code library. Everything else works; Send to phone shows the address"
    echo "to type in instead, until setup.sh is run again with the tablet online."
fi

if [[ "$SKIP_SYSTEM" != "1" ]]; then
    say "Stopping the desktop from grabbing the camera"
    # GNOME mounts a camera as soon as it is plugged in, which blocks every other program.
    if systemctl --user list-unit-files gvfs-gphoto2-volume-monitor.service 2>/dev/null | grep -q gvfs-gphoto2; then
        systemctl --user mask --now gvfs-gphoto2-volume-monitor.service || true
        gio mount -s gphoto2 2>/dev/null || true
        echo "Done. To undo: systemctl --user unmask gvfs-gphoto2-volume-monitor.service"
    else
        echo "The desktop's camera grabber is not installed. Nothing to do."
    fi

    say "Checking Bluetooth, for a keyboard or mouse"
    if [[ -n "$(ls -A /sys/class/bluetooth 2>/dev/null)" ]]; then
        if ! rpm -q bluez >/dev/null 2>&1; then
            sudo dnf install -y bluez || echo "Could not install Bluetooth. Everything else works without it."
        fi
        if rpm -q bluez >/dev/null 2>&1; then
            sudo systemctl enable --now bluetooth.service || true
            echo "Bluetooth is ready. Pair devices in the screens: Settings, Bluetooth."
        fi
    else
        echo "This tablet has no Bluetooth adapter switched on. Nothing to do."
    fi

    say "Installing the DoubleTake boot screen"
    # Installed here, switched on in the screens: Settings, Display and theme, Boot screen.
    # The logo appears in black and white and turns to colour, which needs Plymouth's script
    # plugin. Without it the logo shows in colour, still. The still version borrows the
    # spinner and password images from the system's own spinner theme.
    theme=/usr/share/plymouth/themes/doubletake
    if [[ -d /usr/share/plymouth/themes/spinner ]] && command -v plymouth-set-default-theme >/dev/null 2>&1; then
        script_plugin=""
        for dir in /usr/lib64/plymouth /usr/lib/plymouth /usr/lib/x86_64-linux-gnu/plymouth; do
            [[ -f "$dir/script.so" ]] && script_plugin="$dir/script.so"
        done
        if [[ -z "$script_plugin" ]] && sudo dnf install -y plymouth-plugin-script >/dev/null 2>&1; then
            for dir in /usr/lib64/plymouth /usr/lib/plymouth; do
                [[ -f "$dir/script.so" ]] && script_plugin="$dir/script.so"
            done
        fi
        sudo mkdir -p "$theme"
        for image in /usr/share/plymouth/themes/spinner/*.png; do
            [[ "$(basename "$image")" == watermark.png ]] && continue
            sudo install -o root -g root -m 0644 "$image" "$theme/"
        done
        P="$PROJECT/system/plymouth"
        sudo install -o root -g root -m 0644 "$P/watermark.png" "$P/ring.png" "$P/icon.png" \
            "$P/wordmark.png" "$P/wordmark-tron.png" \
            "$P/box.png" "$theme/"
        sudo install -o root -g root -m 0644 "$P/doubletake.script" "$theme/doubletake-template.script"
        [[ -s "$theme/doubletake.script" ]] || sudo install -o root -g root -m 0644 "$P/doubletake.script" "$theme/doubletake.script"
        if [[ -n "$script_plugin" ]]; then
            # Its own lock, entry and bullet images for the disk password, over the spinner's.
            sudo install -o root -g root -m 0644 "$P/lock.png" "$P/entry.png" "$P/bullet.png" "$theme/"
            sudo install -o root -g root -m 0644 "$P/doubletake.plymouth" "$theme/doubletake.plymouth"
            echo "Installed: the logo turns from black and white to colour."
        else
            sudo install -o root -g root -m 0644 "$P/doubletake-two-step.plymouth" "$theme/doubletake.plymouth"
            echo "Installed, as a still logo (Plymouth's script plugin is not available here)."
        fi
        if [[ "$(plymouth-set-default-theme 2>/dev/null)" == doubletake ]]; then
            echo "The boot screen is on: the start-up images are rebuilt with the new one in a moment."
            NEW_SPLASH=1
        fi
        echo "Switch it on in the screens: Settings, Display and theme."
    else
        echo "This system has no spinner boot theme to build on. Nothing to do."
    fi

    say "Checking the kiosk session, for DoubleTake only"
    # GNOME Kiosk runs one full-screen app with no desktop. It is off until switched on
    # in the screens: Settings, Device, DoubleTake only.
    if rpm -q gnome-kiosk-script-session >/dev/null 2>&1; then
        echo "The kiosk session is installed."
    elif sudo dnf install -y gnome-kiosk gnome-kiosk-script-session; then
        echo "Installed. Switch it on in the screens: Settings, Device, DoubleTake only."
    else
        echo "Could not install the kiosk session. Everything else works without it."
    fi
fi

say "Installing the program in $APP_DIR"
if [[ "$PROJECT" != "$APP_DIR" ]]; then
    if [[ -d "$APP_DIR" && ! -f "$APP_DIR/doubletake/__init__.py" && -n "$(ls -A "$APP_DIR" 2>/dev/null)" ]]; then
        fail "$APP_DIR already exists and is not DoubleTake. Move it aside, then run setup.sh again."
    fi
    # Build the new copy beside the old one, then swap, so a failure leaves the old one working.
    rm -rf "$APP_DIR.new" "$APP_DIR.old"
    mkdir -p "$APP_DIR.new"
    (cd "$PROJECT" && tar --exclude=__pycache__ -cf - .) | (cd "$APP_DIR.new" && tar -xf -)
    if [[ -d "$APP_DIR" ]]; then mv "$APP_DIR" "$APP_DIR.old"; fi
    mv "$APP_DIR.new" "$APP_DIR"
    rm -rf "$APP_DIR.old"
fi
echo "Program version $(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$APP_DIR/doubletake/__init__.py")"

say "Installing the doubletake command"
cat > "$BIN/doubletake" <<EOF
#!/usr/bin/env bash
# Installed by setup.sh. Runs DoubleTake from $APP_DIR
export PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$VENV/bin/python" "$APP_DIR/run.py" "\$@"
EOF
chmod +x "$BIN/doubletake"
echo "Installed: $BIN/doubletake"
# An entry in the desktop's app list, so the screens can be opened again after Exit.
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
cat > "$APPS/doubletake.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DoubleTake
Comment=Camera backup and photo library
Exec=$BIN/doubletake gui
Icon=camera-photo
Terminal=false
Categories=Graphics;Photography;
EOF
echo "Added DoubleTake to the app list."

if [[ $RENAMED -eq 1 || -d "$HOME/FieldStation" || -d "$HOME/Pictures/FieldStation" || -d "$HOME/Documents/FieldStation" ]]; then
    say "Moving photos, records and the desktop's entries over from Field Station"
    "$BIN/doubletake" migrate-names || echo "Some things could not be moved over. The lines above say which."
    # The program's old folder, only when it is the old program.
    if [[ -f "$HOME/FieldStation/fieldstation/__init__.py" ]]; then
        rm -rf "$HOME/FieldStation"
    fi
fi

say "Checking for photos from an earlier version"
"$BIN/doubletake" move-data --check && found=0 || found=$?
if [[ $found -eq 10 ]]; then
    move=1
    if [[ $ASSUME_YES -ne 1 ]]; then
        read -r -p "Move them? Nothing is copied, so this is instant. [Y/n] " answer
        if [[ "$answer" =~ ^[Nn]$ ]]; then move=0; fi
    fi
    if [[ $move -eq 1 ]]; then
        "$BIN/doubletake" move-data
    else
        "$BIN/doubletake" move-data --keep-old
    fi
elif [[ $found -eq 11 ]]; then
    echo "WARNING: the earlier photos were not moved, and the app will not see them until they are."
fi
"$BIN/doubletake" config >/dev/null
PHOTOS_DIR="$("$BIN/doubletake" config | sed -n 's/^  library: //p')"
RECORDS_DIR="$("$BIN/doubletake" config | sed -n 's/^  data_dir: //p')"
mkdir -p "$PHOTOS_DIR" "$RECORDS_DIR"

if [[ "$SKIP_SYSTEM" != "1" ]]; then
    say "Setting up the NAS: the shared photos folder and SFTP"
    # Samba shares the folder with your computer, read-only. The NAS stays off until it is
    # switched on in the screens, and only opens on trusted networks.
    nas_missing=()
    for pkg in samba samba-common-tools policycoreutils-python-utils exfatprogs; do
        rpm -q "$pkg" >/dev/null 2>&1 || nas_missing+=("$pkg")
    done
    nas_ok=1
    if [[ ${#nas_missing[@]} -gt 0 ]]; then
        echo "Installing: ${nas_missing[*]} (you may be asked for your password)"
        if ! sudo dnf install -y "${nas_missing[@]}"; then
            # exfatprogs is only for formatting the card; the NAS works without it.
            if ! rpm -q samba >/dev/null 2>&1 || ! rpm -q samba-common-tools >/dev/null 2>&1; then
                nas_ok=0
                echo "Samba could not be installed, so the NAS is skipped. Everything else works."
                echo "On Red Hat this usually means the system is not registered."
            fi
        fi
    fi
    if [[ $nas_ok -eq 1 ]]; then
        HELPER=/usr/local/libexec/doubletake-helper
        sudo install -o root -g root -m 0755 "$APP_DIR/system/doubletake-helper" "$HELPER"
        # Let this user run the helper, and nothing else, without a password.
        rule="$(mktemp)"
        printf '# DoubleTake: the screens switch the NAS on and off through this helper.\n%s ALL=(root) NOPASSWD: %s\n' "$USER" "$HELPER" > "$rule"
        if sudo visudo -cf "$rule" >/dev/null; then
            sudo install -o root -g root -m 0440 "$rule" /etc/sudoers.d/doubletake
        else
            echo "Could not write the sudo rule for the helper. The NAS switches will not work."
        fi
        rm -f "$rule"
        # The NAS, the password and the boot screen, from the name Field Station.
        OLD_HELPER=/usr/local/libexec/fieldstation-helper
        if [[ -e "$OLD_HELPER" || -e /etc/fieldstation || -d /usr/share/plymouth/themes/fieldstation ]]; then
            echo "Moving the NAS, the password and the boot screen over from Field Station."
            echo "If the boot screen is on, this rebuilds the start-up images: a few minutes."
            sudo "$HELPER" migrate-old-names || echo "Not everything could be moved over. The line above says why."
            sudo rm -f "$OLD_HELPER" /etc/sudoers.d/fieldstation
        fi
        if [[ "${NEW_SPLASH:-0}" == 1 ]]; then
            echo "Rebuilding the start-up images with the new boot screen (a few minutes)."
            sudo "$HELPER" splash refresh >/dev/null || echo "The boot screen could not be rebuilt. Switch it off and on in Settings, Display."
        fi
        # A way to the desktop that does not need the screens: F8 at power-on, in the boot menu.
        echo "Adding DoubleTake maintenance (desktop) to the boot menu."
        if sudo "$HELPER" boot-entry on >/dev/null; then
            echo "Done. If the screens ever stop answering, tap F8 (Fn+F8) at the Dell logo and choose it."
        else
            echo "The boot menu entry could not be added; the boot menu was left as it was."
        fi
        # The boot screen, on by default on a fresh install; a choice made before is kept.
        if [[ "${NEW_SPLASH:-0}" != 1 ]]; then
            echo "Switching the DoubleTake boot screen on (a few minutes; switch it off in Settings, Display)."
            sudo "$HELPER" splash default >/dev/null || echo "The boot screen could not be switched on. Try Settings, Display."
        fi
        if sudo "$HELPER" install --library "$PHOTOS_DIR"; then
            echo "Done. Switch the NAS on in the screens: Settings, NAS."
        else
            echo "The NAS setup did not finish. The message above says why."
        fi
    fi
fi

case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "Note: $BIN is not on your PATH in this terminal. Open a new terminal, or run $BIN/doubletake directly." ;;
esac

say "Setup complete"
cat <<EOF
    Program:  $APP_DIR
    Photos:   $PHOTOS_DIR
    Records:  $RECORDS_DIR

The folder you ran setup.sh from is no longer needed and can be deleted.

Next, with the D850 plugged in and switched on:

    doubletake doctor     check everything is ready
    doubletake gui        open the touch screens

README.md explains each step, and the commands for working without the screens.
EOF
