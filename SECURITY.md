# Security

DoubleTake runs on a tablet that holds someone's only copies of their photos, and part of it
runs as root. Security problems are taken seriously.

## Reporting a problem

Please report anything you think is a security problem privately, through GitHub's
**Report a vulnerability** button on this repository's Security tab, rather than in a public
issue. Include what you found, how to see it happen, and the version (Settings, Device,
About, or `doubletake --version`).

You should hear back within a week. A confirmed problem is fixed in a new release, which
Maintenance mode's updater offers to every tablet that looks for updates, and the release
notes say what was fixed once the fix is out.

## Supported versions

Only the newest release is supported. Updates are offered in Maintenance mode, and
`doubletake update` installs one at any time.

## What DoubleTake does to stay safe

The full design is in [How it works](docs/HOW-IT-WORKS.md#security). In short:

- **The screens are local only.** The web server listens on 127.0.0.1, refuses requests
  that do not name a local host, and needs a custom header on every button press, so
  another web page cannot press them.
- **One root helper with a short, fixed list of jobs.** It is self-contained, imports
  nothing from a folder the user can write to, and checks every argument: user names,
  network names, device names (never a system drive or a camera's card), and paths.
- **The camera is never written to.** Photos and the battery level are read; nothing else.
- **Nothing is erased without a typed confirmation.** Preparing a card or drive needs
  AGREE typed, and the DoubleTake password when one is set.
- **The NAS is read-only** (Samba and SFTP), with a login of its own that cannot sign in to
  the tablet, and it opens only on networks marked as trusted. Elsewhere the firewall keeps
  it closed, even when DoubleTake is not running.
- **Send to phone** runs only while sharing, on the tablet's own hotspot, behind a fresh
  random code, read-only, and stops by itself after 30 minutes.
- **The P keys and the brightness keys** are read only from devices that have no letter
  keys, so typing is never seen.
- **Updates are checked** against the release's SHA-256 checksums before anything is
  installed, and nothing is installed without a yes.
