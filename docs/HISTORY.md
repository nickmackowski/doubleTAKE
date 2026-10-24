# How DoubleTake came to be

DoubleTake Field Station started as a question: can a rugged tablet do what a laptop does
at the end of a day's shooting — empty the cards, keep two verified copies, and say when
it is truly safe to format — with nothing but a touch screen? It grew in five days,
from a command-line backup engine to a full field station, tested at every step on a
Dell Latitude 5175 running Red Hat Enterprise Linux 9, with a Nikon D850 and later an
Insta360 Ace Pro 2.

It has had three names. It began as **Field Photo Station**, became **Field Station** at
0.5.7, and **DoubleTake Field Station** at 0.8.1: two verified copies of every photo,
which is what it was for from the start.

This page tells the story up to the first public release, oldest first. From 1.0.1c on,
each release is in the [changelog](../CHANGELOG.md).

## At a glance

| Versions | When | Name | What it became |
| --- | --- | --- | --- |
| 0.1 – 0.2 | 11 Oct 2026 | Field Photo Station | A verified backup engine, then the second copy and the Safe to Format rule |
| 0.3 – 0.4 | 11–12 Oct | Field Photo Station | Touch screens, the shoot library, the photo viewer, settings, the network |
| 0.5 | 12 Oct | Field Photo Station, then Field Station | The tablet as a NAS for the editing workstation; start at power-on; the editor, export and send to phone |
| 0.6 | 12 Oct | Field Station | The direct cable (FSDC), battery watch, the Cockpit look |
| 0.7 | 12–13 Oct | Field Station | An appliance: Field Station only, maintenance mode, the boot screen, battery icons, a password, unattended backup, screen and sleep |
| 0.8 | 13–14 Oct | DoubleTake Field Station | The new name, the black-and-white-to-colour boot screen, Copy 1 and Copy 2, a drive of its own |
| 0.9 | 15 Oct | DoubleTake Field Station | The package and install.sh, setup in the terminal, one folder per import |
| 1.0 pre-releases | 17–21 Oct | DoubleTake Field Station | Mostly cosmetic, on the Dell 7220: a new boot screen, its keys, updates in Maintenance |
| 1.0.1c | 21 Oct | DoubleTake Field Station | The first public release |

## By the numbers

How big the program was at a few points along the way: the engine (Python), the screens
(HTML, CSS and JavaScript) and the automated tests.

| Version | Engine, lines | Screens, lines | Tests |
| --- | ---: | ---: | ---: |
| 0.1.3 | 1,689 | — | 18 |
| 0.2.0 | 2,638 | — | 36 |
| 0.3.0 | 3,412 | 1,088 | 45 |
| 0.4.0 | 4,181 | 1,952 | 61 |
| 0.5.0 | 5,233 | 2,540 | 108 |
| 0.5.9 | 6,783 | 3,588 | 150 |
| 0.6.0 | 7,408 | 4,018 | 181 |
| 0.7.0 | 7,608 | 4,102 | 198 |
| 0.7.9.4 | 8,574 | 4,521 | 240 |
| 0.8.1 | 8,800 | 4,547 | 254 |
| 0.9.1 | 9,446 | 4,995 | 313 |
| 1.0.1c | 11,526 | 5,280 | 392 |

The program itself is still about 2 MB, most of it pictures and fonts. The packages are
about 20 MB, because they bring the camera library with them. It uses only what
comes with Python and Red Hat, plus three small libraries: gphoto2 for the camera, Pillow
for pictures and segno for QR codes.

## The story, era by era

### 0.1 – 0.2: the engine and the rule

The first version had no screens at all. It was the part everything else stands on: read
the camera over USB, copy each new photo, take a SHA-256 checksum as it comes off the
camera, check it again on the tablet, and keep a record so the next backup copies only
what is new and an interrupted one carries on where it stopped. It never writes to the
camera's cards. `fieldstation speedtest` measured the camera's transfer speed, "the number
the whole design has been waiting for".

0.2 added the second copy: every photo written to a microSD card under a temporary name,
flushed, read back from the card and compared before it counts. With it came the rule the
whole project is built around: **the camera's cards are only safe to format when every
photo on them is verified in two places.**

### 0.3 – 0.4: touch screens

0.3 put touch screens on top of the engine: home, ready, backup running, backup complete,
served to the tablet only and shown full screen in Firefox. The camera is found when it is
plugged in, the second copy runs in the background, and the format check is always on
screen.

0.4 made it a place to work, not only to back up: the **shoot library** with where each
shoot's copies are, **Mark as Imported** and **Free Up Space**, a **photo viewer** with a
100% focus check from the preview inside each NEF, themes (Daylight, Slate, Night and Night
red), settings, and the camera's battery in the top bar. Along the way: its own pop-up
keyboard, shoot thumbnails, full screen by default, and blank shoot names named by the
time of the first photo. 0.4.7 split the program, the photos and the records into
separate folders, so an upgrade can never touch a photo, and added Wi-Fi and the tablet's
own hotspot. 0.4.8 made the hotspot switch off cleanly.

### 0.5: home, and the editing workstation

0.5 turned the tablet into a **NAS** for the computer at home: a read-only Samba share and
SFTP, with a login of their own, open only on trusted networks (the cable, the hotspot and
Wi-Fi networks you choose), and a home-screen tile showing who is connected and what they
are reading. Format the microSD card, Reset settings and Erase everything arrived with it.
Then, in quick steps: a warning on untrusted networks, connection help for Mac, Windows and
Linux, cameras shown by name and brand (Sony model codes too).

0.5.7 renamed it **Field Station** and brought **Start at power-on** (the tablet logs in
and opens full screen by itself), the **camera clock check** — the only thing the app ever
writes to a camera, and only after you confirm — and **Bluetooth** for a keyboard or
mouse. 0.5.9 added a **quick editor** (crop, straighten, exposure, white balance and more,
saved apart from the photo), **Export JPEG**, **Send to phone** over the hotspot with QR
codes, and **Copy to USB**. 0.5.10 added **renaming photos** from a pattern.

### 0.6: in the field

0.6 was about the field itself. The **direct cable**: plug a network cable straight from
the tablet to a laptop and they link by themselves, no router needed. In testing it moved
71 GB from a MacBook in 5 minutes 24 seconds, about 220 MB/s, and in 0.6.1 it got its own
name, **FSDC** (Field Station Direct Connect), and its own icon. **Battery watch** keeps a
backup from starting on a nearly flat camera or tablet. The second copy could go to a USB
drive, and the **Cockpit look** arrived: a second look for every screen, inspired by a
flight deck, in dark and light.

### 0.7: an appliance

0.7 made the tablet a single-purpose device. **Field Station only** starts it into GNOME
Kiosk with nothing else on the screen; if the screens ever close, they open again.
**Maintenance mode** gives one visit to the plain desktop, with a wallpaper saying so and a
restart button in the dock, and the next start is Field Station again. Restart and Shut
down moved into Settings.

Then came the details that make it feel finished:

- the **boot screen** with the logo, from the Dell logo straight to Field Station, no
  messages, no white flash (0.7.3 – 0.7.5);
- **What to copy** (RAW only, RAW + JPEG, or everything) and the **Insta360 Ace Pro 2**,
  read as a camera when it plugs in as a drive (0.7.2);
- **each battery on its own**, as small upright icons with no numbers, green, amber and
  red, flashing when nearly empty, with **Swap** when it is time (0.7.6 – 0.7.9);
- the **Field Station password** for the ways out and the jobs that cannot be undone,
  with recovery written down (0.7.9.1);
- **unattended backup** with a countdown and a chime (0.7.9.2);
- **screen dark** and **sleep on battery** owned by Field Station, never during a copy,
  and touch that always wakes the screen (0.7.9.3 – 0.7.9.6).

### 0.8: DoubleTake

0.8.1 gave it the name it had been earning: **DoubleTake Field Station**. The logo now
appears in black and white at start-up and turns to colour, a double take you can see.
Upgrading moves everything over by itself.

## Every version

Oldest first. Some point releases were folded together.

**0.1.0 – 0.1.3**
- Verified backup engine for a camera over USB: SHA-256 checksums, a record of every photo
  copied, only new photos copied, resume after an interruption.
- Commands: `doctor`, `camera`, `speedtest`, `backup`, `shoots`, `verify`, `config`.
- Never deletes, formats or writes to the camera's cards.

**0.2.0**
- Second copy to a microSD card, written, flushed, read back and compared, in its own
  folder on the card, never erasing anything else there.
- The Safe to Format rule: every photo verified on the tablet and on the second copy.
- `card`, `mirror` and `verify --card`, which catches counterfeit cards.

**0.3.0**
- Touch screens: home, ready to start, backup running, backup complete.
- The camera found when plugged in; the second copy in the background with pause and resume.
- A new card asks once; the format check always on screen.

**0.4.0**
- Shoot library, Mark as Imported, Keep a while or Delete after import, Free Up Space.
- Photo viewer with a 100% focus check, exposure details, swipe between photos.
- Themes: Daylight, Slate, Night, Night red. Settings screens.

**0.4.1 – 0.4.5**
- Full screen by default, in a Firefox of its own; `--window` for a window.
- Its own pop-up keyboard. Shoot thumbnails, and tapping a shoot on the home screen.

**0.4.6**
- A blank shoot name is named by the time of its first photo, so two never share a folder.

**0.4.7**
- Program, photos and records in three separate folders; upgrades never touch photos, and
  earlier photos are moved over, after asking.
- Wi-Fi and the tablet's own hotspot; network icons in the top bar.

**0.4.8**
- The hotspot switches off cleanly and the tablet goes back to a known network.

**0.5.0**
- The tablet as a NAS: read-only Samba share and SFTP, with their own login.
- Trusted networks through firewall zones; the NAS tile with who is connected.
- Format the microSD card; Reset settings; Erase everything.

**0.5.1**
- A warning when the NAS is on but the network is not trusted, with one-tap trust.

**0.5.2 – 0.5.4**
- Only the photos share is offered; connection help for Mac, Windows and Linux.
- Exit moved to Settings, Device; the app in the desktop's app list. A NAS fix.

**0.5.5 – 0.5.6**
- Cameras shown with their brand; Sony model codes shown as camera names.

**0.5.7**
- Renamed **Field Station**.
- Start at power-on; the camera clock check; Bluetooth.

**0.5.8**
- The NAS tile shows connected computers again after a restart.

**0.5.9**
- Quick editor, Export JPEG, Send to phone with QR codes, Copy to USB.
- Viewer zoom at 50%, 75% and 100%; sorting a shoot.

**0.5.10**
- Rename photos from a pattern, on import or afterwards; the microSD copy follows.

**0.6.0**
- Direct cable to a laptop; battery watch; second copy on a USB drive (beta); the Cockpit look.

**0.6.1**
- FSDC, Field Station Direct Connect, with its own icon; no glow in Cockpit dark.

**0.7.0**
- Field Station only (GNOME Kiosk); Restart and Shut down in Settings.

**0.7.1**
- Field Station only finds the kiosk session by its RHEL 9 name.

**0.7.2**
- Maintenance mode; What to copy; the Insta360 Ace Pro 2 as a camera.

**0.7.3 – 0.7.5**
- The boot screen with the logo; the boot menu and start-up messages hidden; no white flash.

**0.7.6 – 0.7.7**
- Every tablet battery counted, then each shown on its own, with its health.

**0.7.8 – 0.7.9**
- Battery icons with no numbers, and Swap; the camera's battery read again while idle.

**0.7.9.1**
- The Field Station password, with recovery; the version at the right of the home bar.

**0.7.9.2**
- Unattended backup: countdown, date-and-number names, a chime, a dark screen between backups.

**0.7.9.3**
- No sleep in the middle of a copy.

**0.7.9.4**
- Screen dark after, and sleep on battery after, owned by Field Station.

**0.7.9.5**
- Touch always wakes a dark screen.

**0.7.9.6**
- Sleep on battery with the keyboard dock attached; settings panels scroll.

**0.8.1**
- Renamed **DoubleTake Field Station**; everything moved over on upgrade.
- The boot screen turns from black and white to colour.

**0.8.2**
- Fixed a folder 0.8.1 left closed to the screens, which stopped the settings and the home screen.

**0.8.3**
- A boot menu entry for a maintenance visit, and a guide to the ways back to the desktop.

**0.8.4**
- The boot menu entry redone as a copy of the usual one; doubleTAKE lettering; the boot
  screen back to black and white at shutdown.

**0.8.5 – 0.8.9**
- Computers on the NAS tile by name; the share's and SFTP's addresses on the tile.
- Copy 1 and Copy 2 in the bottom bar, with icons for the tablet, an SD card, a thumb
  drive or an external drive.
- Copy 2 on a drive of its own: a new card or drive is erased and kept for DoubleTake
  after three checks and Type AGREE.
- The boot screen's fades timed to the tablet's own start-up and shutdown.

**0.9.1**
- Install by double-click (RPM), and the setup wizard (both replaced in 0.9.5).
- A camera's card in a reader is a camera's card, never erased. Copy 2 follows Copy 1
  during a backup. The clock follows the desktop's 12- or 24-hour setting.

### 0.9: for someone else's tablet

0.9 made DoubleTake something another photographer could install: a package for Red Hat 9,
Rocky and Alma 9, and Fedora, with the camera library inside it. It began as a double-click
and a setup wizard on the screens; by 0.9.5 it was `install.sh` and the same questions in the
terminal, which showed every step as it happened, with Reconfigure on the screens for
changing them later. The camera's clock stopped being set (DoubleTake never writes to a
camera now), and each import got a folder of its own: `2026_1015_import_01`. Along with it, a camera's card in a card reader
became a camera's card, never to be erased, and Copy 2 started following Copy 1 during a
backup instead of waiting for it.

### 1.0: on the tablet it was made for

Seventeen pre-releases moved DoubleTake onto the Dell Latitude 7220 Rugged Extreme and
tested it there, on Red Hat, Rocky and Fedora in turn. Most of the work was cosmetic, with
three things worth naming: a new boot screen, the new icon (two photos and an orange ×2)
inside a turning ring that takes over from the maker's logo in its place; the tablet's own
keys, with P1 a one-touch backup that beeps; and updates offered from Maintenance mode. The
copying, the checking and the Safe to Format rule did not need to change.

1.0 itself was skipped: the first public release is **1.0.1c**. The c is a nod to Claude,
which wrote the code with Nick, and stays on every release from here.

## What comes next

- **1.0.2c**: the first update through Maintenance mode's own updater, to prove it.
- Checks that the tablet is hardened (disk encryption, a boot password).
- Maybe: Rocky and Red Hat 10, once 9 is closer to its end (2032).
- Someday: photos over Wi-Fi as they are taken, from cameras that can upload to a server.
