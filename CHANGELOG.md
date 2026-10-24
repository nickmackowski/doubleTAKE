# Changelog

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
- A new icon, and a boot screen to match that takes over from the maker's logo.
- An independent review before release, and the root helper hardened after it: the photos
  folder's path may only use plain characters, so it can never add settings to the share's
  or the mount's files; the tablet's built-in storage is never taken for the card slot; a
  drive is checked to be the same one just before it is erased; and the key reader ignores
  any device with letter keys.
