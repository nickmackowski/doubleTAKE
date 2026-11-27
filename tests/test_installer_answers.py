"""The installer's text step (iso/doubletake.ks, the second %pre), run in a sandbox.

Above all a stick made with build-iso.sh --no-keyboard: it must erase only after the
countdown, and never once the stick is pulled out. The script runs
with its paths moved into a scratch folder and the system's tools replaced by small fakes
(nmcli joins only with the right password; poweroff ends the script).
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

KS = Path(__file__).resolve().parent.parent / "iso" / "doubletake.ks"
BASH = shutil.which("bash")
HOTSPOT = "Nick’s iPhone"  # as an iPhone names its hotspot (a curly apostrophe)

FAKES = {
    # nmcli: "online" once a connect with the right password happened (or ONLINE=1).
    "nmcli": r"""#!/bin/bash
echo "nmcli $*" >> "$SANDBOX/calls"
case "$*" in
  "-t networking connectivity check")
    if [ -f "$SANDBOX/online" ] || [ "${ONLINE:-}" = 1 ]; then echo full; else echo none; fi ;;
  "-t -f SSID device wifi list --rescan no") printf '%s\n' "$SEEN" ;;
  "-t -f DEVICE,TYPE,STATE device") echo "wlp1s0:wifi:disconnected" ;;
  "-t -f TYPE device") echo wifi ;;
  device\ wifi\ connect*)
    if [ "$4" = "$GOOD_SSID" ] && { [ "$5 $6" = "password $GOOD_PASSWORD" ] || [ -z "$GOOD_PASSWORD" ]; }; then
      touch "$SANDBOX/online"
    fi ;;
esac
exit 0
""",
    "findmnt": "#!/bin/bash\necho /dev/sda\n",
    "lsblk": "#!/bin/bash\necho\n",
    # poweroff ends the script; with POWEROFF_FAILS it fails, as it may once the stick is out.
    "poweroff": "#!/bin/bash\necho POWEROFF >> \"$SANDBOX/calls\"\n[ -n \"${POWEROFF_FAILS:-}\" ] && exit 1\nkill -9 $PPID\n",
    # Each screen starts with clear: after PULL_AT screens, the stick is pulled out.
    "clear": """#!/bin/bash
n=$(( $(cat "$SANDBOX/clears" 2>/dev/null || echo 0) + 1 )); echo $n > "$SANDBOX/clears"
if [ -n "${PULL_AT:-}" ] && [ "$n" -ge "$PULL_AT" ]; then rm -rf "$SANDBOX/sys/block/sda"; fi
""",
}
for quiet in ("chvt", "setfont", "chronyc", "chronyd", "hwclock", "sleep", "timeout", "mount", "umount"):
    FAKES[quiet] = "#!/bin/bash\nexit 0\n"


def section() -> str:
    """The second %pre's body: the text step."""
    found = re.findall(r"^%pre[^\n]*\n(.*?)^%end", KS.read_text(), re.S | re.M)
    return found[1]


@unittest.skipUnless(BASH and shutil.which("openssl"), "needs bash and openssl")
class TextStep(unittest.TestCase):
    def setUp(self):
        self.box = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.box, True)
        (self.box / "bin").mkdir()
        for name, body in FAKES.items():
            path = self.box / "bin" / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        (self.box / "repo" / "doubletake").mkdir(parents=True)
        (self.box / "sys" / "block" / "sda").mkdir(parents=True)
        (self.box / "tmp").mkdir()
        (self.box / "tmp" / "doubletake-disk.txt").write_text("SAMSUNG SSD (/dev/nvme0n1), 256 GB\n")
        (self.box / "tmp" / "doubletake-name.txt").write_text("dt-a1b2c3\n")
        (self.box / "tmp" / "doubletake-tag.txt").write_text("a1b2c3\n")
        (self.box / "screen").write_text("")

    def answers(self, wifi=HOTSPOT.replace("’", "'"), password="hotspot99", model="FZ-M1"):
        hashed = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input="tablet1", text=True,
                                capture_output=True, check=True).stdout.strip()
        (self.box / "repo" / "doubletake" / "answers.conf").write_text(
            f"WIFI_NAME={wifi}\nWIFI_PASSWORD={password}\nPASSWORD_HASH={hashed}\n"
            f"ZONE=America/New_York\nMODEL={model}\n")
        return hashed

    def run_step(self, product="FZ-M1FFZZZEM", keys="", timeout=60, **env):
        script = section()
        box = str(self.box)
        for old, new in (("/run/install/repo", f"{box}/repo"), ("/sys/block/", f"{box}/sys/block/"),
                         ("/sys/class/dmi/id/product_name", f"{box}/product"),
                         ("/tmp/doubletake-", f"{box}/tmp/doubletake-"),
                         ("</dev/tty6 >/dev/tty6", f"<{box}/keys >>{box}/screen"),
                         ("</dev/tty1 >/dev/tty1", f"</dev/null >>{box}/screen"),
                         ("https://geoip.fedoraproject.org/city", "http://127.0.0.1:9/"),
                         ('[ -b "$source_dev" ]', '[ -n "$source_dev" ]'),
                         ("/proc/sysrq-trigger", f"{box}/sysrq"),
                         ("left = 30", "left = 5")):
            self.assertIn(old, script)
            script = script.replace(old, new)
        (self.box / "product").write_text(product + "\n")
        (self.box / "keys").write_text(keys)
        environment = dict(os.environ, PATH=f"{box}/bin:{os.environ['PATH']}", SANDBOX=box,
                           SEEN=f"Neighbours\n{HOTSPOT}", GOOD_SSID=HOTSPOT, GOOD_PASSWORD="hotspot99")
        environment.update(env)
        subprocess.run([BASH, "-c", script], env=environment, timeout=timeout, capture_output=True)
        screen = (self.box / "screen").read_text()
        calls = (self.box / "calls").read_text() if (self.box / "calls").exists() else ""
        return screen, calls

    def kickstart(self, name):
        return (self.box / "tmp" / f"doubletake-{name}.ks").read_text()

    def test_no_keyboard_installs_by_itself_on_its_model(self):
        hashed = self.answers()
        screen, calls = self.run_step()
        self.assertIn(f"device wifi connect {HOTSPOT} password hotspot99", calls)  # ' matched to ’
        self.assertIn("ERASED IN 5 SECONDS", screen)
        self.assertIn("ERASED IN 1 SECONDS", screen)
        self.assertIn("#####", screen)  # the seconds in big digits
        self.assertIn("The installer takes it from here", screen)
        self.assertNotIn("POWEROFF", calls)
        self.assertEqual(self.kickstart("user").strip(),
                         f"user --name=a1b2c3 --gecos=a1b2c3 --groups=wheel --iscrypted --password={hashed}")
        self.assertEqual(self.kickstart("time").strip(), "timezone America/New_York --utc")
        self.assertNotIn("Type ERASE", screen)

    def test_pulling_the_stick_stops_it_before_anything_is_erased(self):
        self.answers()
        screen, calls = self.run_step(PULL_AT="4")
        self.assertIn("nothing was erased", screen)
        self.assertIn("POWEROFF", calls)
        self.assertNotIn("The installer takes it from here", screen)

    def test_a_pulled_stick_never_reaches_the_erase_even_if_poweroff_fails(self):
        self.answers()
        with self.assertRaises(subprocess.TimeoutExpired):  # the step stops for good
            self.run_step(PULL_AT="4", POWEROFF_FAILS="1", timeout=3)
        screen = (self.box / "screen").read_text()
        self.assertIn("nothing was erased", screen)
        self.assertNotIn("The installer takes it from here", screen)
        self.assertEqual((self.box / "sysrq").read_text().strip(), "o")

    def test_any_tablet_counts_down_with_no_typing(self):
        # Nick, 2026-11-20: no model check on a no-keyboard stick (his FZ-M1 was asked ERASE).
        self.answers()
        screen, calls = self.run_step(product="Latitude 7220 Rugged Extreme")
        self.assertIn("ERASED IN 5 SECONDS", screen)
        self.assertNotIn("Type ERASE", screen)
        self.assertIn("The installer takes it from here", screen)

    def test_counts_down_even_when_the_stick_cannot_be_watched(self):
        self.answers()
        (self.box / "bin" / "findmnt").write_text("#!/bin/bash\nexit 1\n")
        screen, calls = self.run_step()
        self.assertIn("hold the power button", screen)
        self.assertNotIn("Type ERASE", screen)
        self.assertIn("The installer takes it from here", screen)

    def test_a_normal_stick_still_has_to_type_erase(self):
        screen, calls = self.run_step(ONLINE="1", keys="\ntablet1\ntablet1\n1\n")
        self.assertIn("Type ERASE", screen)
        self.assertNotIn("ERASED IN", screen)
        self.assertNotIn("The installer takes it from here", screen)  # no ERASE typed: it waits

    def test_a_key_during_the_countdown_goes_to_the_usual_question(self):
        self.answers()
        screen, calls = self.run_step(keys="x")
        self.assertIn("ERASED IN 5 SECONDS", screen)
        self.assertIn("Type ERASE", screen)
        self.assertNotIn("The installer takes it from here", screen)

    def test_wrong_wifi_password_keeps_trying_and_never_erases(self):
        self.answers(password="wrongpass1")
        script_timeout = None
        try:
            self.run_step(timeout=4, GOOD_PASSWORD="hotspot99")
        except subprocess.TimeoutExpired as stopped:
            script_timeout = stopped
        screen = (self.box / "screen").read_text()
        self.assertIsNotNone(script_timeout)
        self.assertIn("did not connect", screen)
        self.assertNotIn("ERASED IN", screen)

    def test_a_stick_without_answers_asks_as_before(self):
        screen, calls = self.run_step(ONLINE="1", keys="\ntablet1\ntablet1\n1\nERASE\n")
        self.assertIn("this tablet's user", screen)
        self.assertIn("Type ERASE", screen)
        self.assertNotIn("ERASED IN", screen)
        self.assertIn("The installer takes it from here", screen)
        self.assertIn("--name=a1b2c3", self.kickstart("user"))
        self.assertEqual(self.kickstart("time").strip(), "timezone America/New_York --utc")


if __name__ == "__main__":
    unittest.main()
