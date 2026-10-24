# DoubleTake user guide

Everything the app does, screen by screen, and the commands behind it. For a short
overview see the [README](../README.md); for how backups are verified, see
[How it works](HOW-IT-WORKS.md).

Run the commands in a terminal on the tablet itself, logged in at its own screen. The
system gives camera access to whoever is logged in at the screen, so the same commands
over SSH can report that no camera was found.

## Install

First, the system: Rocky Linux 9 (recommended), Red Hat Enterprise Linux 9 or AlmaLinux 9,
or Fedora (best effort: it runs, but your mileage may vary). When installing it, choose **Workstation** or **Server with GUI**
as the base environment. A Minimal install has no desktop, and DoubleTake needs one: the
login screen for Start at power-on, the kiosk session for DoubleTake only, Wi-Fi, sound,
and the desktop itself for maintenance mode.

Then DoubleTake, two ways: the package, which is the easy one, or setup.sh from the source.

### The package

Download `doubletake-<version>-1.el9.x86_64.rpm` (Red Hat, Rocky or Alma 9) or
`doubletake-<version>-1.fc.x86_64.rpm` (Fedora), and `install.sh`, into one folder. Then,
as the tablet's own user (an administrator: in the wheel group):

    bash install.sh

It checks the system, asks for the tablet's password once, and on Red Hat offers to
register the system if it is not yet (without that, the install stops with "nothing
provides samba"). Then it installs the package: the system fetches Samba, Firefox and the
rest from its own software sources, and the camera library comes inside the package. The
program goes in `/usr/share/doubletake`, the `doubletake` command in `/usr/bin`, and the
helper in `/usr/libexec`.

Then, still in the terminal, `doubletake setup` asks the questions, one at a time:

1. **The system part**, once: the built-in NAS (off until you switch it on), the
   DoubleTake boot screen (on), the boot menu entry, and the rule that lets the screens
   switch them. A clock counts while the boot screen is built, a few minutes.
2. The DoubleTake password, what to copy, Copy 2 (a new card or drive is erased only after
   you type AGREE), the built-in NAS login and switch, where the share opens, the hotspot,
   and how the tablet starts.
3. A restart, and DoubleTake comes up.

Enter keeps what is shown, and Ctrl-C stops with everything answered so far kept. Run
`doubletake setup` again any time. On the screens, **Settings, Device, Reconfigure** walks
through the same choices; the system part is done only in the terminal, and anything that
needs it says "Not set up yet. In a terminal: doubletake setup".

**Upgrading:** run `bash install.sh` next to the new package. Photos, records and settings
are kept. Double-clicking the package opens the system's own installer instead, which
installs the package but skips install.sh's checks; use install.sh.

**Removing:** `sudo dnf remove doubletake` switches the NAS off, puts the boot screen, the
desktop and the boot menu back as they were, and removes the screens' rule. Photos,
records and settings are kept.

### From the source

Clone the repository or unpack a release anywhere, for example in Downloads, then:

    cd doubletake
    ./setup.sh

The script installs the camera tools, stops the desktop from grabbing the camera,
installs the program into `~/DoubleTake` and the `doubletake` command. It asks
before changing anything. Afterwards the unpacked folder is no longer needed.

Three places, each with one job:

| Folder | What is in it | Replaced by an upgrade? |
| --- | --- | --- |
| `~/DoubleTake` | The program | Yes |
| `~/Pictures/DoubleTake` | The photos, one folder per shoot | Never |
| `~/Documents/DoubleTake` | The records: what has been backed up, run logs, thumbnails | Never |

The settings stay in `~/.config/doubletake/config.json`.

**Upgrading:** press Exit on the screens, get the new version, and run `./setup.sh`
from it again. Setup replaces only `~/DoubleTake`. Photos and records are never
touched by an upgrade.

The tablet needs to be online, because the camera library is downloaded during setup.
A Red Hat system that is not registered works too: the script only asks dnf for
packages that are missing, and a normal desktop install already has them.

## The screens

    doubletake gui

This starts the engine and opens the screens full screen in Firefox. Leave the
terminal open: the engine runs there. For a normal Firefox window instead:

    doubletake gui --window

DoubleTake is also in the desktop's app list. Stop it with Settings, Device, Exit to
the desktop, or Ctrl-C in the terminal. Stopping is always safe. Anything part-way is picked up again next time.

What happens by itself:

- **The camera is found when it is plugged in.** The home screen reads its cards and
  shows how many photos are new. The first read after plugging in takes about a minute.
- **The second copy runs in the background.** It starts once a tenth of the backup from
  the camera is done and follows a little behind it, from the tablet's own drive: it only
  ever takes photos already verified on the tablet. As soon as the photos are on the
  tablet, the camera can be unplugged, and Copy 2 finishes by itself, with its progress on
  the home screen. It also starts by itself when the
  screens are opened and photos are still waiting. Pause and Resume are on its tile.
- **A new card or drive becomes DoubleTake's.** The second copy lives on a card or drive of its
  own. The first time a card or drive is seen, DoubleTake
  shows its name, size and what is on it, then asks three times: **Prepare for DoubleTake**,
  **Erase it** (listing everything that will go), and **Type AGREE**, plus the DoubleTake
  password if one is set. It then erases the whole drive, gives it one exFAT partition named
  `DT-COPY2` (readable on a Mac, Windows and Linux), and uses it silently from then on. **Not
  this one** leaves the drive untouched. A card already holding DoubleTake's copies from an
  earlier version is used as it is. The drive the system runs from, or the one holding the
  photos, is never offered.
- **The format check is always on screen.** Green only when every photo on the camera's
  cards is verified on the tablet and on the second copy.

Full screen runs a separate Firefox of its own, which Exit closes. It does
not touch your normal Firefox. If the screens are closed by accident, run
`doubletake gui` again: it finds the engine still running and opens the screens.

**Typing:** tapping a shoot name, or Add a Name in the settings, opens the app's own
keyboard at the bottom of the screen. A keyboard plugged into the tablet types into it
too, and Enter finishes.

The screens are served to this tablet only (127.0.0.1). Nothing is reachable from the
network. Reloading or closing the browser does not stop a backup: the engine keeps
going and the screens pick up wherever it is.

Shoot names offered as one-tap buttons come from `quick_names` in the settings file,
plus the names used most recently.

**Where a backup goes:** each backup copies only the photos that are new since the
last one, into a folder of its own: the day of the import, the shoot name, and a number.
Left blank, the name is `import`: `2026_1015_import_01`, then `2026_1015_import_02` the
next time the camera is plugged in that day. A typed name takes its place, in lower case
with `_` for spaces: "Ramp Sunset" is `2026_1015_ramp_sunset_01`. Characters that Windows
or a Mac cannot hold in a folder name (`/ \ : * ? " < > |`) are left out. The numbers count
per name per day. If the camera's cards
were not formatted in between, the records know what is already on the tablet, so the next
folder holds only the new photos. A backup stopped part-way is carried on in its own
folder. The start screen shows the folder before you start.

**Gaps in the numbers are normal.** A deleted shoot's number is not used again. Import three
times in a day (`_01`, `_02`, `_03`), delete `_03`, and the next import that day is `_04`,
not `_03`. So a folder already brought onto your editing workstation as `_03` is never
mixed up with a different shoot of the same name, and the records never mix the two.

To try the screens without a camera, point them at any folder of photos:

    doubletake gui --source dir:/some/folder

For a short test, limit how many photos each backup from the screens copies:

    doubletake gui --limit 50

### Shoot library

Tap a shoot on the home screen's Shoot Library tile to go straight to its photos, or
tap the tile's title for the list of all shoots. Each shoot shows a picture of its
first photo and where its copies are:
on Copy 1 (the tablet) and on Copy 2. Tap the picture or View Photos to see its photos.

**Deleting shoots.** DoubleTake's job is the two copies; keeping the photos safe after that,
on your editing workstation, is yours. So once a shoot is there, delete it from the tablet:
tap **Choose** on each shoot to delete (one or several), then **Delete** at the bottom. It
asks once, with the size, and for the DoubleTake password if one is set, then removes the
shoots from Copy 1 and Copy 2. It cannot be undone.

- **A shoot is kept for a day after its last photo was copied**, in case something went
  wrong: until then it says when it can be deleted.
- A shoot being backed up right now cannot be deleted.
- Deleting removes only the photos DoubleTake copied and its report; anything else in a
  shoot's folder is left alone. If Copy 2 is out at the time, its copy is removed the next
  time it is connected.
- A deleted shoot stays in the list, greyed, and its photos are still recognised if they
  are still on the camera's card, so they are not copied again. The Camera cards tile then
  says they are on neither copy.
- Its number is not used again that day: after deleting `2026_1015_import_03`, the next
  import is `_04`. See "Gaps in the numbers are normal" above.

Safe to Format counts Copy 1 and Copy 2 only.

### Photo viewer

- Tap a photo in the grid to open it. It is shown whole, in a mat: a frame of the same
  width on all four sides, on the screen's own colour. Swipe, the arrow buttons or the keyboard's arrow
  keys move between photos. The strip on the right shows the nearby frames.
- **50%, 75% and 100%** zoom in to check focus. Double-tap a spot on the photo to look
  there at the level last chosen, drag to look around, and double-tap again (or Fit) to go
  back. The label shows the size of the picture being used: it is the JPEG preview the
  camera stores inside each RAW file (NEF, most DNGs), not the RAW data. A DNG with no
  preview inside, such as the Insta360 Ace Pro 2's, is drawn from its sensor data instead,
  at half the sensor's size: the camera's white balance and an automatic brightness, good
  for choosing and checking photos but not a finished conversion. Making its pictures
  takes about a second each.
- Exposure (shutter, aperture, ISO, focal length) and the time taken come from the NEF.
- **Sort:** at the top of a shoot's photos, by capture time or by name, either way round.
  Capture time, oldest first, is the default. The choice is remembered, and the viewer
  follows the same order.

### Photo names

Settings, Folders and naming, **Photo names**:

- **Rename photos on import:** off by default, so photos keep the camera's names. When on,
  each photo is given its new name as it comes off the camera.
- **Pattern:** tap a ready-made one, or Change to type your own. The parts:

  | Part | Becomes |
  | --- | --- |
  | `{date}` | the date it was taken: `2026-10-11` |
  | `{time}` | the time it was taken: `141205` |
  | `{shoot}` | the shoot name |
  | `{seq}` | a running number in the shoot: `0001` |
  | `{orig}` | the camera's own name: `DSC_4841` |
  | `{num}` | the camera's number: `4841` |
  | `{camera}` | the camera: `D850` |

  A pattern needs `{seq}`, `{orig}` or `{num}`, so every photo gets a different name. The
  running number follows capture order, and carries on when a later backup adds to the shoot.

**Rename** at the top of a shoot's photos renames a shoot that is already on the tablet. It
shows the first few old and new names before anything changes.

- The microSD card's copies are renamed at the same time, or, if the card is out, the next
  time it is put in. Both copies always end up matching.
- A RAW file and a JPEG shot together (`DSC_4841.NEF`, `DSC_4841.JPG`) get the same new name.
- The camera's own names are kept in the records, so photos still on the camera are still
  recognised and never copied twice, and Safe to Format is unaffected.
- A shoot marked as imported is not renamed, because your photo app would lose
  track of the files. Other files you put in a shoot folder keep their names.
- Nothing on the camera is ever renamed.

### Quick edits

Tap **Edit** in the viewer. The editor works on the full-size JPEG the camera stores inside
each NEF, so it is quick on the tablet and starts from what the camera's own JPEG looks
like. The NEF is never changed, and real editing is still done from it, on your editing workstation.

- **Crop:** pick a shape (Free, Original, 3:2, 4:5, 1:1 or 16:9), then drag the box or its
  corners. An upright photo gets the upright version of the shape. **Done cropping** shows
  the result; **No crop** removes it.
- **Straighten:** up to 15 degrees either way. The picture is trimmed so no empty corners show.
- **Light:** exposure (in stops), contrast, highlights and shadows.
- **Colour:** temperature (warmer or cooler), tint, saturation, and black and white.
- **Detail:** sharpening.
- Double-tap a slider's name to put it back. **Hold for original** shows the photo as it
  came while held. **Reset** puts everything back.
- Edits are saved by themselves, as a small file per photo in
  `~/Documents/DoubleTake/edits/`. The grid marks edited photos, and exported ones.

### Exports

**Export JPEG** in the editor makes a new JPEG with the edits applied:

- **Size:** full size, 2048 px on the long side (suits social media) or 1080 px.
- **Quality:** High or Standard.
- **This photo**, or **All edited**: every edited photo in the shoot.

Exports go to `~/Pictures/DoubleTake/Exports/<shoot>/`, apart from the shoots. They keep
the camera model and the time the photo was taken. Exporting again replaces the earlier
JPEG. Deleting a shoot never removes its exports.
The **Exports** button in the shoot library (and in a shoot that has exports) shows them all.

Getting them off the tablet:

- **Send to phone:** the tablet's hotspot comes on if it is not already, and the screen shows
  two QR codes. Scan the first with the phone's camera to join the hotspot, then the second
  to open a page with the photos. On an iPhone, tap a photo, then press and hold it and
  choose Save to Photos; on Android, tap Save. The page is reachable only over the hotspot,
  its address holds a random code made fresh each time, it shows only the exports, and it
  stops after 30 minutes or when you tap Stop sharing. While the hotspot is on, the tablet
  is not on any other Wi-Fi network.
- **Copy to USB:** plug in a stick, choose it, and tap Copy. The exports go to a folder
  called `DoubleTake Exports` on it, each one read back and compared. Nothing else on
  the stick is touched. **Eject** before pulling it out.
- **The NAS:** the Exports folder is part of the share, next to the shoots.
- Thumbnails are made the first time a shoot is opened and kept, so the second time is
  quick. They live in `~/Documents/DoubleTake/previews/` and are removed when a
  shoot is cleared.

### Settings

- **Display and theme:** when the screen goes dark with no touch (2, 5 or 10 minutes, or
  never; never during a backup; the first touch or key only lights it again, and presses
  nothing) and, on battery, when the tablet sleeps (15 or 30 minutes,
  an hour, or never; never while photos are being copied); the boot screen (the DoubleTake icon in a turning orange and green ring while the tablet starts and shuts down,
  or the system's own; changing it takes a few minutes), and the look, Classic or Cockpit.
  With the boot screen on, the boot menu is hidden; tap F8 (Fn+F8) or Esc while the tablet
  starts to see it. Classic comes in Daylight, Slate,
  Night and Night Red; Cockpit in Cockpit Dark, Cockpit Light and Cockpit Red. The look changes only how the screens are
  drawn, never what they do.
- **Folders and naming:** how shoot folders are named, and the one-tap names.
- **Second copy:** on or off, where it goes (the microSD card, or a USB drive, beta), and
  use or set aside the card or drive that is in. See below.
- **Network:** Wi-Fi and the hotspot. See below.
- **Bluetooth:** keyboards, mice and the like. See below.
- **NAS:** the shared photos folder and SFTP. See below.
- **Backup rules:** what to copy (RAW only, RAW + JPEG, or everything with videos), what
  happens after a shoot is imported, and for how long it is kept. See What to copy, below.
- **Device:** start at power-on, Files and Terminal, exit to the desktop, the memory
  card, resets, and details about the tablet. See below.

### Network

The top bar shows the tablet's connections next to the batteries: **Wired** when a
network cable is connected, and the Wi-Fi network's name with its signal strength,
**Hotspot** while the tablet runs its own network, or **No Wi-Fi**.

Settings, Network:

- **Wi-Fi networks:** the networks in range, strongest first. Tap one to join it. A
  password is asked for, on the app's keyboard, only the first time; after that the
  tablet remembers it. The keyboard's **#+=** key gives the symbols passwords need, and
  **Show** shows what was typed.
- **Hotspot for the laptop:** switches on the tablet's own Wi-Fi network, so the laptop
  can join it directly with no router. The network name and password are shown there
  and can be changed. On the laptop the tablet is then at 10.42.0.1. While the hotspot
  is on, the tablet is not on any other Wi-Fi network, because one Wi-Fi card cannot do
  both; switching the hotspot off puts it back on a known network nearby.

- **FSDC (Field Station Direct Connect):** on by default. Plug a network cable from the tablet
  straight into the laptop, through USB network adapters on either end if needed. A cable
  first looks for a network as usual; when nothing answers for about 20 seconds, the
  tablet links to the laptop directly. The top bar shows **Linking…** with a laptop icon
  while it waits, then **FSDC**, lit up in the accent colour, the way two computers
  do with no router: each gives itself an address starting 169.254. On the laptop, open
  `smb://<tablet>.local` (Finder: Go, Connect to Server). The share is open on the direct
  cable when **Network cable** is trusted in the NAS settings. Plugged into a router or the
  dock, the cable works as usual and nothing changes. The tablet never hands out addresses
  on a cable, so it cannot upset a network it is plugged into.

All of this goes through the desktop's NetworkManager, so networks joined here also show
in GNOME's own settings, and the other way round.

### Second copy on a USB drive (beta)

Settings, Second copy, **Where it goes**: microSD card or USB drive. Only the chosen kind is
used; a USB drive is ignored while microSD card is chosen, and the other way round.

- The first time a USB drive is plugged in, it is prepared as for a card: erased, after the
  same three checks, and kept for DoubleTake. A drive with several partitions, as drives
  formatted on a Mac or by Windows often are, counts as one drive.
- The drive need not stay plugged in. Backups go on without it; the home screen says
  **No USB drive**, and the camera cards are not called safe to format. When it is plugged
  back in, the photos waiting are copied to it by itself and each is read back.
- Use Eject in the Files app, or pause the second copy, before pulling the drive out while
  it is being written.
- Copy to USB in the photo viewer is for exports, and never offers the second-copy drive.

### The DoubleTake password

**Setting or changing it**

1. Settings, Device, **DoubleTake password**, then **Set** (or **Change** if one is set).
2. When changing it, type the current one first.
3. Type the new one, then type it again. It needs at least 10 characters; common
   passwords are refused. A few words with spaces, such as `ramp at dawn 27`, is easy to
   type on the screen keyboard and hard to guess. **Show** on the keyboard shows what you
   typed.

To stop using one, tap **No password**, read the warning, and type the current password.

**If it is forgotten or lost**

The password cannot be cleared from the touch screens, so that someone who picks up the
tablet cannot simply remove it. Clearing it takes the tablet's own login (the Linux user
and password, not the DoubleTake one), from a text console. You need a keyboard: a USB
or Bluetooth one, or the tablet's keyboard cover if it has one.

1. With DoubleTake on the screen, press **Ctrl+Alt+F3**. A black text screen opens.
2. Log in with the tablet's own user name and password: the user set up when the OS was
   installed.
3. Type `sudo -i` and press Enter, and type the tablet's password again when asked.
4. Type the following and press Enter:

   ```
   /usr/local/libexec/doubletake-helper fs-password clear
   ```

   It answers `{"ok": true, "mode": "unset"}`.
5. Type `exit` twice to log out, then press **Ctrl+Alt+F1** (on some tablets **F2**) to
   return to DoubleTake.
6. Settings, Device: the password now shows as **Not set**. Set a new one.

If Ctrl+Alt+F3 does nothing, the keyboard's top row may be set to media keys: hold
**Fn** as well (Ctrl+Alt+Fn+F3), or press **Fn+Esc** once on a Dell keyboard to switch the
row to F keys. Or use either of the ways below.

### If the screens stop answering

Three ways to the desktop that do not need the screens. Each needs the tablet's own login
(the Linux user and password), not the DoubleTake password.

1. **The boot menu.** Restart the tablet (hold the power button if need be). When the Dell
   logo appears, tap **F8** about twice a second until a menu appears; on a keyboard whose top
   row starts as media keys, that is **Fn+F8**. Esc works on some
   keyboards; holding Shift often does not. The menu waits 3 seconds for the key. Choose
   **DoubleTake maintenance (desktop)**, listed with the system's own entries. The login screen
   asks for the tablet's password, then opens the plain desktop for one visit, as Maintenance
   mode does. Restart to return to DoubleTake. (UEFI Firmware Settings in the same menu, and
   F2 at the Dell logo, open the tablet's own firmware setup.)
2. **A text console.** Ctrl+Alt+F3, as above, to run a command such as an upgrade's setup.
3. **Another computer.** From a Mac or PC on the same network, open a terminal and type
   `ssh <your-user>@<tablet-name>.local`, with the tablet's password. This works when the
   tablet's remote login (sshd) is on, as it is on a standard Red Hat install.

The wait after wrong tries is cleared at the same time.

**If the tablet's own password is forgotten as well**, DoubleTake cannot help: reset the
Linux user's password with your OS's recovery procedure (for RHEL, booting to the rescue
target from the installation media), then follow the steps above.

### Unattended backup

Settings, Backup rules, **Back up when the camera is plugged in** (off at first).

- Plug in the camera. A countdown shows for 10 seconds with **Cancel** (to only look at the
  camera) and **Start now**. Then the new photos are copied as usual,
  verified, and the second copy follows.
- Each unattended backup is an import of its own: `2026_1013_import_01`, then
  `2026_1013_import_02` for the next one that day.
- **What to copy** and the battery rules apply: below 10% it does not start, and the screen
  says why.
- **Dark screen between backups** (on): a minute after the last touch on the result, the
  screen goes dark. The tablet stays awake: plugging in the camera, or a touch, lights it.
- Formatting the camera's cards is never done for you. The cards tile says whether it is safe.
- The backup beeps (below) say when it starts and when it is done.
- Switching it on sets **On battery, sleep after** (Settings, Display) to Never: the tablet
  waits awake with the screen dark, because plugging in the camera may not wake a sleeping
  tablet (that depends on its firmware). If you choose a sleep time anyway, press the power
  button before plugging in; the countdown starts as soon as the screens are back. While
  photos are being copied, the tablet never goes to sleep.

### Backup beeps

Settings, Backup rules, **Backup beeps** (on). Every backup, whether started on the
screen, with P1 or by itself when the camera is plugged in:

- **One beep:** the backup has started.
- **Two beeps:** it is done, every photo copied and checked. Also straight away when P1
  finds nothing new on the camera.
- **Three low beeps:** it needs a look: it could not start (no camera, a battery too low),
  or it stopped with a problem. The screen says what.
- A backup you stop yourself makes no sound.

### What to copy

Settings, Backup rules, **What to copy**:

- **RAW only:** RAW files (NEF, CR3, ARW, DNG and the rest) and anything that belongs to
  one, such as a voice memo. JPEGs and videos stay on the camera.
- **RAW + JPEG** (the default): RAW and JPEG photos. Videos stay on the camera.
- **Everything:** every file in the camera's DCIM folder, videos too.

What is left on the camera is listed on the Ready screen. The camera cards are never called
safe to format while anything is left on them: the card tile says the copied photos are
safe, and how many JPEGs or videos would be lost by formatting. Changing the setting reads
the camera again.

One exception, for cameras that cannot shoot RAW alone (the Insta360 Ace Pro 2 offers only
RAW + JPEG, or JPEG): with **RAW only**, the JPEG the camera made beside each RAW is not
needed. It does not keep the cards from being safe to format once its RAW is on Copy 1 and
Copy 2. It is read once, straight from the camera, to make the RAW's thumbnail and screen
picture (the camera's own colours), and is not kept: it never lands in the shoot folder, on
Copy 2 or on the NAS. A JPEG with no RAW beside it still counts as left on the camera.

### Cameras that plug in as a drive, and cards in a reader

The **Insta360 Ace Pro 2**, in its U-Disk mode, shows up as a drive rather than as a camera.
DoubleTake recognises it and reads it like a camera: from its DCIM folder only, never
writing to it, and never offering it as the second copy or for exports. Set What to copy
to RAW only to take just its DNG photos.

**A camera's card in a card reader**, or in the tablet's own card slot, is read the same
way. Any card or drive with a DCIM folder is a camera's card: it shows as **Camera card**
(with its label, such as NIKON D850, when the camera set one), is backed up like a camera,
and is never offered as Copy 2 or to be prepared. DoubleTake refuses to erase it even if
asked. A reader is often much faster than the camera's own USB, as with the Ace Pro 2.
Photos are recognised whichever way they come: a card backed up through the camera and
later put in a reader is not copied again. There is no battery to show for a card.

### Batteries

The top bar shows the camera's battery, next to the camera icon, and each of the tablet's
batteries, next to the tablet icon (the 7220 has two bays).
Each is an upright icon filled to its level, with no numbers:

| Level | Icon |
|---|---|
| above 30% | green |
| 30% to 20% | amber |
| below 20% | red |
| 5% or below | red, flashing (a steady red outline when the system is set to reduce motion) |

Tap either group for Settings, Device, Batteries, which gives each battery's exact level,
what it is doing, and its health: how much it holds compared with when it was new.

**Swap** appears next to the camera's battery at 5% or below, and next to the tablet's when
one of its batteries is at 10% or below while another still has charge. On a tablet with
two batteries the copy carries on while you swap the empty one.

**Below 10%, DoubleTake will not start a copy**:

- A backup does not start with the camera at 10% or below. Charge it or swap in a fresh
  battery, then tap Start.
- Nor with the tablet at 10% or below when it is not on its charger. Plug it in to start.
- If the camera gets to 10% during a backup, the copy stops cleanly after the photo it is
  on. Every photo copied so far is verified and kept. Swap the battery, plug the camera in
  again, and tap **Continue backup**: it carries on where it stopped. The same happens if
  the tablet gets to 10% off its charger.

### NAS

DoubleTake's built-in NAS shares `~/Pictures/DoubleTake` with your editing workstation, so any
photo app that imports from a network folder can bring the photos straight in: dock the tablet
at home, or plug a cable straight in (FSDC), open the share, import.

- **Read-only.** Nothing on the computer can change or delete the tablet's photos. Importing
  only reads them. Clearing shoots stays on the tablet.
- **Its own login.** Settings, NAS, Login: a username and password for the share only.
  That login cannot sign in to the tablet itself.
- **Connecting:** use the tablet's name (its host name), shown in Settings, NAS,
  Connect from a computer, along with its addresses:
  - Mac: Finder, Go, Connect to Server, `smb://<tablet-name>.local`. The tablet also
    shows up in Finder's sidebar.
  - Windows: type `\\<tablet-name>\DoubleTake` in File Explorer's address bar.
  - Linux: Files, Other Locations, `smb://<tablet-name>.local/DoubleTake`.
  Only the `DoubleTake` share is offered; Samba's built-in home-folder and printer
  shares are switched off.
- **SFTP**, for FTP-style apps such as Cyberduck, Transmit or FileZilla: switch it on in
  the same place, then connect to `sftp://<username>@<address>:2222`. Also read-only,
  and it shows only the photos.
- **Allowed on:** the share can be seen only on the cable, the tablet's hotspot, and the
  Wi-Fi networks switched on in the list. Anywhere else the firewall keeps it closed,
  even when the app is not running. Only password-protected networks can be trusted,
  because an open network can be imitated by anyone. Switch the cable off if you plug
  into a hotel or office port.
- **The NAS tile** on the home screen shows whether it is on, which computers are
  connected, the file being read now and how fast.

Over the dock's cable, expect roughly 70 to 110 MB/s. Over the hotspot it is much
slower, which is fine for a few selects in the field.

Setup does the one-time part with your password: it installs Samba, opens the share
and SFTP in the firewall's trusted zones only, sets SELinux to let Samba read the
photos, and installs a small helper (`/usr/local/libexec/doubletake-helper`) that
the screens may run without a password. The helper does a short fixed list of jobs
and checks everything it is given. The NAS stays off until it is switched on.

### Bluetooth

For a keyboard, a mouse or the like. Switch it on, tap Look for devices with yours in
pairing mode, then Pair. A keyboard may show a number to type on it; the screen says so.
Paired devices can be connected, disconnected or removed. The camera is not paired here:
cameras use Bluetooth only for remote control and small previews, and photos come over USB.

### Device

- **Exit to the desktop:** closes the screens, after asking. Anything part-way is picked
  up again next time. Start them again from the app list, or with `doubletake gui`.
  Not shown in DoubleTake only, where there is no desktop to exit to.
- **DoubleTake password:** a password for DoubleTake alone, separate from the tablet's
  own. Once set, it is asked for Maintenance mode, Exit, switching DoubleTake only off,
  and Format, Reset settings and Erase everything; nothing else asks for it. It needs at
  least 10 characters and is checked against common passwords; a few words with spaces is
  easy to type on the screen keyboard and hard to guess. After five wrong tries, each next
  try waits longer, from 30 seconds up to 15 minutes, even after a restart. Changing it, or
  choosing **No password** (with a warning), asks for the current one.
  If it is lost or forgotten, see [The DoubleTake password](#the-doubletake-password) below.
- **DoubleTake only:** from the next login, the tablet runs GNOME Kiosk instead of the
  desktop: DoubleTake full screen, with no top bar, app list or windows behind it. If
  Firefox ever closes or crashes, the screens open again by themselves. Together with
  Start at power-on, the tablet goes from the power button straight to the home screen.
  After switching it on, it offers to log out so it starts at once. Files and Terminal
  are hidden while it is on. Setup installs the kiosk session (`gnome-kiosk` and
  `gnome-kiosk-script-session`); the switch stays greyed out until it is there.
- **Maintenance mode** (in DoubleTake only): logs out to the plain desktop for one visit,
  for upgrades and settings. On the way, the screen lists each step as it is done, with a
  green OK: Copy 2 stopped at a safe point, the records saved, the next login switched to
  the desktop. The list holds for a few seconds once everything is done. The screen then
  goes black for 20 seconds or so while the desktop starts: that is normal. DoubleTake stays closed there; the wallpaper and a
  notification say it is maintenance mode, and the dock has a **Restart to DoubleTake**
  button. DoubleTake only stays on, so restarting, or simply logging out, returns to
  DoubleTake, and the desktop's own wallpaper is put back. Use it before upgrading,
  because setup.sh runs in a terminal. The DoubleTake icon in the app list still opens
  the screens on the desktop if you want them.
- **Cards and drives in DoubleTake only** are opened by DoubleTake itself, since
  there is no desktop to do it.
- **Restart, Shut down:** after asking. Not while a backup from the camera is running;
  the second copy is stopped at a safe point first and carries on after the next start.
  The steps are listed on the screen as they are done, as for Maintenance mode.
- **Tablet status:** the same panel as the 7220's P3 key (see
  [The tablet's own keys](#the-tablets-own-keys-dell-latitude-7220)).
- **Screen flicker fix** (Intel graphics only): some Intel screens flicker on Linux. The
  usual causes are two power savings in the graphics driver: Panel Self Refresh, and the
  display's deep idle states. On the 7220 it took both. Switching this on turns both off
  for every kernel, the Maintenance entry in the boot menu included (it adds
  `i915.enable_psr=0 i915.enable_dc=0` to the kernel's settings, and nothing else). It
  costs a little battery. It takes effect after a restart, which it offers, and asks for
  the DoubleTake password. Switching it off puts the system's own choice back. By hand,
  the same is `sudo grubby --update-kernel=ALL --args="i915.enable_psr=0 i915.enable_dc=0"`
  (and `--remove-args` to undo); `cat /proc/cmdline` shows whether it is in use. If
  Panel Self Refresh alone is enough on a tablet, set just that one by hand.
- **About:** the version, the tablet, the system, the camera and image libraries, and the
  three folders.
- **Start at power-on:** the tablet logs in by itself, with no password, and opens
  DoubleTake full screen. The screen does not lock or go dark. Switching it off puts the
  login password, the lock screen and the desktop's own blanking settings back. The NAS
  keeps its own login either way.
- **Memory card, Format:** erases the microSD card and starts it again as an empty
  second copy (exFAT). It says first how many photos on it are not yet imported
  and whether it holds files that are not the app's. The camera's cards are formatted
  in the camera, as always.
- **Reset settings:** every setting back to how it came, and the NAS switched off with
  its login removed. Photos and records stay, and so does where they are kept.
- **Erase everything:** every photo and record on the tablet, and the settings. While
  any shoot is not marked as imported, it asks you to type ERASE. The
  microSD card and the camera's cards are left as they are.

The settings are saved to `~/.config/doubletake/config.json`.

### Updates

Updates are looked for only in Maintenance mode, when the desktop is up and DoubleTake is
closed, so nothing is ever being copied while packages change. In the field nothing is
looked for.

- At a Maintenance visit, when the tablet is due a look (every 30 days, or every 60 or
  90: Settings, Device, Updates), a terminal opens and asks **Check for updates now?**
- **Off:** nothing is looked for at a Maintenance visit, not even security fixes. Keep the
  tablet up to date yourself, with `doubletake update` in a terminal.
- It looks for the system's own updates (Rocky, Red Hat or Fedora) and says how many are
  waiting, how many are security fixes, and whether there is a new kernel. **Install them
  now?** asks for the tablet's password and installs them.
- It looks for a newer DoubleTake on GitHub. If there is one, it downloads the package for
  this system and install.sh into `~/Downloads/DoubleTake-<version>`, checks them against
  the release's checksums, and installs it. Photos, records and settings are kept.
- When something needs it (a new kernel, or a new DoubleTake), it offers a restart, which
  comes up in DoubleTake.
- A security fix waiting opens the terminal at any Maintenance visit, whatever the 30 or
  60 days.
- Any time, in a terminal: `doubletake update`.

### The tablet's own keys (Dell Latitude 7220)

The three keys on the 7220's frame do one job each, whatever is on the screen:

- **P1, one-touch backup:** with the camera plugged in, the new photos are backed up at
  once, just as with Back up on the screen, and the screen shows it as usual. The beeps
  follow it: one at the start, two when done, three low ones if it cannot start or needs
  a look. Pressing P1 again during a backup does nothing, so a second press is harmless.
  P1 works whenever the screen is on; a sleeping tablet is woken with the power button.
- **P2, red:** the screens turn red on black, keeping your night vision: Cockpit Red in
  the Cockpit look, Night Red in the Classic one. Again, and the theme you had
  comes back.
- **P3, the tablet's status:** a panel over whatever is on the screen: DoubleTake's
  version and the system, the number of shoots and photos, the space the photos take on
  Copy 1 and Copy 2 and the space free on each, and each battery's charge, whether it is
  charging, its health (what it holds now against when it was new) and its charge
  cycles, with the time left at the rate it is going. A tap anywhere, or P3 again, closes
  it. On any tablet, Settings, Device, **Tablet status** shows the same panel.

The volume and brightness keys work in DoubleTake only too (the kiosk session has no
handler of its own for them): the screens pass them on, and a small bar shows the level
for a moment. Volume never goes above 100%, and brightness never down to black.

The keys send one tap each, however long they are held. None of them leaves DoubleTake:
Maintenance mode is in Settings, Device, behind the DoubleTake password, and never on a key.

### Screenshots

The screens cannot take a screenshot themselves: the session that runs DoubleTake only has
no Print Screen. In Maintenance mode, open a terminal and run `doubletake gui --window`,
press **F11** to make Firefox full screen, then **Shift + Print Screen**. The pictures go
to Pictures, Screenshots. Restart the tablet to go back to DoubleTake.

## Commands

| Command | What it does |
| --- | --- |
| `doubletake gui` | Opens the touch screens, full screen (`--window` for a window, `--limit 50` for short tests) |
| `doubletake doctor` | Checks the tablet, the camera library and the camera |
| `doubletake camera` | Shows the camera and what is new on its card |
| `doubletake speedtest` | Measures how fast the camera hands over files |
| `doubletake backup` | Copies and verifies new photos, then makes the second copy |
| `doubletake card` | Shows the card used for the second copy |
| `doubletake card use` | Uses the inserted card, for example after saying no earlier |
| `doubletake card ignore` | Stops using the inserted card. Nothing on it is changed |
| `doubletake mirror` | Brings the second copy up to date |
| `doubletake shoots` | Lists shoots, with how many files are in the second copy |
| `doubletake verify [shoot]` | Re-checks shoots on the tablet against their checksums |
| `doubletake verify --card` | Re-checks the second copy on the card |
| `doubletake config` | Shows the settings in use and where the settings file is |

Useful options for `backup`:

- `--name Airshow` names the shoot
- `--limit 20` copies at most 20 files
- `--all` copies every photo on the card, not only new ones
- `--date 2026-10-11` sets the date in the folder name (default: today)
- `--source dir:/some/folder` backs up from a folder, for testing without the camera

## Where things go

    ~/Pictures/DoubleTake/
      2026_1011_airshow_01/
        DSC_4821.NEF
        manifest.json            the backup report for this shoot

    ~/Documents/DoubleTake/
      ledger.sqlite              the record of what has been backed up
      logs/                      one log per backup, the last 20 kept
      previews/                  thumbnails and screen-size pictures for the viewer

On the microSD card, the same layout inside one folder. Anything else on the card is
left alone.

    <card>/DoubleTake/
      2026_1011_airshow_01/
        DSC_4821.NEF
        manifest.json
      .doubletake/card.json      marks the card, so it is recognised next time

Settings live in `~/.config/doubletake/config.json`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `library` | `~/Pictures/DoubleTake` | Where shoots are stored |
| `data_dir` | `~/Documents/DoubleTake` | Where the records are kept |
| `download_method` | `auto` | `auto`, `chunked` or `whole` (see `speedtest`) |
| `chunk_mb` | `8` | Size of each read when reading in pieces |
| `second_copy` | `auto` | `auto` uses a card found in the tablet, `off` switches the second copy off, a folder path uses that folder |
| `second_copy_folder` | `DoubleTake` | The app's folder on the card |
| `quick_names` | none: add your own in Settings, Backup rules | Shoot names offered as one-tap buttons |
| `theme` | `daylight` | `daylight`, `slate`, `night` or `nightred` |
| `port` | `8765` | Port the screens are served on, to this tablet only |
| `hotspot_name` | `DoubleTake` | Name of the tablet's own Wi-Fi network |
| `hotspot_password` | made at first use | Its password, at least 8 characters |
| `nas_cable` | `true` | The NAS may be seen on a network cable |
| `nas_hotspot` | `true` | The NAS may be seen on the tablet's hotspot |
| `nas_trusted` | none | Wi-Fi networks, by name, where the NAS may be seen |

## Undo

Installed from the package: `sudo dnf remove doubletake`, then
`systemctl --user unmask gvfs-gphoto2-volume-monitor.service`.

Installed by setup.sh:

    sudo /usr/local/libexec/doubletake-helper reset     # NAS off, its login and automatic login removed
    sudo rm /usr/local/libexec/doubletake-helper /etc/sudoers.d/doubletake
    rm ~/.local/bin/doubletake
    rm -f ~/.local/share/applications/doubletake.desktop ~/.config/autostart/doubletake.desktop
    rm -r ~/.local/share/doubletake ~/DoubleTake
    systemctl --user unmask gvfs-gphoto2-volume-monitor.service

Your photos in `~/Pictures/DoubleTake` and the records in `~/Documents/DoubleTake`
are left alone.

