# DoubleTake installer, based on Rocky Linux 9.
#
# build-iso.sh puts this into Rocky's own installer image, with the DoubleTake package and
# the installer's DoubleTake look beside it. Then, on one plain text screen at the start:
#
#   1. Wi-Fi: a numbered list of networks, type the number and the password. Skipped when
#      the tablet is already online: any wired link with a cable (a USB-C adapter too) is
#      switched on first. Rocky's packages come from the internet.
#   2. The tablet's user: a name (Enter for the service tag, as in the tablet's name; else
#      doubletake) and a password, twice.
#   3. The time zone, found from the network; else a short list (Eastern, Central...), with
#      the time in each, in 12 hours. Network time is on either way.
#   4. The drive it will erase (the tablet's own, never a USB drive or a card): type ERASE.
#
# A stick made with build-iso.sh --no-keyboard carries those answers (doubletake/answers.conf):
# then nothing is asked, on any tablet. It erases after a 30-second countdown (pulling the
# stick out or holding the power button stops it; a key on a keyboard goes to the ERASE
# question). Nick's call (2026-11-20): no model check, the FZ-M1 has no keyboard to type ERASE.
#
# With all of that known the installer has nothing to ask, so it installs by itself (in
# DoubleTake's look): GNOME, Firefox, the kiosk session, the DoubleTake package, and a
# first-start step that sets the tablet up as DoubleTake's (doubletake-firstboot). If
# anything was left unknown (no drive found, no time zone), its summary screen asks.
#
# On the first start DoubleTake opens on its welcome walk-through: the DoubleTake password,
# what to copy, Copy 2, the NAS, the hotspot, start-up.

graphical
lang en_US.UTF-8
keyboard --xlayouts='us'
eula --agreed
firstboot --disable
rootpw --lock
selinux --enforcing
firewall --enabled
# Quiet from the very first start: firmware notices (harmless on Dell tablets) stay off the screen.
bootloader --timeout=1 --append="rhgb quiet loglevel=3 rd.udev.log_level=3 vt.global_cursor_default=0"
reboot --eject

# Rocky's packages, from the internet: the Wi-Fi step (the first %pre) comes before this.
url --mirrorlist="https://mirrors.rockylinux.org/mirrorlist?arch=x86_64&repo=BaseOS-9"
repo --name=AppStream --mirrorlist="https://mirrors.rockylinux.org/mirrorlist?arch=x86_64&repo=AppStream-9"

# Written by the %pre steps below. Anything they could not settle, the installer asks.
%include /tmp/doubletake-disk.ks
%include /tmp/doubletake-user.ks
%include /tmp/doubletake-time.ks
%include /tmp/doubletake-name.ks
# Network time (chrony), whatever the time zone.
services --enabled=chronyd

# No crash dumps: they keep part of the tablet's memory aside for nothing DoubleTake uses.
%addon com_redhat_kdump --disable
%end

%packages
@^workstation-product-environment
firefox
gnome-kiosk-script-session
plymouth-plugin-script
-gnome-initial-setup
%end

%pre --log=/tmp/doubletake-pre.log
#!/bin/bash
# The tablet's name on a network: dt-<Dell service tag> (dt-7abc123); on other makes,
# dt-<the last 6 of the serial number>; a short random code when there is no serial to read.
# (A dash: host names may not have underscores.)
serial=$(cat /sys/class/dmi/id/product_serial 2>/dev/null | tr -cd 'A-Za-z0-9' | tr 'A-Z' 'a-z')
vendor=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null)
case "$serial" in
  ""|tobefilledbyoem*|defaultstring|systemserialnumber|none|notapplicable|notspecified|unknown|0123456789|123456789*) serial="" ;;
esac
[ -n "$(printf '%s' "$serial" | tr -d '0')" ] || serial=""
if [ -n "$serial" ] && [[ "$vendor" == Dell* ]] && [ "${#serial}" -le 10 ]; then
  code=$serial
elif [ -n "$serial" ]; then
  code=${serial: -6}
fi
# The same code is the tablet's user name, unless it is all numbers (a user name may not be).
if [ -n "$serial" ] && [[ "$code" =~ [a-z] ]]; then
  echo "$code" > /tmp/doubletake-tag.txt
else
  rm -f /tmp/doubletake-tag.txt
fi
if [ -z "$serial" ]; then
  code=$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')
fi
echo "network --hostname=dt-$code" > /tmp/doubletake-name.ks
echo "dt-$code" > /tmp/doubletake-name.txt
echo "name: dt-$code"

# The tablet's own drive: an NVMe or SATA drive, else a built-in eMMC (it has boot0
# partitions; a microSD card has none). Never a USB drive, never a card. The biggest of
# those, if there are several. Nothing sure: no line, and the installer asks.
best="" bestsize=0
while read -r line; do
  NAME= TYPE= TRAN= RM= SIZE=
  eval "$line"
  [ "$TYPE" = disk ] || continue
  [ "$RM" = 1 ] && continue
  case "$TRAN" in
    nvme|sata|ata) ;;
    "") case "$NAME" in /dev/mmcblk*) [ -e "${NAME}boot0" ] || continue ;; *) continue ;; esac ;;
    *) continue ;;
  esac
  if [ "$SIZE" -gt "$bestsize" ]; then best=$NAME; bestsize=$SIZE; fi
done < <(lsblk -dbnpP -o NAME,TYPE,TRAN,RM,SIZE)
if [ -n "$best" ]; then
  name=${best#/dev/}
  cat > /tmp/doubletake-disk.ks <<EOF
ignoredisk --only-use=$name
clearpart --all --initlabel --drives=$name
# The usual layout, under DoubleTake's own names: the boot partitions this tablet needs
# (EFI and /boot), then one LVM group "doubletake": swap, / a fixed 25 GB (Rocky and DoubleTake
# take about 10, room for years of updates), /home all the rest: the photo library lives there.
# (Until 1.1.2c / grew to 70 GB, which left a 128 GB drive only 43 GB for photos.)
reqpart --add-boot
part pv.doubletake --size=1 --grow --ondisk=$name
volgroup doubletake pv.doubletake
logvol swap --vgname=doubletake --name=swap --recommended
logvol / --vgname=doubletake --name=root --fstype=xfs --size=25600
logvol /home --vgname=doubletake --name=home --fstype=xfs --size=10240 --grow
EOF
  # For the ERASE question: what the drive is, as a person would know it.
  model=$(lsblk -dno MODEL "$best" 2>/dev/null | sed 's/ *$//')
  echo "${model:-the internal drive} ($best), $((bestsize / 1000000000)) GB" > /tmp/doubletake-disk.txt
  echo "drive: $best ($bestsize bytes)"
else
  : > /tmp/doubletake-disk.ks
  rm -f /tmp/doubletake-disk.txt
  echo "drive: none sure, the installer asks"
fi
%end

%pre --interpreter=/usr/bin/bash --log=/tmp/doubletake-ask.log
# The one text screen: Wi-Fi, the user, the time zone, and ERASE. Everything it settles is
# written as a small kickstart file that the main script %includes.
: > /tmp/doubletake-user.ks
: > /tmp/doubletake-time.ks

# ---- A stick made for a tablet with no keyboard (build-iso.sh --no-keyboard) ----
# Its answers are read once, here, and the stick it came on is noted: pulling that stick
# out during the countdown stops the install before anything is erased.
answers_file=""
for dir in /run/install/repo /run/install/sources/mount-0000-cdrom /run/install/isodir; do
  [ -f "$dir/doubletake/answers.conf" ] && answers_file="$dir/doubletake/answers.conf" && break
done
if [ -z "$answers_file" ]; then
  mkdir -p /mnt/doubletake-answers
  for dev in /dev/sr* /dev/sd*; do
    [ -b "$dev" ] || continue
    mount -o ro "$dev" /mnt/doubletake-answers 2>/dev/null || continue
    if [ -f /mnt/doubletake-answers/doubletake/answers.conf ]; then
      answers_file=/mnt/doubletake-answers/doubletake/answers.conf; break
    fi
    umount /mnt/doubletake-answers
  done
fi
A_WIFI_NAME="" A_WIFI_PASSWORD="" A_PASSWORD_HASH="" A_ZONE="" stick=""
if [ -n "$answers_file" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    key=${line%%=*} value=${line#*=}
    case "$key" in
      WIFI_NAME) A_WIFI_NAME=$value ;;
      WIFI_PASSWORD) A_WIFI_PASSWORD=$value ;;
      PASSWORD_HASH) [[ "$value" =~ ^\$6\$[A-Za-z0-9./\$]+$ ]] && A_PASSWORD_HASH=$value ;;
      ZONE) [[ "$value" =~ ^[A-Za-z0-9_+/-]+$ ]] && [ -f "/usr/share/zoneinfo/$value" ] && A_ZONE=$value ;;
    esac
  done < "$answers_file"
  # The stick: the disk under the answers (/dev/sda1 or /dev/sda: sda).
  source_dev=$(findmnt -no SOURCE --target "$answers_file" 2>/dev/null | head -1)
  if [ -b "$source_dev" ]; then
    stick=$(lsblk -no PKNAME "$source_dev" 2>/dev/null | head -1)
    [ -n "$stick" ] || stick=$(basename "$source_dev")
    case "$stick" in loop*|dm-*|zram*) stick="" ;; esac  # not a stick that can be pulled out
    [ -n "$stick" ] && [ -e "/sys/block/$stick" ] || stick=""
  fi
  echo "answers: $answers_file (stick: ${stick:-not known})"
fi
# A no-keyboard stick erases by itself (after the countdown) on whatever tablet it starts.
product=$(cat /sys/class/dmi/id/product_name 2>/dev/null)
auto_erase() { [ -n "$answers_file" ] && [ -n "$A_PASSWORD_HASH" ]; }
# A phone's hotspot: "Nick's iPhone" typed with a plain ' is shown as Nick’s iPhone. The name
# seen nearby that is the same, apostrophes and capitals aside.
same_name() { printf '%s' "$1" | sed "s/\xe2\x80\x99/'/g; s/\xe2\x80\x98/'/g" | tr '[:upper:]' '[:lower:]'; }
wifi_seen() {
  local want line name
  want=$(same_name "$1")
  while IFS= read -r line; do
    name=${line//\\:/:}
    [ -n "$name" ] || continue
    if [ "$name" = "$1" ] || [ "$(same_name "$name")" = "$want" ]; then printf '%s' "$name"; return 0; fi
  done < <(nmcli -t -f SSID device wifi list --rescan no 2>/dev/null)
  return 1
}
stick_gone() { [ -n "$stick" ] && [ ! -e "/sys/block/$stick" ]; }
# The countdown's seconds, five lines tall and centred, to read from arm's length.
big_number() {
  local glyphs=("######   ##   ##   ######" "  #   ##    #    #   ### " "#####    #######    #####" "#####    # ####    ######"
    "#   ##   ######    #    #" "######    #####    ######" "######    ######   ######"
    "#####    #   #   #    #  " "######   #######   ######" "######   ######    ######")
  local digits=$1 row i d line pad
  pad=$(( (80 - ${#digits} * 7) / 2 ))
  for row in 0 1 2 3 4; do
    line=$(printf '%*s' "$pad" '')
    for ((i = 0; i < ${#digits}; i++)); do
      d=${digits:i:1}
      line+="${glyphs[d]:row*5:5}  "
    done
    echo "$line"
  done
}
# The stick was pulled out: never go on to the erase. With the stick gone the installer's own
# programs may not start any more, so: turn off; else the kernel's own power off; else this
# step stops here for good (the installer waits on it, and erases nothing).
stopped() {
  title "stopped"
  echo "  The stick was pulled out: nothing was erased."
  echo "  The tablet turns off in 10 seconds. (If it does not: hold the power button.)"
  sleep 10
  poweroff -f
  echo o > /proc/sysrq-trigger 2>/dev/null
  kill -STOP $$
  while :; do read -r -t 3600 _ </dev/null; done
}

# Online: NetworkManager says so, or (when it cannot tell) a route out and Rocky's mirror
# list found by name.
online() {
  case "$(nmcli -t networking connectivity check 2>/dev/null)" in
    full) return 0 ;;
    none|portal|limited) return 1 ;;
  esac
  ip route show default 2>/dev/null | grep -q . &&
    python3 -c 'import socket; socket.getaddrinfo("mirrors.rockylinux.org", 443)' 2>/dev/null
}
title() { clear; echo; echo "  DoubleTake installer: $1"; echo; }
# Any wired link (a dock, a USB-C adapter) is switched on: the installer leaves some off.
# Without a cable it simply stays off.
wired_up() {
  local dev type state
  while IFS=: read -r dev type state; do
    [ "$type" = ethernet ] || continue
    case "$state" in connected*|connecting*) continue ;; esac
    nmcli device set "$dev" managed yes >/dev/null 2>&1
    nmcli device set "$dev" autoconnect yes >/dev/null 2>&1
    (nmcli --wait 20 device connect "$dev" >/dev/null 2>&1 &)
  done < <(nmcli -t -f DEVICE,TYPE,STATE device 2>/dev/null)
}
# The time now in a time zone, in 12 hours (9:41 PM).
clock() { TZ=$1 date '+%-I:%M %p' 2>/dev/null; }
ZONES=(America/New_York America/Chicago America/Denver America/Los_Angeles America/Anchorage Pacific/Honolulu)
LABELS=(Eastern Central Mountain Pacific Alaska Hawaii)
# The time zone from a short list, or typed (Europe/London).
ask_zone() {
  local pick typed i
  while true; do
    title "time zone"
    echo "  Which time zone is this tablet in?"
    echo
    for i in "${!ZONES[@]}"; do
      printf "   %d   %-10s %s\n" "$((i + 1))" "${LABELS[$i]}" "$(clock "${ZONES[$i]}")"
    done
    echo
    echo "    t   type one (such as Europe/London)"
    echo
    echo "  (The times are the tablet's clock. Once it is online, network time corrects it.)"
    echo
    ask -r -e -p "  Your choice: " pick; pick=$(unrub "$pick")
    case "$pick" in
      [1-6]) zone=${ZONES[$((pick - 1))]}; return ;;
      t|T) ask -r -e -p "  Time zone: " typed; typed=$(unrub "$typed" | tr -d ' ')
           if [[ "$typed" =~ ^[A-Za-z0-9_+/-]+$ ]] && [ -f "/usr/share/zoneinfo/$typed" ]; then
             zone=$typed; return
           fi
           echo "  \"$typed\" is not a time zone this installer knows."; sleep 3 ;;
    esac
  done
}
# Keyboards differ in what Backspace sends (^H or ^?). Make both work, and take any that
# still come through out of what was typed, with whatever they rubbed out.
stty erase '^?' 2>/dev/null
# No keyboard answers at all (the screen went away): stop asking, and leave the rest to the
# installer's own screens.
leave() {
  echo "  No answer from the keyboard: the installer asks the rest."; sleep 3
  chvt 1; exit 0
}
ask() { read "$@" || leave; }
unrub() {
  local text=$1 out="" i c
  for ((i = 0; i < ${#text}; i++)); do
    c=${text:i:1}
    if [ "$c" = $'\b' ] || [ "$c" = $'\x7f' ]; then out=${out%?}; else out+=$c; fi
  done
  printf '%s' "$out" | tr -d '[:cntrl:]'
}

exec </dev/tty6 >/dev/tty6 2>&1
chvt 6
setfont latarcyrheb-sun32 2>/dev/null || true
title "starting"
echo "  Looking for a network..."
wired_up
for i in $(seq 1 15); do online && break; [ $((i % 5)) = 0 ] && wired_up; sleep 1; done

# ---- 1. Wi-Fi (skipped when a cable has the tablet online already) ----
# From the stick's answers: tried until it joins (a phone's hotspot may need its screen open).
if ! online && [ -n "$A_WIFI_NAME" ]; then
  nmcli radio wifi on 2>/dev/null
  tries=0
  while ! online; do
    tries=$((tries + 1))
    title "joining Wi-Fi"
    echo "  Joining $A_WIFI_NAME (try $tries)..."
    nmcli device wifi rescan 2>/dev/null; sleep 4
    if seen=$(wifi_seen "$A_WIFI_NAME"); then
      if [ -n "$A_WIFI_PASSWORD" ]; then
        nmcli device wifi connect "$seen" password "$A_WIFI_PASSWORD" >/dev/null 2>&1
      else
        nmcli device wifi connect "$seen" >/dev/null 2>&1
      fi
      for i in $(seq 1 20); do online && break; sleep 1; done
      online && { echo "  Connected."; sleep 1; break; }
      nmcli connection delete id "$seen" >/dev/null 2>&1
      echo "  $seen is there but did not connect: check the Wi-Fi password the stick was made with."
    else
      echo "  $A_WIFI_NAME is not in sight."
      echo "  For a phone's hotspot: turn it on, keep its hotspot screen open, phone near the tablet."
    fi
    echo "  Trying again in 10 seconds. (To give up: hold the power button.)"
    sleep 10
  done
fi
if ! online; then
  nmcli radio wifi on 2>/dev/null
  while ! online; do
    if ! nmcli -t -f TYPE device 2>/dev/null | grep -qx wifi; then
      title "network"
      echo "  This tablet has no Wi-Fi the installer can use."
      echo "  Plug in a network cable, then press Enter."
      ask -r _
      wired_up
      for i in $(seq 1 20); do online && break; sleep 1; done
      continue
    fi
    title "choose your Wi-Fi"
    echo "  Rocky Linux's packages are downloaded during the install (about 2 GB)."
    echo "  Looking for networks..."
    wired_up
    nmcli device wifi rescan 2>/dev/null; sleep 4
    online && break  # a cable came up meanwhile
    names=() ; locks=()
    while IFS= read -r line; do
      sec=${line##*:}; rest=${line%:*}; name=${rest%:*}
      name=${name//\\:/:}
      [ -n "$name" ] || continue
      for n in "${names[@]}"; do [ "$n" = "$name" ] && continue 2; done
      names+=("$name"); locks+=("$sec")
    done < <(nmcli -t -f SSID,SIGNAL,SECURITY device wifi list --rescan no 2>/dev/null)
    title "choose your Wi-Fi"
    for i in "${!names[@]}"; do
      printf "   %2d   %s\n" "$((i + 1))" "${names[$i]}"
      [ "$i" -ge 14 ] && break
    done
    echo
    echo "    r   look again (or after plugging in a cable)"
    echo "    h   a hidden network       s   skip (set it later)"
    echo
    ask -r -e -p "  Your choice: " pick; pick=$(unrub "$pick")
    case "$pick" in
      s|S) break ;;
      r|R|"") continue ;;
      h|H) ask -r -e -p "  Network name: " name; name=$(unrub "$name"); hidden=yes; sec=WPA ;;
      *) [[ "$pick" =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le "${#names[@]}" ] || continue
         name=${names[$((pick - 1))]}; sec=${locks[$((pick - 1))]}; hidden=no ;;
    esac
    password=""
    if [ -n "$sec" ] && [ "$sec" != "--" ]; then
      ask -r -s -p "  Password for $name: " password; echo; password=$(unrub "$password")
    fi
    echo "  Joining $name..."
    if [ -n "$password" ]; then
      nmcli device wifi connect "$name" password "$password" hidden "$hidden" >/dev/null 2>&1
    else
      nmcli device wifi connect "$name" hidden "$hidden" >/dev/null 2>&1
    fi
    for i in $(seq 1 20); do online && break; sleep 1; done
    if online; then echo "  Connected."; sleep 1; break; fi
    echo "  That did not connect. Check the password and try again."
    nmcli connection delete id "$name" >/dev/null 2>&1
    sleep 3
  done
fi

# ---- 2. The tablet's user ----
# The service tag by default (7abc123), as in the tablet's network name dt-7abc123.
default=$(cat /tmp/doubletake-tag.txt 2>/dev/null)
[[ "$default" =~ ^[a-z0-9][a-z0-9_-]{0,30}$ ]] && ! [[ "$default" =~ ^[0-9]+$ ]] && ! grep -q "^$default:" /etc/passwd || default=doubletake
if [ -n "$A_PASSWORD_HASH" ]; then
  # From the stick: the usual name, and the password scrambled when the stick was made.
  user=$default
  echo "user --name=$user --gecos=$user --groups=wheel --iscrypted --password=$A_PASSWORD_HASH" > /tmp/doubletake-user.ks
fi
while [ -z "$A_PASSWORD_HASH" ]; do
  title "this tablet's user"
  echo "  The name and password for this tablet (for its Maintenance mode and updates)."
  echo
  ask -r -e -p "  Name [$default]: " user
  user=$(unrub "$user" | tr '[:upper:]' '[:lower:]' | tr -d ' ')
  user=${user:-$default}
  # (The installer has no getent: its own list of system users is /etc/passwd.)
  # A Dell service tag (7abc123) is fine: it may start with a number, but not be all numbers.
  [[ "$user" =~ ^[a-z0-9_][a-z0-9_-]{0,30}$ ]] && ! [[ "$user" =~ ^[0-9]+$ ]] &&
    ! grep -q "^$user:" /etc/passwd && break
  echo "  \"$user\" will not do: small letters and numbers only, not all numbers,"
  echo "  and not a name the system uses (such as root). Press Enter for $default."; sleep 4
done
while [ -z "$A_PASSWORD_HASH" ]; do
  echo
  ask -r -s -p "  Password for $user: " pw1; echo
  ask -r -s -p "  The same password again: " pw2; echo
  pw1=$(unrub "$pw1"); pw2=$(unrub "$pw2")
  if [ "$pw1" != "$pw2" ]; then echo "  Those two are not the same. Again."; continue; fi
  if [ "${#pw1}" -lt 6 ]; then echo "  At least 6 characters, please. Again."; continue; fi
  break
done
[ -n "$A_PASSWORD_HASH" ] || hash=$(printf '%s' "$pw1" | python3 -c 'import crypt, sys; print(crypt.crypt(sys.stdin.read(), crypt.mksalt(crypt.METHOD_SHA512)))' 2>/dev/null)
[ -n "$A_PASSWORD_HASH" ] || [ "${hash:0:3}" = '$6$' ] || hash=$(printf '%s' "$pw1" | openssl passwd -6 -stdin 2>/dev/null)
pw1=""; pw2=""
if [ -n "$A_PASSWORD_HASH" ]; then
  :
elif [ "${hash:0:3}" = '$6$' ]; then
  echo "user --name=$user --gecos=$user --groups=wheel --iscrypted --password=$hash" > /tmp/doubletake-user.ks
else
  echo "  Could not keep that password. The installer will ask for the user instead."; sleep 4
fi

# ---- 3. The clock and the time zone: from the network, else asked ----
# Online: set the clock from network time now, so the times shown here are right even when
# the tablet's own clock is off (some keep local time in it: hours out). The installed
# tablet keeps it right by itself afterwards (chronyd).
if online; then
  if chronyc tracking >/dev/null 2>&1; then
    chronyc -a burst 4/4 >/dev/null 2>&1
    chronyc waitsync 15 0.5 >/dev/null 2>&1
    chronyc -a makestep >/dev/null 2>&1
  else
    timeout 25 chronyd -q 'pool 2.rocky.pool.ntp.org iburst' >/dev/null 2>&1
  fi
  hwclock --systohc --utc >/dev/null 2>&1
fi
zone=$A_ZONE
if [ -z "$zone" ] && online; then
  zone=$(python3 - <<'PY' 2>/dev/null
import json, urllib.request
with urllib.request.urlopen("https://geoip.fedoraproject.org/city", timeout=10) as reply:
    print(json.load(reply).get("time_zone") or "")
PY
)
fi
[ -n "$zone" ] && [ -f "/usr/share/zoneinfo/$zone" ] || ask_zone
echo "timezone $zone --utc" > /tmp/doubletake-time.ks

# ---- 4. ERASE ----
# A no-keyboard stick: a 30-second countdown instead of typing ERASE. Pulling the stick
# out stops it (nothing erased, the tablet turns off); a key on a keyboard, if there is one,
# goes to the usual question.
if [ -f /tmp/doubletake-disk.txt ]; then
  disk=$(cat /tmp/doubletake-disk.txt)
  erase=ask
  if auto_erase; then
    erase=yes
    for ((left = 30; left > 0; left--)); do
      title "installing by itself"
      echo "  Tablet:     $product"
      echo "  User:       $user"
      echo "  Time zone:  $zone (now $(clock "$zone"))"
      echo "  Name:       $(cat /tmp/doubletake-name.txt 2>/dev/null)"
      echo "  Drive:      $disk"
      echo
      echo "  EVERYTHING ON THAT DRIVE WILL BE ERASED IN $left SECONDS."
      echo
      big_number "$left"
      echo
      echo "  To stop: pull out the USB stick, or hold the power button until it turns off."
      stick_gone && stopped
      read -r -s -n 1 -t 1 _; got=$?
      if [ "$got" -eq 0 ]; then erase=ask; break; fi
      [ "$got" -gt 128 ] || sleep 1  # >128: the second passed; else no keyboard (read returned at once)
    done
    stick_gone && stopped
  fi
  while [ "$erase" = ask ]; do
    title "ready to install"
    echo "  User:       $user"
    echo "  Time zone:  $zone (now $(clock "$zone"))"
    echo "  Name:       $(cat /tmp/doubletake-name.txt 2>/dev/null)"
    echo "  Drive:      $disk"
    echo
    echo "  Installing DoubleTake ERASES EVERYTHING on that drive."
    echo
    echo "  Type ERASE to install, ZONE to change the time zone,"
    ask -r -e -p "  or OFF to turn the tablet off: " answer; answer=$(unrub "$answer")
    case "$answer" in
      ERASE|erase|Erase) break ;;
      ZONE|zone|Zone) ask_zone; echo "timezone $zone --utc" > /tmp/doubletake-time.ks ;;
      OFF|off|Off) poweroff -f ;;
    esac
  done
  title "installing"
  echo "  The installer takes it from here: about 15 to 30 minutes."
  sleep 2
fi

chvt 1
exec </dev/tty1 >/dev/tty1 2>&1
%end

%post --nochroot --log=/mnt/sysimage/root/doubletake-install.log
#!/bin/bash
# The DoubleTake files on the installer stick: the package and the first-start step.
found=""
for dir in /run/install/repo /run/install/sources/mount-0000-cdrom /run/install/isodir; do
  [ -d "$dir/doubletake" ] && found="$dir/doubletake" && break
done
if [ -z "$found" ]; then
  mkdir -p /mnt/doubletake-media
  for dev in /dev/sr* /dev/sd*; do
    [ -b "$dev" ] || continue
    mount -o ro "$dev" /mnt/doubletake-media 2>/dev/null || continue
    if [ -d /mnt/doubletake-media/doubletake ]; then found=/mnt/doubletake-media/doubletake; break; fi
    umount /mnt/doubletake-media
  done
fi
if [ -z "$found" ]; then
  echo "The DoubleTake files are not on the installer stick."
  exit 1
fi
echo "DoubleTake files: $found"
mkdir -p /mnt/sysimage/root/doubletake
cp -v "$found"/*.rpm /mnt/sysimage/root/doubletake/
install -v -m 0755 "$found/doubletake-firstboot" /mnt/sysimage/usr/libexec/doubletake-firstboot
install -v -m 0644 "$found/doubletake-firstboot.service" /mnt/sysimage/etc/systemd/system/doubletake-firstboot.service
%end

%post --log=/root/doubletake-install-2.log
#!/bin/bash
set -x
dnf install -y /root/doubletake/doubletake-*.el9.*.rpm
# A DoubleTake appliance: its desktop corner shows DoubleTake, not the system's logo.
mkdir -p /etc/doubletake
echo "Installed by the DoubleTake installer, $(date +%F)" > /etc/doubletake/appliance
# The DoubleTake boot screen from the very first start (the first-start setup would only
# switch it on for the start after).
/usr/libexec/doubletake-helper splash default || echo "boot screen: left for the first start"

# An appliance carries only what it needs: these apps are taken off again. Nothing is
# taken off if that would also take anything the desktop or DoubleTake needs.
extras="evolution cheese totem gnome-tour gnome-characters gnome-font-viewer"
plan=$(dnf -q remove --assumeno --setopt=clean_requirements_on_remove=False $extras 2>&1)
echo "$plan"
if echo "$plan" | grep -qwE 'gnome-shell|gnome-session|gdm|nautilus|firefox|gnome-kiosk|doubletake|gnome-control-center'; then
  echo "apps: left on, as taking them off would take more"
else
  dnf -y remove --setopt=clean_requirements_on_remove=False $extras || echo "apps: left on"
fi

# Kiosk Script is how DoubleTake runs full screen: it stays, but out of the app grid.
for f in $(rpm -ql gnome-kiosk-script-session | grep '^/usr/share/applications/.*\.desktop$'); do
  mkdir -p /usr/local/share/applications
  sed -e '/^NoDisplay=/d' -e '/^\[Desktop Entry\]/a NoDisplay=true' "$f" > "/usr/local/share/applications/$(basename "$f")"
done

# The top bar's logo: the system's gnome-shell draws the file below, by its path, next to
# Activities. It becomes the boot screen's ring, and rpm is told to leave that one file
# alone from now on (a "net shared" path), so Rocky's logo updates do not put theirs back.
ring=/usr/share/doubletake/doubletake/assets/start-here.svg
logo=/usr/share/icons/hicolor/scalable/apps/start-here.svg
mkdir -p /etc/rpm
echo "%_netsharedpath $logo" > /etc/rpm/macros.doubletake
install -m 0644 "$ring" "$logo" && echo "top bar: the ring"
install -m 0644 "$ring" /usr/share/icons/hicolor/scalable/places/start-here.svg
gtk-update-icon-cache -f /usr/share/icons/hicolor >/dev/null 2>&1 || true

# Wired connections connect by themselves on the tablet, whichever port or adapter (the
# installer may have saved one set not to).
for f in /etc/NetworkManager/system-connections/*.nmconnection; do
  [ -f "$f" ] && grep -q '^type=ethernet' "$f" && sed -i '/^autoconnect=false/d' "$f"
done
for f in /etc/sysconfig/network-scripts/ifcfg-*; do
  [ -f "$f" ] && grep -q '^TYPE=Ethernet' "$f" && sed -i 's/^ONBOOT=no/ONBOOT=yes/' "$f"
done
mkdir -p /etc/NetworkManager/conf.d
printf '# DoubleTake: a cable plugged in later connects by itself.\n[main]\nno-auto-default=\n' \
  > /etc/NetworkManager/conf.d/90-doubletake-wired.conf
systemctl enable chronyd || true

# Power on Balanced from the first start (tuned-ppd's own record of the mode GNOME shows).
if rpm -q tuned-ppd >/dev/null 2>&1; then echo balanced > /etc/tuned/ppd_base_profile; fi

# The clock in 12 hours (9:41 PM), as the default for every user (each can still change it).
mkdir -p /etc/dconf/db/local.d /etc/dconf/profile
[ -f /etc/dconf/profile/user ] || printf 'user-db:user\nsystem-db:local\n' > /etc/dconf/profile/user
grep -q '^system-db:local' /etc/dconf/profile/user || echo 'system-db:local' >> /etc/dconf/profile/user
# Automatic Screen Brightness off: on the 7220 its light sensor makes the desktop flicker.
# DoubleTake's own screens set their brightness themselves.
cat > /etc/dconf/db/local.d/00-doubletake <<'DCONF'
[org/gnome/desktop/interface]
clock-format='12h'

[org/gnome/settings-daemon/plugins/power]
ambient-enabled=false
DCONF
dconf update || echo "dconf: left for the first start"

mkdir -p /var/lib/doubletake
touch /var/lib/doubletake/firstboot
systemctl enable doubletake-firstboot.service
%end
