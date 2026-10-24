# How DoubleTake works

The design behind the app: how a backup is verified, when the camera's cards may be
formatted, how the parts fit together, and what the app is and is not allowed to do.

## The promises

These hold everywhere in the code, and the tests check them:

- **Edits never touch a photo.** Quick edits are kept as a small settings file per photo,
  and an export is a new JPEG in its own Exports folder.
- **The camera is only read.** Nothing is ever written to a camera: not its cards, not its
  settings, not its clock. DoubleTake reads the photos and the battery level. The camera's
  cards are formatted in the camera.
- **The second copy's card or drive is never erased behind your back.** It is erased
  only when you choose **Prepare for DoubleTake** for a new card or drive (its name, size
  and what is on it shown first, then Erase it, then Type AGREE, plus the password if one
  is set), or by Settings, Device, Memory card, Format, which asks first and says what
  would be lost. Otherwise the app writes only inside its own folder there (`DoubleTake/`).
- **"Safe to format" is never shown too early.** See below.
- **Nothing is deleted by itself.** A shoot is deleted only when you choose it in the shoot
  library and confirm, never within a day of its last photo being copied, and only the
  files the app itself copied.
- **The NAS is read-only, has its own login, and is closed on networks you have not
  trusted.**

## Verified, step by step

**Camera to tablet.** For every file, as it lands on the tablet:

- its SHA-256 checksum is computed from the bytes the camera sent, and recorded in the
  ledger (an SQLite database in `~/Documents/DoubleTake`)
- its size is compared with the size the camera reported
- the start of the file is checked against its type (RAW, JPEG or video)

A file that fails any of these is reported as failed, is not kept, and is tried again on
the next backup. Files are written under a temporary name and renamed only once they
pass, so an interrupted backup never leaves a half-written photo that looks complete.
Unplugging the camera part-way is safe: the next backup carries on from where it stopped.

**Tablet to microSD card.** For every file, in the background after the camera is done:

- it is written under a temporary name and flushed to the card
- the tablet's cache of it is dropped, so the next step really reads the card
- it is read back from the card, and the checksum of what the card returned is compared
  with the one recorded when the photo came off the camera
- only then is it given its real name and counted as a second copy

A file whose copy on the tablet no longer matches its checksum is not passed on to the
card; it is reported instead. `doubletake verify --card` re-reads the whole card, which
also catches counterfeit cards that are smaller than they claim.

## Safe to format

The Camera Cards tile turns green, and the backup reports the cards as safe to format,
only when **every photo on the camera's cards** is:

- verified on Copy 1 (the tablet), **and**
- verified on Copy 2.

Nothing else counts: DoubleTake's job is the two copies, and what happens to the photos
after that, on an editing workstation or anywhere else, is the photographer's. A shoot
marked as imported only allows it to be cleared from the tablet. Photos backed up once and
cleared from the tablet since are on neither copy: if they are still on the camera's
cards, the tile says so and does not call the cards safe; they are not copied again.

In any other case it says not to format, with the reason and the number of photos
still waiting. With no card in the tablet, a backup still runs and says that it exists
only on the tablet; the second copy catches up when a card is put in.

## The parts

```
 Camera (USB)            microSD card                 Your computer
     |                        ^                             ^
     | read only              | write + read back           | read only (SMB / SFTP)
     v                        |                             |
 +---------------------- DoubleTake engine (Python) ----------------------+
 |  watcher (every 2 s): camera, card, network, NAS status                   |
 |  backup thread  ->  ~/Pictures/DoubleTake   (photos, one folder a shoot)|
 |  mirror thread  ->  microSD card                                          |
 |  ledger         ->  ~/Documents/DoubleTake  (checksums, copies, logs)  |
 |  web server on 127.0.0.1 only                                             |
 +---------------------------------------------------------------------------+
        ^                                     |
        | touch screens (Firefox, full screen) | sudo, one fixed helper
        |                                     v
     the tablet's screen          /usr/local/libexec/doubletake-helper
                                  (Samba, SFTP, firewall zones, SELinux,
                                   card format, automatic login)
```

- **Engine** (`doubletake/`): plain Python 3.9+ with the standard library, plus
  python-gphoto2 for the camera and Pillow for previews. Each thread has its own
  database connection. Long jobs stop cleanly when asked, at a safe point.
- **Screens** (`doubletake/web/`): HTML, CSS and JavaScript with no framework and no
  network access, served by the engine to the tablet itself. They ask the engine for its
  state a little more than once a second. Closing them never stops a backup.
- **Camera access** goes through libgphoto2, which works with most cameras that speak
  the standard still-image protocol (PTP). The D850 is the one it is developed with.
- **Network** settings go through NetworkManager (`nmcli`), and Bluetooth through BlueZ
  (`bluetoothctl`), so they match what the desktop shows.
- **Direct cable:** when a network port has a cable with a live link but no network for
  20 seconds, the engine starts a NetworkManager connection named `DoubleTake-Direct` on
  that port with link-local addresses (IPv4 169.254.0.0/16 and IPv6 fe80::), as a laptop
  does by itself when no router answers. It never connects by itself, and pulling the cable
  drops it, so the next cable tries for a network first. Link-local was chosen over
  NetworkManager's "shared" mode because shared mode runs an address server, which could
  hand out addresses on someone else's network if the cable were ever plugged into one.
- **DoubleTake only** uses GNOME Kiosk's script session. DoubleTake writes
  `~/.local/bin/gnome-kiosk-script`, which the session runs at login and which starts
  `doubletake gui`; while in that session the engine reopens Firefox whenever it closes.
  Which session the user logs in to is kept by the accounts service; the helper's
  `session kiosk|desktop` job sets it (over D-Bus, and in
  `/var/lib/AccountsService/users/<user>` for older versions), remembers the session
  it replaced, and puts that one back.
- **The boot screen** is a Plymouth theme in `/usr/share/plymouth/themes/doubletake`,
  drawn by Plymouth's script plugin: the app icon inside a ring picture that the script
  turns a little every frame, and the name below. Where the firmware says where it drew
  its own logo (the ACPI BGRT table, `/sys/firmware/acpi/bgrt`), the helper copies that
  logo in beside the theme and writes its place into the script: the boot screen shows it
  in the same spot on black, then fades it into the ring and icon, centred there and the
  same size. Without the table (a VM, older firmware), the screen is centred. Without the plugin, a still picture of
  the same (`watermark.png`) on the system spinner theme's images.
  The helper's `splash on|off` job switches to it with `plymouth-set-default-theme -R`
  (which rebuilds the start-up image) and back to the theme it replaced.
- **Batteries** are read every 20 seconds during a backup, between photos: the camera's
  through libgphoto2, the tablet's from `/sys/class/power_supply`. A stop for a battery is
  the same clean stop as the Stop button.
  The status panel (P3) reads each battery's health as its full charge now over its full
  charge when new (`energy_full` / `energy_full_design`), its `cycle_count`, and the time
  left from the rate it is draining or charging at that moment.
- **The P keys** on the 7220 come from the firmware's own small key device. The helper's
  `keys` job picks the device that has those keys and no letter keys, so it can never
  read typing, and prints one line per press for the engine.
- **The volume keys** reach the screens (Firefox has them while DoubleTake is up), which
  pass them to the engine, which changes the volume through PipeWire (`wpctl`, or `pactl`).
  No input device is read for them: on the 7220 they come from the built-in keyboard
  device, which the helper never reads.
- **The brightness keys** come from the screen's own device (the Video Bus), which has no
  letter keys, so the helper's `keys` job reads them with the P keys. In DoubleTake only,
  the engine steps the brightness through the desktop's power service, or through the
  login service (`SetBrightness`) when that is not there.
- **Beeps** are played by the screens: the engine counts each sound it wants (a backup's
  start, done, or a problem) and the screens play the new ones as they see them.
- **The way out** (Maintenance mode, restart, shut down) runs as a list of steps in the
  engine, each marked done or failed as it finishes; the screens draw the list. A failed
  step stops the list, and DoubleTake carries on as before.
- **The screen flicker fix** is the helper's `flicker on|off` job: `grubby
  --update-kernel=ALL` with exactly `i915.enable_psr=0 i915.enable_dc=0` added or removed, for Intel
  graphics only, and the Maintenance boot entry written again from the usual one.

## Security

- **The screens are local only.** The web server listens on 127.0.0.1, refuses requests
  that do not name a local host, and requires a custom header on every button press, so
  another web page cannot press them.
- **One root helper, with a short fixed list of jobs.** Setup installs
  `/usr/local/libexec/doubletake-helper` and a sudo rule that lets the tablet's user run
  that one file, and nothing else, without a password. The helper is self-contained (it
  imports nothing from the user-writable program folder), runs Python in isolated mode,
  and checks every argument: user names, network IDs, device names (never a system
  drive), and paths (the photos folder must be inside your home folder and not a link).
- **The NAS share is read-only** in Samba and in SFTP (a read-only bind mount, inside a
  chroot, with SFTP's own read-only mode). Its login is a system account with no shell
  that cannot sign in to the tablet. Samba's built-in home-folder and printer shares are
  switched off.
- **Trusted networks only.** The share and SFTP are opened only in the firewall's
  `home` zone and the hotspot's zone. Each saved network is put in `home` only if the
  settings trust it: the cable and the hotspot by default, and Wi-Fi networks only when
  switched on in the list and protected by a password. Everything else is `public`,
  where the share is closed, whether or not the app is running.
- **Send to phone** runs a second, separate small web server only while sharing. It listens
  on the hotspot's address alone, serves only the exported JPEGs chosen for it, needs a
  random code in the address, and stops after 30 minutes. The helper opens its port in the
  hotspot's firewall zone for that time only, without saving it, and closes it again.
- **The DoubleTake password** is kept only as a salted scrypt hash in
  `/etc/doubletake/password.json`, readable by root alone, and checked by the helper
  (`fs-password check`, the password on stdin). The engine asks it before the guarded
  buttons, on the engine's side, not only in the screens. Wrong tries are counted on disk;
  after five, each try waits from 30 seconds doubling to 15 minutes. `fs-password clear`
  refuses when run through the sudo rule the screens use, so clearing it takes a root
  shell, which takes the tablet's own password. It protects the touch screen; someone who
  can run commands as the tablet's user, or take the drive out, is beyond what it guards.
- **The direct cable** is in the firewall's `home` zone only while Network cable is trusted
  in the NAS settings, and `public` otherwise, like any other cable.
- **Start at power-on** turns off the login password, by your choice and after a warning.
  It is off by default.

## Files on disk

| Where | What |
| --- | --- |
| `~/DoubleTake` | The program. Replaced by each upgrade |
| `~/Pictures/DoubleTake` | The photos, one folder per import (`2026_1015_import_01`), each with a `manifest.json` |
| `~/Pictures/DoubleTake/Exports` | Exported JPEGs, one folder per shoot |
| `~/Documents/DoubleTake` | `ledger.sqlite`, run logs, thumbnails, and `edits/` (one small file per edited photo) |
| `~/.config/doubletake/config.json` | Settings |
| `~/.local/share/doubletake` | The Python environment and the kiosk Firefox profile |
| `<card>/DoubleTake` | The second copy, and `.doubletake/card.json`, which marks the card |
| `/etc/doubletake/nas.json` | What the helper has set up (no passwords) |
| `/etc/samba/doubletake.conf`, `/etc/ssh/doubletake_sftp_config` | The NAS |
