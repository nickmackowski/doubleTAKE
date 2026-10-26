# DoubleTake Field Station: the package (RPM).
#
# Built by packaging/build-rpm.sh, twice: for Red Hat Enterprise Linux 9 and its rebuilds
# (Rocky, Alma), and for Fedora. Each brings the camera library (python-gphoto2, with
# libgphoto2 inside it) built for that system's Python, so nothing is downloaded when it is
# installed or first started. The el9 package also brings the image and QR code libraries;
# Fedora has its own image library, and the QR code library is pure Python.

%global debug_package %{nil}
%global __os_install_post %{nil}
%global _build_id_links none
%global __brp_python_bytecompile %{nil}
# The bundled libraries carry what they need inside; they ask nothing of the system.
%global __requires_exclude_from ^/usr/lib/doubletake/.*$
%global __provides_exclude_from ^/usr/lib/doubletake/.*$

Name:           doubletake
Version:        %{dt_version}
Release:        1%{?dist}
Summary:        Field photo backup station: two verified copies of every photo
License:        MIT AND GPL-3.0-or-later AND LGPL-2.1-or-later AND HPND AND BSD-3-Clause
URL:            https://github.com/
Source0:        doubletake-%{version}.tar.gz
Source1:        vendor.tar.gz
BuildArch:      x86_64

Requires:       python3 >= 3.9
Requires:       libgphoto2
Requires:       firefox
Requires:       samba
Requires:       samba-common-tools
Requires:       openssh-server
Requires:       policycoreutils-python-utils
Requires:       exfatprogs
Requires:       util-linux
Requires:       sudo
Requires:       NetworkManager
Requires:       grubby
%if 0%{?fedora}
Requires:       python3-pillow
%endif
Recommends:     gnome-kiosk-script-session
Requires:       plymouth-plugin-script
Recommends:     bluez
Recommends:     avahi

%description
DoubleTake Field Station turns a rugged tablet into a photo backup station for the field.
Plug in the camera: every photo is copied to the tablet and checked, then copied again to
a card or drive of DoubleTake's own and checked again. The camera's cards are only called
safe to format when every photo on them is verified in two places. At home, the built-in
NAS brings the photos onto your editing workstation over a read-only share.

Built for the Dell Latitude 7220 Rugged Extreme on Red Hat Enterprise Linux 9.

%prep
%setup -q -n doubletake
mkdir -p vendor && tar -xzf %{SOURCE1} -C vendor

%build
# Nothing to build: Python, and the bundled libraries come ready-made.

%install
rm -rf %{buildroot}
install -d %{buildroot}/usr/share/doubletake
cp -a doubletake run.py %{buildroot}/usr/share/doubletake/
install -d %{buildroot}/usr/share/doubletake/system
cp -a system/plymouth %{buildroot}/usr/share/doubletake/system/
find %{buildroot}/usr/share/doubletake -name __pycache__ -prune -exec rm -rf {} +
install -d %{buildroot}/usr/lib/doubletake
cp -a vendor/. %{buildroot}/usr/lib/doubletake/
install -D -m 0755 packaging/doubletake %{buildroot}/usr/bin/doubletake
install -D -m 0755 system/doubletake-helper %{buildroot}/usr/libexec/doubletake-helper
install -D -m 0644 packaging/doubletake-backlight.service %{buildroot}/usr/lib/systemd/system/doubletake-backlight.service
install -D -m 0644 packaging/doubletake.desktop %{buildroot}/usr/share/applications/doubletake.desktop
install -D -m 0644 packaging/doubletake.png %{buildroot}/usr/share/icons/hicolor/256x256/apps/doubletake.png
install -D -m 0644 packaging/doubletake.svg %{buildroot}/usr/share/icons/hicolor/scalable/apps/doubletake.svg
install -D -m 0644 packaging/doubletake-symbolic.svg %{buildroot}/usr/share/icons/hicolor/symbolic/apps/doubletake-symbolic.svg
T=%{buildroot}/usr/share/plymouth/themes/doubletake
install -d $T
for f in ring.png icon.png wordmark.png wordmark-tron.png watermark.png box.png lock.png entry.png bullet.png; do
    install -m 0644 system/plymouth/$f $T/$f
done
# The script as shipped; the helper writes the one in use from it, for this tablet, so an
# upgrade never puts back a script without the tablet's own line and shutdown timing.
install -m 0644 system/plymouth/doubletake.script $T/doubletake-template.script
touch $T/doubletake.script
install -m 0644 system/plymouth/doubletake.plymouth $T/doubletake-script.plymouth
install -m 0644 system/plymouth/doubletake-two-step.plymouth $T/doubletake-two-step.plymouth
touch $T/doubletake.plymouth

%post
T=/usr/share/plymouth/themes/doubletake
[ -s $T/doubletake.script ] || cp -f $T/doubletake-template.script $T/doubletake.script
# The boot screen: the logo turning to colour needs Plymouth's script plugin; without it,
# the still logo, which borrows the system spinner theme's images.
if [ -f /usr/lib64/plymouth/script.so ] || [ -f /usr/lib/plymouth/script.so ]; then
    cp -f $T/doubletake-script.plymouth $T/doubletake.plymouth
else
    cp -f $T/doubletake-two-step.plymouth $T/doubletake.plymouth
fi
if [ -d /usr/share/plymouth/themes/spinner ]; then
    for image in /usr/share/plymouth/themes/spinner/*.png; do
        name=$(basename "$image")
        [ "$name" = watermark.png ] && continue
        [ -e "$T/$name" ] || cp "$image" "$T/$name"
    done
fi
touch -c /usr/share/icons/hicolor >/dev/null 2>&1 || :
gtk-update-icon-cache -q /usr/share/icons/hicolor >/dev/null 2>&1 || :
# The backlight's fade at start-up, instead of the system's one-step jump.
systemctl daemon-reload >/dev/null 2>&1 || :
systemctl enable doubletake-backlight.service >/dev/null 2>&1 || :
exit 0

%posttrans
# The boot screen's kind, chosen again once the whole install is in: the script plugin may
# have come in the same install, after this package's own step above.
T=/usr/share/plymouth/themes/doubletake
if [ -f /usr/lib64/plymouth/script.so ] || [ -f /usr/lib/plymouth/script.so ]; then
    cmp -s $T/doubletake-script.plymouth $T/doubletake.plymouth || cp -f $T/doubletake-script.plymouth $T/doubletake.plymouth
fi
# An upgrade with the boot screen in use: rebuild the start-up images with the new one.
if [ "$(plymouth-set-default-theme 2>/dev/null)" = doubletake ]; then
    /usr/libexec/doubletake-helper splash refresh >/dev/null 2>&1 || :
else
    /usr/libexec/doubletake-helper splash script >/dev/null 2>&1 || :
fi
exit 0

%preun
if [ $1 -eq 0 ]; then
    # Removing DoubleTake: the NAS off, the boot screen, the kiosk session and the boot menu
    # entry back as they were, and the screens' rule removed. Photos and records are kept.
    /usr/libexec/doubletake-helper reset >/dev/null 2>&1 || :
    systemctl disable doubletake-backlight.service >/dev/null 2>&1 || :
    rm -f /etc/sudoers.d/doubletake
fi
exit 0

%postun
if [ $1 -eq 0 ]; then
    # The spinner images copied in for the still boot screen.
    rm -rf /usr/share/plymouth/themes/doubletake
fi
exit 0

%files
%license LICENSE
%doc README.md CHANGELOG.md docs/USER-GUIDE.md docs/HOW-IT-WORKS.md docs/HISTORY.md
/usr/bin/doubletake
/usr/libexec/doubletake-helper
/usr/lib/systemd/system/doubletake-backlight.service
/usr/share/doubletake
/usr/lib/doubletake
/usr/share/applications/doubletake.desktop
/usr/share/icons/hicolor/256x256/apps/doubletake.png
/usr/share/icons/hicolor/scalable/apps/doubletake.svg
/usr/share/icons/hicolor/symbolic/apps/doubletake-symbolic.svg
%dir /usr/share/plymouth/themes/doubletake
/usr/share/plymouth/themes/doubletake/ring.png
/usr/share/plymouth/themes/doubletake/icon.png
/usr/share/plymouth/themes/doubletake/wordmark.png
/usr/share/plymouth/themes/doubletake/wordmark-tron.png
/usr/share/plymouth/themes/doubletake/watermark.png
/usr/share/plymouth/themes/doubletake/box.png
/usr/share/plymouth/themes/doubletake/lock.png
/usr/share/plymouth/themes/doubletake/entry.png
/usr/share/plymouth/themes/doubletake/bullet.png
/usr/share/plymouth/themes/doubletake/doubletake-template.script
%ghost /usr/share/plymouth/themes/doubletake/doubletake.script
/usr/share/plymouth/themes/doubletake/doubletake-script.plymouth
/usr/share/plymouth/themes/doubletake/doubletake-two-step.plymouth
%ghost /usr/share/plymouth/themes/doubletake/doubletake.plymouth

%changelog
* Mon Oct 26 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0.3c-1
- Start on the home screen begins the backup; the date in the viewer's line
- The boot ring in the start-up logo's own shape; the backlight fades at start-up
- Larger terminal windows; one zip per release

* Sun Oct 25 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0.2.5c-1
- The boot screen's ring sized to meet the maker's logo evenly all the way round

* Sun Oct 25 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0.2c-1
- The viewer's photo details on the photo, one line, hidden with a tap
- The full shoot name on the home screen; every shoot is an import
- The USB drive for the second copy is no longer beta

* Wed Oct 21 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0.1c-1
- The first public release

* Tue Oct 20 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr17-1
- The boot screen's ring sized to the maker's logo itself

* Tue Oct 20 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr16-1
- The boot screen takes over from the maker's logo, in its place

* Tue Oct 20 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr15-1
- The home screen's title with a dot between DoubleTake and Field Station

* Tue Oct 20 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr14-1
- A new boot screen: the icon inside a turning orange and green ring

* Tue Oct 20 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr13-1
- The full boot screen on Fedora: the script plugin required

* Tue Oct 20 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr12-1
- Setup and updates restart the tablet on newer Fedora too

* Mon Oct 19 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr11-1
- The brightness keys work in DoubleTake only too

* Mon Oct 19 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr10-1
- The volume and brightness keys work in DoubleTake only

* Mon Oct 19 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr9-1
- The flicker fix turns off the display's deep idle states too

* Mon Oct 19 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr8-1
- The boot screen no longer follows the tablet's name

* Sun Oct 18 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr7-1
- Updates every 30, 60 or 90 days, or off

* Sun Oct 18 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr6-1
- Setup restarts the tablet again; the boot screen's line checked in the start-up images

* Sun Oct 18 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr5-1
- The switching screen's note on one line

* Sun Oct 18 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr4-1
- The switching screen at a readable pace, with a note about the black screen after it

* Sun Oct 18 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr3-1
- The photo in a mat; upgrades keep the boot screen as tuned for the tablet

* Sun Oct 18 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr2-1
- P1 one-touch backup, backup beeps, P3 tablet status, the switching screen, the flicker fix, a new icon

* Sat Oct 17 2026 DoubleTake <doubletake@users.noreply.github.com> - 1.0~pr1-1
- 1.0 pre-release 1: updates in Maintenance, the system named on the wallpaper, theme names

* Sat Oct 17 2026 DoubleTake <doubletake@users.noreply.github.com> - 0.9.9-1
- The 7220 P keys: dark backup, night red, the screen; Cockpit night red

* Fri Oct 16 2026 DoubleTake <doubletake@users.noreply.github.com> - 0.9.8-1
- The boot screen follows a change of the tablet's name

* Fri Oct 16 2026 DoubleTake <doubletake@users.noreply.github.com> - 0.9.7-1
- The terminal setup restarts the tablet even while the desktop holds a restart off

* Thu Oct 15 2026 DoubleTake <doubletake@users.noreply.github.com> - 0.9.6c-1
- The camera bookkeeping files (Insta360 fileinfo_list.list) never block Safe to Format

* Thu Oct 15 2026 DoubleTake <doubletake@users.noreply.github.com> - 0.9.6-1
- Pictures drawn from the sensor data for DNGs with no preview (Insta360 Ace Pro 2)
- RAW only: the camera JPEG beside a RAW makes its pictures, is not kept, and does not block Safe to Format

* Thu Oct 15 2026 DoubleTake <doubletake@users.noreply.github.com> - 0.9.5c-1
- Pictures for DNG files that keep their preview as a JPEG strip or as plain RGB

* Thu Oct 15 2026 DoubleTake <doubletake@users.noreply.github.com> - 0.9.5-1
- Setup in the terminal only; a folder per import; the camera is never written to

* Thu Oct 15 2026 DoubleTake <doubletake@users.noreply.github.com> - 0.9.4-1
- doubletake setup: the setup questions in the terminal; install.sh

* Thu Oct 15 2026 DoubleTake <doubletake@users.noreply.github.com> - 0.9.3c-1
- Setup works for logins that start with a digit; failed setup is shown in the wizard

* Thu Oct 15 2026 DoubleTake <doubletake@users.noreply.github.com> - 0.9.3-1
- Strict Safe to Format; delete shoots from the library; restart after setup.
* Thu Oct 15 2026 DoubleTake <doubletake@users.noreply.github.com> - 0.9.2-1
- The boot screen on by default; the built-in NAS wording; the GRUB default guard.
* Thu Oct 15 2026 DoubleTake <doubletake@users.noreply.github.com> - 0.9.1-1
- First package: install by double-click, the setup wizard, and the first start.
