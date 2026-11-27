# Changelog

## 1.1.2c
- **Three tablets:** the Dell Latitude 7220 and 7212 and the Panasonic FZ-M1, each with its
  buttons put to work (the [table](docs/USER-GUIDE.md#the-tablets-own-keys); the FZ-M1's A
  button needs one setting in its Setup Utility first).
- **An installer.** A DoubleTake installer stick, built on Rocky Linux 9: one text step for
  Wi-Fi, your user, the time zone and erasing the drive, then the install runs on its own
  and the tablet starts straight into DoubleTake's welcome pages. The zip install for an
  existing Red Hat Enterprise Linux 9 or Fedora system carries on as before.
- **Watermark on what goes to a phone.** Settings › Send to phone: a switch and two lines,
  your name and website to start with, in thin white letters from corner to corner, with
  a preview. Only the phone's copies are marked; the exports stay as they are.
- **No "Open previous tabs?"** The screens always start fresh, with no Firefox bar on top.
- **An appliance, from the installer:** the boot screen's ring in the top bar and around
  the icon in the desktop corner, which says "running on Rocky Linux"; only the apps
  DoubleTake needs, with Kiosk Script out of sight; the clock in 12 hours; Automatic Screen Brightness off; power on
  Balanced. The tablet is named dt-<its service tag> on a network, the NAS DoubleTake_NAS
  and the hotspot DoubleTake_HS. A wired network (a dock or USB-C adapter) is used before
  Wi-Fi; without a network the installer asks the time zone, showing the time in 12 hours.
- **The power button** sleeps the tablet (or turns it off: Settings › Device) with no
  question. During a backup it waits until both copies are verified, with a notice and
  Cancel. **Power mode** (Power Saver, Balanced, Performance) in Settings › Device.
- **Sleep on the charger too** (after 1 hour, or as set), and Screen dark after 30 min.
  Maintenance mode goes dark and sleeps as well. Never while photos are being copied, a
  computer is using the NAS, Send to phone is running, or an update is installing.
- **Rotate** (↻ in the top bar of the viewer and the editor): 90° a tap, for cameras that do
  not record which way up they were held (the Insta360). The photo file is never changed.
- **About** shows Copy 2's card or drive: maker, model, size, and how DoubleTake set it up.
- **The Panasonic FZ-M1** (7"): its buttons (A for night mode, rotate for brightness,
  Windows for Home), the boot screen sized for its wide firmware logo, and touch fixes that
  help every tablet: edit sliders take a finger anywhere on their row, the viewer's bottom
  buttons take touches near the bezel, the backup banner title stays on one line.
- **Room for photos** in the bottom bar: one number, what can still be backed up to both
  copies ("391 GB for both copies"), with each copy's icon turning amber or red when it needs a look. A tap opens the
  tablet's status.
- **The viewer:** Previous and Next beside the photo, Export straight from the viewer, and
  Up/Down to page the strip of thumbnails ("Frames 21 to 30 of 921").
- **The phone page** in two columns, each photo in its own shape, with a save arrow on each.
- **The folder box** ("Put these photos in") is three across and only as big as its boxes,
  with the imports in order (01, 02, ...) and New folder last.
- **Deleted shoots are forgotten:** gone from the library, and their photos count as new.
- **doubletake camera-timing:** where the wait before copying goes, with a camera over USB.
- **The installer** sets the clock from the internet before showing it, and suggests the
  tablet's service tag as its user name.
- **The installer's progress screen** has a thin line across the top that fills from orange
  to green as the install goes.
- **More room for photos on an ISO install:** the system part is a fixed 25 GB, and the photos
  get all the rest of the drive (a 128 GB drive went from 43 GB for photos to about 88).
- **When Copy 2 cannot finish** (its card full or out), the end of a backup says so in amber
  and says what to do, instead of "carries on by itself".
- **Updates noticed while DoubleTake runs**, on a trusted network only: an UPDATE AVAILABLE
  tag beside the title, and once a pop-up, Later or Go to Maintenance.
- **The 7212's buttons:** the Windows button goes Home;
  the rotate button steps the brightness 25% at a time, up to full and back down.
- **The viewer's Previous and Next** in the same style as the strip's Up and Down.
- **About** names the maker and where the code and releases are, and lists the tablet's
  processor, memory, drive, screen and serial number.
- **doubletake speedtest** measures a card reader honestly (different photos for each way).

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
