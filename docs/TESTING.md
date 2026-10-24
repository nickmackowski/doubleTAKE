# Testing DoubleTake on a tablet

A step-by-step first test with a camera, a test of the second copy, and the automated
tests. The steps use the command line so each part can be checked on its own; the touch
screens do the same things.

## First test, in order

Plug the D850 into the tablet and switch it on.

1. **Check the tablet is ready**

       doubletake doctor

   Every line should say `ok`. If the camera line says another program has taken
   hold of it, open Files, eject the camera, and run the command again.

2. **See what is on the card**

       doubletake camera

   Shows the model, serial number, battery, card space, and how many photos are new.

3. **Measure the transfer speed**

       doubletake speedtest

   Reads 10 photos two different ways and prints the speed of each. Nothing is
   written. This is the number the whole design has been waiting for.

4. **Back up a few photos**

       doubletake backup --name test --limit 20

   Copies the 20 oldest new photos into `~/Pictures/DoubleTake/2026/<date>_test/`.

5. **Back up the rest**

       doubletake backup --name Airshow

   Only photos that are not already on the tablet are copied. If you leave out
   `--name`, it asks for one.

6. **Try an interruption**

   Start a backup, then unplug the cable part-way through. Plug it back in and run
   the same command again. It continues where it stopped.

7. **Re-check everything on the tablet**

       doubletake verify

## Testing the second copy

Put a microSD card in the tablet and wait for it to show up in Files. The card does
not need to be empty.

1. **See the card**

       doubletake card

   It should say a new microSD card was found, with its size and free space.

2. **Copy a few photos to it**

       doubletake mirror --limit 20

   The first time, it says what it is about to do and asks whether to use the card.
   Press Enter to accept. It then copies 20 photos from the tablet to the card and
   reads each one back from the card. The summary shows the card's write and read
   speeds.

3. **Copy the rest**

       doubletake mirror

   Pull the card out part-way if you want to test an interruption, put it back, wait
   for it to show up in Files, and run the command again.

4. **Re-check the whole card**

       doubletake verify --card

   Reads every file on the card again and compares it with its checksum. Do this once
   with any card that is new to you. A counterfeit card that is smaller than it claims
   can pass the check made while copying, and fails this one.

5. **Ask whether the camera's cards can be formatted**

   Plug in the camera and run

       doubletake camera

   The last lines say how many photos are on the camera's cards, how many are verified
   on the tablet, and how many are verified on the second copy too.

From now on `doubletake backup` does both steps: camera to tablet, then tablet to
card. The camera can be unplugged as soon as the first step is done.

## What to send back

Paste the output of `doubletake doctor`, `doubletake speedtest` and the summary
at the end of a backup. If something goes wrong, the run logs are in
`~/Documents/DoubleTake/logs/`.

## Automated tests

On the tablet, after setup:

    cd ~/DoubleTake
    ~/.local/share/doubletake/venv/bin/python -m unittest discover -s tests -v

Or from a clone on any Linux machine with Python 3.9 or newer. No camera, card or root
is needed: cameras, cards, NetworkManager, Bluetooth and the system helper are all
stood in for.

    python3 -m unittest discover -s tests -v

Tests that need Pillow or the camera library are skipped when they are not installed.
