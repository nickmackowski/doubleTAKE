# Changelog

## 1.0.5c
- **Straight home.** « next to the back button jumps to the home screen from a shoot, the
  viewer, the editor or Exports, rather than four taps of ‹.
- **Half-time stays in one folder.** Backing up within 12 hours of an import, Start asks
  where the photos go: one of those imports, or a new folder. The second camera of a
  two-camera event still joins on its own.

## 1.0.4c
- **Choose what goes to the phone.** Tap exports to tick them; Send to phone and Copy to USB
  take the ticked ones, or all of them when none are.
- **Sharing keeps going while you work.** Done turns the box into a bar, and photos ticked
  or exported meanwhile show on the phone within seconds, with no new codes.
- **A better phone page:** the last exported first, each photo in its own shape, and a large
  view with Previous, Next and All photos.
- **Saving on an iPhone goes to Photos.** The page tells iPhones to press and hold the
  photo and choose Save to Photos, rather than a download that lands in Files. Android
  keeps its Save button. The DoubleTake icon, in the boot screen's turning ring, is at the
  bottom of the page, and your website is a link.
- **The hotspot goes back off.** When Send to phone turned the hotspot on, stopping the
  share turns it off and the tablet rejoins its Wi-Fi. A hotspot turned on by hand stays on.
- **Your name on it.** Settings › Send to phone: your name, website, logo and links on the
  phone page; your name and website go into each exported JPEG as Artist and Copyright.
- **Two cameras, one import.** Shoot with more than one camera (Backup rules): a second
  camera's photos join the first one's import.

## 1.0.3c
- **Start begins the backup.** Tapping Back up camera on the home screen starts copying
  straight away, as P1 does. If it cannot start (the tablet's battery too low off its
  charger, or not enough room), a message says why.
- **The day a photo was taken** is in the viewer's line now: `10-22-26  14:21:45`.
- **The boot screen's ring takes the shape of the start-up logo.** A round maker's logo
  stored a little oval, as the 7220's is, gets a ring of that very shape and place, so one
  turns into the other with nothing to line up. The ring spins without wobbling.
- **No jump in brightness at start-up.** The panel comes up at full brightness, and the
  level you last used used to come back in one step partway through the boot screen. It now
  fades down to it in about a second.
- **Larger terminal windows.** The updater and the other terminals DoubleTake opens come up
  a third larger than usual, window and text alike, on Red Hat and Rocky.
- **One download:** each release has a zip with both packages and install.sh, which picks
  the right package itself.

## 1.0.2.5c
- **The boot screen's ring meets the maker's logo evenly.** The 7220's Dell logo is drawn a
  little oval (411 by 430), and the ring was sized to its height, so at the sides it stood
  out past Dell's circle while one faded into the other. It is now sized between the two,
  and the rings overlap evenly all the way round. A wide logo, such as a name on its own,
  is still ringed by its longer side.

## 1.0.2c
The first release offered by Maintenance mode's own updater.
- **The viewer's details sit on the photo:** the file name and time taken, a dot, then the
  shutter, aperture, ISO and focal length, in one line in the photo's bottom-left corner
  (two lines, broken at the dot, when the photo is too narrow). No boxes over the frame any
  more. Tap the photo to hide them, and again to show them; the choice stays for every photo.
- **The home screen shows each shoot's full name**, with its number of photos underneath.
- **Every shoot is an import:** `2026_1025_import_01`, then `_02`. The shoot name box and
  the one-tap names are gone; shoots named before keep their names. Renaming photos stays.
- **The USB drive for the second copy is no longer beta.**

## 1.0.1c — the first public release
DoubleTake on the Dell Latitude 7220 Rugged Extreme, for Rocky Linux 9 and Red Hat
Enterprise Linux 9 (Fedora: best effort). What it does is in the [README](README.md), and
how it got here in [How DoubleTake came to be](docs/HISTORY.md). Made for this release:
- The 7220's keys: P1 one-touch backup, P2 the red theme, P3 the tablet's status; the
  volume and brightness keys in DoubleTake only.
- Backup beeps for every backup.
- Updates in Maintenance mode, every 30, 60 or 90 days, or off.
- The way into Maintenance, and out to a restart or shutdown, listed step by step.
- A screen flicker fix for Intel graphics.
- An even frame round the photo in the viewer.
- A new icon, and a boot screen to match that gets its inspiration from the icon.
