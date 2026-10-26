"""Tests for the system helper, against a stand-in system: nothing real is changed."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader("doubletake_helper", str(HERE.parent / "system" / "doubletake-helper"))
spec = importlib.util.spec_from_loader("doubletake_helper", loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)

def _owner():
    """A real account on the test machine, standing in for the tablet's user: whoever runs the
    tests, or, when that is root, the first ordinary account with a home folder."""
    import pwd

    me = pwd.getpwuid(os.getuid())
    if me.pw_uid != 0 and me.pw_dir.startswith("/home/"):
        return me.pw_name, me.pw_dir
    for p in sorted(pwd.getpwall(), key=lambda p: p.pw_uid):
        if p.pw_uid >= 1000 and p.pw_dir.startswith("/home/") and helper.OWNER_RE.match(p.pw_name):
            return p.pw_name, p.pw_dir
    raise unittest.SkipTest("no ordinary account on this machine to stand in for the tablet's user")


OWNER, HOME = _owner()
LIBRARY = f"{HOME}/Pictures/DoubleTake"


class Recorder:
    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def __call__(self, args, stdin=None, timeout=120):
        self.calls.append((list(args), stdin))
        joined = " ".join(args)
        for key, answer in self.answers.items():
            if key in joined:
                return answer
        if args[:2] == ["firewall-cmd", "--get-zones"]:
            return 0, "block dmz drop external home internal nm-shared public trusted work\n", ""
        if "connection.type" in joined:
            return 0, "802-11-wireless\n", ""
        return 0, "", ""

    def ran(self, *words):
        return any(all(w in " ".join(a) for w in words) for a, _ in self.calls)


class HelperBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="doubletake-root-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "etc/samba"))
        with open(os.path.join(self.root, "etc/samba/smb.conf"), "w") as fh:
            fh.write("[global]\n   workgroup = SAMBA\n\n[homes]\n   browseable = No\n")
        self.rec = Recorder()
        self.sys = helper.System(self.root, self.rec)
        self.env = {"SUDO_USER": OWNER}

    def job(self, *argv, stdin=None):
        out = []
        real_print = helper.print if hasattr(helper, "print") else print
        helper.print = lambda text: out.append(text)
        try:
            code = helper.main(list(argv), self.sys, self.env, stdin)
        finally:
            helper.print = real_print
        return code, json.loads(out[-1])

    def file(self, p):
        with open(os.path.join(self.root, p.lstrip("/"))) as fh:
            return fh.read()


class InstallTests(HelperBase):
    def test_install_sets_everything_up_once(self):
        code, result = self.job("install", "--library", LIBRARY)
        self.assertEqual((code, result["ok"]), (0, True))
        smb = self.file("/etc/samba/smb.conf")
        self.assertEqual(smb.count(helper.INCLUDE), 1)
        self.job("install", "--library", LIBRARY)
        self.assertEqual(self.file("/etc/samba/smb.conf").count(helper.INCLUDE), 1)  # not added twice
        self.assertNotIn("[DoubleTake]", self.file("/etc/samba/doubletake.conf"))  # no login yet, no share
        self.assertTrue(self.rec.ran("firewall-cmd", "--zone=home", "--add-service=samba"))
        self.assertTrue(self.rec.ran("firewall-cmd", "--zone=nm-shared", "--add-port=2222/tcp"))
        self.assertFalse(self.rec.ran("--zone=public", "--add"))  # never opened on untrusted networks
        self.assertTrue(self.rec.ran("setsebool", "samba_export_all_ro", "on"))
        self.assertIn("Options=bind,ro", self.file("/etc/systemd/system/srv-doubletake-Photos.mount"))
        self.assertIn(f"What={LIBRARY}", self.file("/etc/systemd/system/srv-doubletake-Photos.mount"))
        state = json.loads(self.file("/etc/doubletake/nas.json"))
        self.assertEqual((state["owner"], state["library"]), (OWNER, LIBRARY))

    def test_only_the_photos_are_shared(self):
        rhel = ("[global]\n\tworkgroup = SAMBA\n\n[homes]\n\tcomment = Home Directories\n\tvalid users = %S, %D%w%S\n"
                "\tbrowseable = No\n\n[printers]\n\tpath = /var/tmp\n\tprintable = Yes\n\n[print$]\n\tpath = /var/lib/samba/drivers\n")
        with open(os.path.join(self.root, "etc/samba/smb.conf"), "w") as fh:
            fh.write(rhel)
        self.job("install", "--library", LIBRARY)
        self.job("install", "--library", LIBRARY)
        smb = self.file("/etc/samba/smb.conf")
        for line in smb.splitlines():
            if line.startswith(("[homes]", "[printers]", "[print$]", "\tpath", "\tcomment")):
                self.fail("left switched on: " + line)
        self.assertIn("\tworkgroup = SAMBA", smb)
        self.assertEqual(smb.count(helper.OFF + helper.OFF), 0)  # not commented twice
        self.assertEqual(self.file("/etc/samba/smb.conf.before-doubletake"), rhel)
        self.assertEqual([l for l in smb.splitlines() if "doubletake.conf" in l], [helper.INCLUDE])  # still pulled in
        self.job("reset")
        self.assertNotIn(helper.OFF, self.file("/etc/samba/smb.conf"))
        self.assertIn("[homes]", self.file("/etc/samba/smb.conf"))

    def test_repairs_the_052_file(self):
        """0.5.2 switched off its own include line along with the printers."""
        broken = ("[global]\n\tworkgroup = SAMBA\n\n#doubletake-off# [print$]\n#doubletake-off# \tpath = /x\n\n"
                  "#doubletake-off# # Field Photo Station\n#doubletake-off# " + helper.INCLUDE + "\n")
        with open(os.path.join(self.root, "etc/samba/smb.conf"), "w") as fh:
            fh.write(broken)
        self.job("install", "--library", LIBRARY)
        smb = self.file("/etc/samba/smb.conf")
        self.assertIn("\n" + helper.INCLUDE + "\n", smb)
        self.assertEqual(smb.count("doubletake.conf"), 1)
        self.assertIn(helper.OFF + "[print$]", smb)

    def test_install_needs_the_tablets_user(self):
        self.env = {}
        self.assertIn("sudo", self.job("install", "--library", LIBRARY)[1]["error"])
        self.env = {"SUDO_USER": "root"}
        self.assertFalse(self.job("install", "--library", LIBRARY)[1]["ok"])

    def test_a_folder_name_cannot_add_lines_to_the_share(self):
        for bad in (LIBRARY + "\n[evil]\npath = /\nread only = no", LIBRARY + "\rx", LIBRARY + "%u",
                    LIBRARY + "\\x", LIBRARY + ";x", LIBRARY + '"x', HOME + "/-x", HOME + "//x"):
            code, result = self.job("install", "--library", bad)
            self.assertFalse(result["ok"], repr(bad))
            self.assertIn("letters, digits", result["error"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "etc/samba/doubletake.conf")))

    def test_only_a_folder_inside_the_users_home_can_be_shared(self):
        for bad in ("/etc", HOME, HOME + "ette/x", HOME + "/../root"):
            code, result = self.job("install", "--library", bad)
            self.assertEqual(code, 1, bad)
            self.assertFalse(result["ok"], bad)


class UserTests(HelperBase):
    def setUp(self):
        super().setUp()
        self.job("install", "--library", LIBRARY)

    def test_the_share_login(self):
        code, result = self.job("set-user", "photos", stdin="goodpassword\n")
        self.assertEqual(result, {"ok": True, "user": "photos"})
        self.assertTrue(self.rec.ran("useradd", "--system", "--shell", "/sbin/nologin", "photos"))
        stdins = [s for a, s in self.rec.calls if a[0] in ("chpasswd", "smbpasswd")]
        self.assertEqual(stdins, ["photos:goodpassword\n", "goodpassword\ngoodpassword\n"])
        conf = self.file("/etc/samba/doubletake.conf")
        self.assertIn("read only = yes", conf)
        self.assertIn("valid users = photos", conf)
        self.assertIn(f"force user = {OWNER}", conf)
        self.assertIn(f"path = {LIBRARY}", conf)
        self.assertIn("AllowUsers photos", self.file("/etc/ssh/doubletake_sftp_config"))
        self.assertIn("internal-sftp -R", self.file("/etc/ssh/doubletake_sftp_config"))

    def test_names_and_passwords_that_are_refused(self):
        for name in ("root", OWNER, "Bad Name", "x" * 40, "-x", "nobody"):
            self.assertFalse(self.job("set-user", name, stdin="goodpassword")[1]["ok"], name)
        for password in ("short", "has:colon1", "two\nlines1", "x" * 200):
            self.assertFalse(self.job("set-user", "photos", stdin=password)[1]["ok"], password)
        self.assertFalse(self.rec.ran("useradd"))

    def test_switching_needs_a_login_first(self):
        self.assertIn("username", self.job("nas", "on")[1]["error"])
        self.job("set-user", "photos", stdin="goodpassword")
        self.assertTrue(self.job("nas", "on")[1]["ok"])
        self.assertTrue(self.rec.ran("systemctl", "enable", "--now", "smb.service"))
        self.assertTrue(self.rec.ran("systemctl", "enable", "--now", "doubletake-nas-status.service"))
        self.job("nas", "off")
        self.assertTrue(self.rec.ran("systemctl", "disable", "--now", "smb.service"))
        self.assertTrue(self.rec.ran("systemctl", "disable", "--now", "doubletake-nas-status.service"))

    def test_status_writer_comes_back_after_a_restart(self):
        """It starts at boot while the NAS is on, and status-check starts it if it is not running."""
        self.assertIn("WantedBy=multi-user.target", helper.STATUS_UNIT)
        self.job("set-user", "photos", stdin="goodpassword")
        self.job("nas", "on")
        self.rec.calls.clear()
        code, result = self.job("status-check")
        self.assertEqual((result["ok"], result["running"]), (True, True))
        self.assertTrue(self.rec.ran("systemctl", "enable", "--now", "doubletake-nas-status.service"))
        self.job("nas", "off")
        self.assertFalse(self.job("status-check")[1]["running"])

    def test_reset_removes_the_share(self):
        self.job("set-user", "photos", stdin="goodpassword")
        self.job("nas", "on")
        self.assertTrue(self.job("reset")[1]["ok"])
        self.assertNotIn("[DoubleTake]", self.file("/etc/samba/doubletake.conf"))
        self.assertEqual(json.loads(self.file("/etc/doubletake/nas.json"))["user"], "")


class NetworkAndCardTests(HelperBase):
    def test_zone(self):
        uuid = "1b2c3d4e-0000-1111-2222-333344445555"
        self.assertTrue(self.job("zone", uuid, "home", "wlp1s0")[1]["ok"])
        self.assertTrue(self.rec.ran("nmcli", "connection", "modify", "uuid", uuid, "connection.zone", "home"))
        self.assertTrue(self.rec.ran("firewall-cmd", "--zone=home", "--change-interface=wlp1s0"))
        for bad in (("not-a-uuid", "home"), (uuid, "trusted"), (uuid, "home", "eth0; rm -rf /")):
            self.assertFalse(self.job("zone", *bad)[1]["ok"], bad)

    def test_only_wifi_and_cable_networks(self):
        self.rec.answers["connection.type"] = (0, "vpn\n", "")
        self.assertFalse(self.job("zone", "1b2c3d4e-0000-1111-2222-333344445555", "home")[1]["ok"])

    def make_disk(self, name, removable="1", kind="SD"):
        os.makedirs(os.path.join(self.root, f"sys/class/block/{name}/device"), exist_ok=True)
        with open(os.path.join(self.root, f"sys/class/block/{name}/removable"), "w") as fh:
            fh.write(removable + "\n")
        with open(os.path.join(self.root, f"sys/class/block/{name}/device/type"), "w") as fh:
            fh.write(kind + "\n")  # an SD card in the slot; MMC is built-in storage

    def set_mounts(self, text):
        os.makedirs(os.path.join(self.root, "proc/self"), exist_ok=True)
        with open(os.path.join(self.root, "proc/self/mounts"), "w") as fh:
            fh.write(text)

    def test_formatting_the_microsd_card(self):
        self.make_disk("mmcblk0", "0")
        self.set_mounts("/dev/mapper/rhel-root / xfs rw 0 0\n/dev/mmcblk0p1 /run/media/u/SD_CARD exfat rw 0 0\n")
        self.assertTrue(self.job("format-card", "/dev/mmcblk0p1", "FIELDCARD")[1]["ok"])
        self.assertTrue(self.rec.ran("umount", "/dev/mmcblk0p1"))
        self.assertTrue(self.rec.ran("mkfs.exfat", "-L", "FIELDCARD", "/dev/mmcblk0p1"))

    def test_never_formats_a_cameras_card(self):
        self.make_disk("mmcblk0", "0")
        self.set_mounts("/dev/mmcblk0p1 /run/media/u/NIKON\\040D850 exfat rw 0 0\n")
        os.makedirs(os.path.join(self.root, "run/media/u/NIKON D850/DCIM/100NCD850"))
        result = self.job("format-card", "/dev/mmcblk0p1", "FIELDCARD")[1]
        self.assertFalse(result["ok"])
        self.assertIn("camera", result["error"])
        self.assertFalse(self.rec.ran("mkfs.exfat"))

    def test_formatting_refuses_anything_else(self):
        self.make_disk("mmcblk0", "0")
        self.make_disk("sda", "0")
        self.make_disk("nvme0n1", "0")
        self.set_mounts("/dev/mmcblk0p2 / xfs rw 0 0\n")
        for device in ("/dev/mmcblk0p2", "/dev/sda1", "/dev/nvme0n1p1", "/dev/mmcblk0", "/etc/passwd",
                       "/dev/mmcblk1p1"):
            self.assertFalse(self.job("format-card", device, "X")[1]["ok"], device)
        self.assertFalse(self.job("format-card", "/dev/mmcblk0p1", "bad;label")[1]["ok"])
        self.assertFalse(self.rec.ran("mkfs.exfat"))


class AutologinTests(HelperBase):
    RHEL = ("# GDM configuration storage\n\n[daemon]\n# Uncomment the line below to force the login screen to use Xorg\n"
            "#WaylandEnable=false\n\n[security]\n\n[xdmcp]\n\n[chooser]\n\n[debug]\n#Enable=true\n")

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.root, "etc/gdm"))
        with open(os.path.join(self.root, "etc/gdm/custom.conf"), "w") as fh:
            fh.write(self.RHEL)

    def test_on_then_off(self):
        self.job("install", "--library", LIBRARY)
        code, result = self.job("autologin", "on")
        self.assertTrue(result["ok"], result)
        conf = self.file("/etc/gdm/custom.conf")
        self.assertIn("[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=" + OWNER + "\n", conf)
        self.job("autologin", "on")
        self.assertEqual(self.file("/etc/gdm/custom.conf").count("AutomaticLogin="), 1)  # not twice
        self.job("autologin", "off")
        self.assertEqual(self.file("/etc/gdm/custom.conf"), self.RHEL)

    def test_reset_turns_it_off(self):
        self.job("install", "--library", LIBRARY)
        self.job("autologin", "on")
        self.job("reset")
        self.assertNotIn("AutomaticLogin", self.file("/etc/gdm/custom.conf"))

    def test_only_on_or_off(self):
        code, result = self.job("autologin", "root")
        self.assertFalse(result["ok"])


class PasswordTests(HelperBase):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.root, "etc/doubletake"), exist_ok=True)

    def pw(self, action, text="", now=1000.0):
        return helper.fs_password(self.sys, action, text, self.env, now=now)

    def test_unset_lets_everything_through(self):
        self.assertEqual(self.pw("status")["mode"], "unset")
        self.assertTrue(self.pw("check", "anything")["ok"])

    def test_set_check_and_change(self):
        self.assertTrue(self.pw("set", "ramp at dawn 27")["ok"])
        self.assertEqual(self.pw("status")["mode"], "password")
        saved = self.file("/etc/doubletake/password.json")
        self.assertNotIn("ramp at dawn", saved)  # only a hash is kept
        self.assertTrue(self.pw("check", "ramp at dawn 27")["ok"])
        self.assertFalse(self.pw("check", "ramp at dusk 27")["ok"])
        # Changing it needs the current one.
        self.assertFalse(self.pw("set", "wrong one here\nnew pass phrase")["ok"])
        self.assertTrue(self.pw("set", "ramp at dawn 27\nnew pass phrase")["ok"])
        self.assertTrue(self.pw("check", "new pass phrase")["ok"])

    def test_weak_passwords_are_refused(self):
        for weak in ("short", "password123", "1234567890123", "aaaaabbbbb"):
            with self.assertRaises(helper.Refused):
                self.pw("set", weak)

    def test_wrong_tries_slow_down(self):
        self.pw("set", "ramp at dawn 27")
        for i in range(5):
            self.assertEqual(self.pw("check", "nope", now=1000 + i).get("wait"), 0 if i < 4 else 30)
        # Locked for 30 s, even with the right password.
        self.assertFalse(self.pw("check", "ramp at dawn 27", now=1010)["ok"])
        self.assertTrue(self.pw("check", "ramp at dawn 27", now=1040)["ok"])
        self.assertEqual(self.pw("status", now=1040)["wait"], 0)

    def test_no_password_needs_the_current_one_and_says_so(self):
        self.pw("set", "ramp at dawn 27")
        self.assertFalse(self.pw("none", "guess")["ok"])
        self.assertEqual(self.pw("none", "ramp at dawn 27")["mode"], "none")
        self.assertTrue(self.pw("check", "")["ok"])

    def test_clear_only_from_a_root_shell(self):
        self.pw("set", "ramp at dawn 27")
        self.env = {"SUDO_USER": OWNER, "SUDO_COMMAND": "/usr/local/libexec/doubletake-helper fs-password clear"}
        with self.assertRaises(helper.Refused):
            self.pw("clear")
        self.env = {"SUDO_USER": OWNER, "SUDO_COMMAND": "/bin/bash"}
        self.assertEqual(self.pw("clear")["mode"], "unset")


class SplashTests(HelperBase):
    def setUp(self):
        super().setUp()
        theme = os.path.join(self.root, "usr/share/plymouth/themes/doubletake")
        os.makedirs(theme)
        open(os.path.join(theme, "doubletake.plymouth"), "w").close()
        self.initrd = {}
        self.rec.answers = {"plymouth-set-default-theme -R": (1, "", "use dracut"),
                            "plymouth-set-default-theme": (0, "bgrt\n", ""),
                            "grubby": (0, "/boot/vmlinuz-5.14.0-611.el9.x86_64\n", ""),
                            "uname": (0, "5.14.0-570.el9.x86_64\n", "")}
        real = self.rec.__call__

        def run(args, stdin=None, timeout=120):
            if args[0] == "lsinitrd":
                self.rec.calls.append((list(args), stdin))
                return 0, f"[Daemon]\nTheme={self.initrd.get(args[-1], 'bgrt')}\n", ""
            if args[0] == "dracut":
                self.rec.calls.append((list(args), stdin))
                self.initrd[args[2]] = "doubletake" if "on" == self.mode else "bgrt"
                return 0, "", ""
            return real(args, stdin, timeout)

        self.mode = "on"
        self.sys.run = run
        self.job("install", "--library", LIBRARY)

    def test_refresh_rebuilds_only_the_one_in_use(self):
        self.job("splash", "on")
        self.rec.answers["plymouth-set-default-theme"] = (0, "doubletake\n", "")
        calls = len(self.rec.calls)
        code, result = self.job("splash", "refresh")
        self.assertTrue(result["ok"], result)
        self.assertTrue(any(a[0] == "dracut" for a, _ in self.rec.calls[calls:]))  # rebuilt though it matched
        self.rec.answers["plymouth-set-default-theme"] = (0, "bgrt\n", "")
        calls = len(self.rec.calls)
        code, result = self.job("splash", "refresh")
        self.assertTrue(result.get("skipped"))
        self.assertFalse(any(a[0] == "dracut" for a, _ in self.rec.calls[calls:]))

    def test_on_then_back_to_the_one_before(self):
        code, result = self.job("splash", "on")
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.rec.ran("plymouth-set-default-theme", "doubletake"))
        # Both the default kernel's start-up image and the running one's are rebuilt.
        self.assertTrue(self.rec.ran("dracut", "-f", "/boot/initramfs-5.14.0-611.el9.x86_64.img"))
        self.assertTrue(self.rec.ran("dracut", "-f", "/boot/initramfs-5.14.0-570.el9.x86_64.img"))
        self.assertEqual(self.sys.state()["splash_before"], "bgrt")
        calls = len(self.rec.calls)
        self.job("splash", "on")  # already in place: nothing is rebuilt
        self.assertFalse(any(a[0] == "dracut" for a, _ in self.rec.calls[calls:]))
        self.rec.answers["plymouth-set-default-theme"] = (0, "doubletake\n", "")
        self.mode = "off"
        self.assertEqual(self.job("splash", "off")[1]["theme"], "bgrt")
        self.assertTrue(self.rec.ran("plymouth-set-default-theme", "bgrt"))
        self.assertFalse(self.sys.state()["splash"])

    def test_the_boot_menu_is_hidden_and_put_back(self):
        grub = ('GRUB_TIMEOUT=5\nGRUB_DISTRIBUTOR="$(sed \'s, release .*$,,g\' /etc/system-release)"\n'
                'GRUB_DEFAULT=saved\nGRUB_TERMINAL_OUTPUT="console"\nGRUB_CMDLINE_LINUX="rhgb quiet"\n'
                'GRUB_ENABLE_BLSCFG=true\n')
        os.makedirs(os.path.join(self.root, "etc/default"))
        os.makedirs(os.path.join(self.root, "boot/grub2"))
        with open(os.path.join(self.root, "etc/default/grub"), "w") as fh:
            fh.write(grub)
        open(os.path.join(self.root, "boot/grub2/grub.cfg"), "w").close()
        self.job("splash", "on")
        text = self.file("/etc/default/grub")
        self.assertIn("GRUB_TIMEOUT=3\n", text)
        self.assertIn("GRUB_TIMEOUT_STYLE=hidden\n", text)
        self.assertIn('GRUB_CMDLINE_LINUX="rhgb quiet"', text)
        self.assertTrue(self.rec.ran("grub2-mkconfig", "-o", "/boot/grub2/grub.cfg"))
        self.assertTrue(self.rec.ran("grubby", "--update-kernel=ALL", "--args=loglevel=3"))
        self.job("splash", "on")  # again: what to go back to is kept
        self.rec.answers["plymouth-set-default-theme"] = (0, "doubletake\n", "")
        self.mode = "off"
        self.job("splash", "off")
        self.assertEqual(self.file("/etc/default/grub"), grub)
        self.assertTrue(self.rec.ran("grubby", "--update-kernel=ALL", "--remove-args=loglevel=3"))

    def test_needs_the_theme_installed(self):
        shutil.rmtree(os.path.join(self.root, "usr/share/plymouth/themes/doubletake"))
        self.assertIn("setup.sh", self.job("splash", "on")[1]["error"])

    def test_only_on_or_off(self):
        self.assertFalse(self.job("splash", "spinner")[1]["ok"])


class SessionTests(HelperBase):
    def setUp(self):
        super().setUp()
        # As on RHEL 9.7: the same file name in both folders.
        for path in ("usr/share/wayland-sessions/gnome-kiosk-script.desktop",
                     "usr/share/xsessions/gnome-kiosk-script.desktop",
                     "usr/share/wayland-sessions/gnome.desktop"):
            os.makedirs(os.path.dirname(os.path.join(self.root, path)), exist_ok=True)
            open(os.path.join(self.root, path), "w").close()
        os.makedirs(os.path.join(self.root, "var/lib/AccountsService/users"))
        with open(os.path.join(self.root, "var/lib/AccountsService/users", OWNER), "w") as fh:
            fh.write("[User]\nLanguage=en_US.UTF-8\nSession=gnome-xorg\nSystemAccount=false\n\n[InputSource0]\nxkb=us\n")
        self.job("install", "--library", LIBRARY)

    def saved(self):
        return self.file("/var/lib/AccountsService/users/" + OWNER)

    def test_kiosk_then_back_to_the_desktop_it_was(self):
        code, result = self.job("session", "kiosk")
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.rec.ran("busctl", "SetSession", "gnome-kiosk-script"))
        text = self.saved()
        self.assertIn("Session=gnome-kiosk-script\n", text)
        self.assertIn("SessionType=wayland", text)
        self.assertIn("Language=en_US.UTF-8", text)
        self.assertIn("[InputSource0]\nxkb=us", text)  # the rest is kept
        self.job("session", "kiosk")  # twice changes nothing of what to go back to
        code, result = self.job("session", "desktop")
        self.assertEqual(result["session"], "gnome-xorg")
        self.assertIn("Session=gnome-xorg\n", self.saved())
        self.assertIn("SessionType=x11", self.saved())
        self.assertEqual(self.saved().count("Session="), 2)  # Session and XSession, once each

    def test_without_the_kiosk_package(self):
        os.remove(os.path.join(self.root, "usr/share/wayland-sessions/gnome-kiosk-script.desktop"))
        os.remove(os.path.join(self.root, "usr/share/xsessions/gnome-kiosk-script.desktop"))
        code, result = self.job("session", "kiosk")
        self.assertFalse(result["ok"])
        self.assertIn("gnome-kiosk-script-session", result["error"])

    def test_an_older_accounts_service_is_restarted_to_read_the_file(self):
        self.rec.answers = {"SetSession": (1, "", "Unknown method"), "SetXSession": (1, "", "Unknown method")}
        self.assertTrue(self.job("session", "kiosk")[1]["ok"])
        self.assertTrue(self.rec.ran("systemctl", "try-restart", "accounts-daemon"))
        self.assertIn("Session=gnome-kiosk-script", self.saved())

    def test_reset_goes_back_to_the_desktop(self):
        self.job("session", "kiosk")
        self.job("reset")
        self.assertIn("Session=gnome-xorg\n", self.saved())

    def test_only_kiosk_or_desktop(self):
        self.assertFalse(self.job("session", "gnome-kiosk-script")[1]["ok"])

    def test_other_versions_name_it_differently(self):
        for folder in ("wayland-sessions", "xsessions"):
            os.remove(os.path.join(self.root, "usr/share", folder, "gnome-kiosk-script.desktop"))
        open(os.path.join(self.root, "usr/share/xsessions/gnome-kiosk-script-xorg.desktop"), "w").close()
        self.assertEqual(self.job("session", "kiosk")[1]["session"], "gnome-kiosk-script-xorg")
        self.assertIn("SessionType=x11", self.saved())
        self.assertFalse(self.job("session")[1]["ok"])


class PhonePortTests(HelperBase):
    def test_opens_only_while_sharing_and_only_on_the_hotspot(self):
        self.rec.answers = {"--get-zone-of-interface=wlp1s0": (0, "nm-shared\n", "")}
        code, result = self.job("phone-port", "on", "wlp1s0")
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.rec.ran("firewall-cmd", "--zone=nm-shared", "--add-port=8780/tcp"))
        self.assertFalse(self.rec.ran("--permanent", "8780"))  # never saved for good
        self.job("phone-port", "off", "wlp1s0")
        self.assertTrue(self.rec.ran("firewall-cmd", "--zone=nm-shared", "--remove-port=8780/tcp"))

    def test_refuses_bad_devices(self):
        self.assertFalse(self.job("phone-port", "on", "wl;rm -rf /")[1]["ok"])
        self.assertFalse(self.job("phone-port", "maybe", "wlp1s0")[1]["ok"])


class StatusTests(unittest.TestCase):
    def test_smbstatus_json(self):
        text = json.dumps({
            "sessions": {"3": {"username": "photos", "remote_machine": "192.168.1.23",
                               "hostname": "ipv4:192.168.1.23:50211"}},
            "open_files": {"/home/u/Pictures/DoubleTake/2026/x/N81_0001.NEF": {"filename": "2026/x/N81_0001.NEF"},
                           "/home/u/Pictures/DoubleTake/.": {"filename": "."}},
        })
        sessions, files = helper.parse_smbstatus_json(text)
        self.assertEqual(sessions, [{"user": "photos", "machine": "192.168.1.23", "address": "192.168.1.23",
                                     "kind": "smb"}])
        self.assertEqual(files, ["N81_0001.NEF"])

    def test_smbstatus_text(self):
        sessions_text = ("PID     Username     Group        Machine                                   Protocol Version\n"
                         "--------------------------------------------------------\n"
                         "4242    photos       photos       192.168.1.23 (ipv4:192.168.1.23:50211)   SMB3_11\n")
        locks_text = ("Pid   User(ID)   DenyMode   Access   R/W   Oplock   SharePath   Name   Time\n"
                      "---------------------------------\n"
                      "4242  1000  DENY_NONE  0x120089  RDONLY  LEASE(RWH)  /home/u/Pictures/DoubleTake  "
                      "2026/2026-10-11_KCLT ramp/N81_0002.NEF   Mon Oct 12 06:00:00 2026\n")
        sessions, files = helper.parse_smbstatus_text(sessions_text, locks_text)
        self.assertEqual(sessions[0]["address"], "192.168.1.23")
        self.assertEqual(files, ["N81_0002.NEF"])

    def test_sftp_peers(self):
        peers = helper.sftp_peers("0      0      192.168.1.10:2222      192.168.1.23:51234\n")
        self.assertEqual(peers[0]["address"], "192.168.1.23")


class OldNameTests(SplashTests):
    """Moving over from the name Field Station, used before 0.8.1."""

    def old_install(self):
        def put(path, text="x"):
            full = os.path.join(self.root, path.lstrip("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write(text)
        put("/etc/fieldstation/nas.json", json.dumps({"owner": OWNER, "library": LIBRARY, "user": "photos",
                                                      "nas": True, "sftp": True, "splash": True,
                                                      "splash_before": "bgrt"}))
        put("/etc/fieldstation/password.json", '{"hash": "abc"}')
        for unit in helper.OLD_UNITS:
            put(f"/etc/systemd/system/{unit}")
        put("/etc/samba/fieldstation.conf")
        put("/etc/ssh/fieldstation_sftp_config")
        put("/usr/share/plymouth/themes/fieldstation/fieldstation.plymouth")
        put("/etc/samba/smb.conf", "[global]\n   workgroup = SAMBA\n\n#fieldstation-off# [homes]\n"
                                   "#fieldstation-off#    browseable = No\n\n# Field Station\n"
                                   "include = /etc/samba/fieldstation.conf\n")
        os.remove(os.path.join(self.root, "etc/doubletake/nas.json"))  # from setUp's install

    def test_everything_moves_over(self):
        self.old_install()
        self.rec.answers["plymouth-set-default-theme"] = (0, "fieldstation\n", "")
        code, result = self.job("migrate-old-names")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["splash"])
        state = self.sys.state()
        self.assertEqual((state["user"], state["sftp"], state["splash_before"]), ("photos", True, "bgrt"))
        self.assertIn("abc", self.file("/etc/doubletake/password.json"))
        self.assertFalse(os.path.exists(os.path.join(self.root, "etc/fieldstation")))
        # The screens read nas.json, so its folder must stay open to them.
        self.assertEqual(os.stat(os.path.join(self.root, "etc/doubletake")).st_mode & 0o777, 0o755)
        for unit in helper.OLD_UNITS:
            self.assertTrue(self.rec.ran("systemctl", "disable", "--now", unit))
            self.assertFalse(os.path.exists(os.path.join(self.root, "etc/systemd/system", unit)))
        self.assertTrue(self.rec.ran("plymouth-set-default-theme", "doubletake"))
        self.assertFalse(os.path.exists(os.path.join(self.root, "usr/share/plymouth/themes/fieldstation")))
        # Then install writes the NAS under the new names, and brings SFTP back as it was.
        self.job("install", "--library", LIBRARY)
        smb = self.file("/etc/samba/smb.conf")
        self.assertNotIn("fieldstation", smb)
        self.assertIn("#doubletake-off# [homes]", smb)
        self.assertEqual(smb.count(helper.INCLUDE), 1)
        self.assertTrue(self.rec.ran("systemctl", "enable", "--now", "doubletake-sftp.service"))

    def test_a_closed_folder_is_opened_again(self):
        os.chmod(os.path.join(self.root, "etc/doubletake"), 0o700)  # as 0.8.1 left it
        self.job("install", "--library", LIBRARY)
        self.assertEqual(os.stat(os.path.join(self.root, "etc/doubletake")).st_mode & 0o777, 0o755)

    def test_nothing_old_nothing_done(self):
        code, result = self.job("migrate-old-names")
        self.assertEqual((result["ok"], result["done"], result["splash"]), (True, [], False))


class AdoptTests(HelperBase):
    """The first setup after installing the package, through sudo in a terminal."""

    def test_the_tablets_user_gets_the_rule_the_nas_and_the_boot_entry(self):
        import pwd as _pwd
        self.env = {"SUDO_USER": _pwd.getpwnam(OWNER).pw_name}
        code, result = self.job("adopt", "--library", LIBRARY)
        self.assertTrue(result["ok"], result)
        rule = self.file("/etc/sudoers.d/doubletake")
        self.assertIn(f"{OWNER} ALL=(root) NOPASSWD: " + helper.HELPER_PATH, rule)
        self.assertEqual(self.sys.state()["owner"], OWNER)

    def test_without_sudo_it_is_refused(self):
        self.env = {}
        code, result = self.job("adopt", "--library", LIBRARY)
        self.assertFalse(result["ok"])

    def test_any_login_the_installer_accepts_fits_the_rule(self):
        for name in ("7xkq2p9", "nick", "Nick.M", "_svc", "a-b_c"):
            self.assertTrue(helper.OWNER_RE.match(name), name)
        for name in ("1000", "", "ni ck", "nick\nALL", "x,y", "-n", "a" * 40):
            self.assertFalse(helper.OWNER_RE.match(name), name)

    def test_the_units_name_the_helper_in_use(self):
        self.assertIn(helper.HELPER_PATH + " status-loop", helper.STATUS_UNIT.replace("@HELPER@", helper.HELPER_PATH))
        self.assertNotIn("@HELPER@", helper.BOOT_UNIT.replace("@HELPER@", helper.HELPER_PATH))


class SplashDefaultTests(HelperBase):
    def test_a_choice_made_before_is_kept(self):
        self.job("install", "--library", LIBRARY)
        state = self.sys.state(); state["splash"] = False; self.sys.save_state(state)
        code, result = self.job("splash", "default")
        self.assertTrue(result.get("skipped"), result)
        self.assertFalse(self.rec.ran("plymouth-set-default-theme", "doubletake"))

    def test_on_when_never_chosen(self):
        self.job("install", "--library", LIBRARY)
        seen = []
        real = helper.splash
        helper.splash = lambda sysm, on, force=False: seen.append(on) or {"ok": True}
        try:
            theme = os.path.join(self.root, helper.SPLASH_DIR.lstrip("/"))
            os.makedirs(theme, exist_ok=True)
            open(os.path.join(theme, helper.SPLASH_NAME + ".plymouth"), "w").close()
            code, result = self.job("splash", "default")
        finally:
            helper.splash = real
        self.assertEqual(seen, [True])


class BacklightTests(HelperBase):
    def panel(self, now, top, saved=None, name="intel_backlight"):
        base = os.path.join(self.root, "sys/class/backlight", name)
        os.makedirs(base, exist_ok=True)
        for f, v in (("brightness", now), ("max_brightness", top)):
            with open(os.path.join(base, f), "w") as fh:
                fh.write(f"{v}\n")
        if saved is not None:
            d = os.path.join(self.root, "var/lib/systemd/backlight")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"pci-0000:00:02.0:backlight:{name}"), "w") as fh:
                fh.write(f"{saved}\n")
        return os.path.join(base, "brightness")

    def test_fades_down_to_the_saved_level_in_even_steps(self):
        path = self.panel(96000, 96000, saved=24000)
        written = []
        real_open = open
        def spy(file, mode="r", *a, **kw):
            fh = real_open(file, mode, *a, **kw)
            if file == path and "w" in mode:
                write = fh.write
                fh.write = lambda text: (written.append(int(text)), write(text))[1]
            return fh
        with mock.patch("builtins.open", spy):
            result = helper.backlight_fade(self.sys, sleep=lambda s: None)
        self.assertEqual(result["faded"], [{"panel": "intel_backlight", "from": 96000, "to": 24000}])
        self.assertEqual(written[-1], 24000)
        self.assertEqual(written, sorted(written, reverse=True))  # always down, never back up
        self.assertGreater(len(written), 10)
        self.assertEqual(int(self.file("/sys/class/backlight/intel_backlight/brightness")), 24000)

    def test_nothing_saved_or_nothing_to_change(self):
        self.panel(96000, 96000)
        self.assertEqual(helper.backlight_fade(self.sys, sleep=lambda s: None)["faded"], [])
        self.panel(500, 1000, saved=500, name="acpi_video0")
        self.assertEqual(helper.backlight_fade(self.sys, sleep=lambda s: None)["faded"], [])

    def test_never_darker_than_the_system_allows(self):
        self.panel(1000, 1000, saved=0)
        result = helper.backlight_fade(self.sys, sleep=lambda s: None)
        self.assertEqual(result["faded"][0]["to"], 50)  # 5%, as the system clamps it

    def test_a_strange_panel_name_is_left_alone(self):
        self.panel(1000, 1000, saved=300, name="bad name")
        self.assertEqual(helper.backlight_fade(self.sys, sleep=lambda s: None)["faded"], [])


class FlickerTests(HelperBase):
    def intel(self):
        os.makedirs(os.path.join(self.root, "sys/module/i915"))

    def test_status_reads_the_boot_settings_and_the_running_kernel(self):
        self.intel()
        self.sys.write("/proc/cmdline", "BOOT_IMAGE=/vmlinuz root=x ro quiet\n")
        self.rec.answers["--info=DEFAULT"] = (0, 'index=0\nargs="ro quiet i915.enable_psr=0 i915.enable_dc=0"\n', "")
        code, result = self.job("flicker", "status")
        self.assertEqual((result["intel"], result["set"], result["active"]), (True, True, False))

    def test_on_and_off_touch_only_the_one_setting(self):
        self.intel()
        code, result = self.job("flicker", "on")
        self.assertTrue(result["ok"])
        self.assertTrue(self.rec.ran("grubby", "--update-kernel=ALL", "--args=i915.enable_psr=0 i915.enable_dc=0"))
        self.job("flicker", "off")
        self.assertTrue(self.rec.ran("grubby", "--update-kernel=ALL", "--remove-args=i915.enable_psr=0 i915.enable_dc=0"))

    def test_the_first_fix_alone_counts_as_off(self):
        # From the pre-releases that set only the first: shown off, and switching on adds both.
        self.intel()
        self.rec.answers["--info=DEFAULT"] = (0, 'args="ro quiet i915.enable_psr=0"\n', "")
        self.assertFalse(self.job("flicker", "status")[1]["set"])

    def test_not_for_a_tablet_without_intel_graphics(self):
        code, result = self.job("flicker", "on")
        self.assertFalse(result["ok"])
        self.assertIn("Intel", result["error"])
        self.assertFalse(self.rec.ran("grubby"))

    def test_nothing_else_is_accepted(self):
        self.intel()
        code, result = self.job("flicker", "i915.enable_psr=1")
        self.assertFalse(result["ok"])
        self.assertFalse(self.rec.ran("grubby"))


def tiny_bmp(width=4, height=2, bpp=24):
    import struct as st
    step = bpp // 8
    stride = (width * step + 3) & ~3
    pixels = b""
    for y in range(height):  # bottom-up: the last row first
        row = b"".join(bytes((10 * x, 20, 200)) + (b"\x00" if bpp == 32 else b"") for x in range(width))
        pixels += row + b"\x00" * (stride - len(row))
    header = st.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54)
    dib = st.pack("<IiiHHIIiiII", 40, width, height, 1, bpp, 0, len(pixels), 0, 0, 0, 0)
    return header + dib + pixels


class FirmwareLogoTests(HelperBase):
    def setUp(self):
        super().setUp()
        self.theme = "/usr/share/plymouth/themes/doubletake"
        self.sys.write(f"{self.theme}/doubletake.script",
                       'edition = "";  # doubletake-helper splash tune\n'
                       'bgrt_x = 0; bgrt_y = 0; bgrt_w = 0; bgrt_h = 0;  # doubletake-helper splash tune\n')

    def firmware(self, status="1", x="800", y="300"):
        self.sys.write(f"{helper.BGRT_DIR}/status", status + "\n")
        self.sys.write(f"{helper.BGRT_DIR}/xoffset", x + "\n")
        self.sys.write(f"{helper.BGRT_DIR}/yoffset", y + "\n")
        with open(self.sys.path(f"{helper.BGRT_DIR}/image"), "wb") as fh:
            fh.write(tiny_bmp())

    def test_the_bmp_becomes_a_png_the_right_way_up(self):
        png = helper.bmp_to_png(tiny_bmp())
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct_unpack(png), (4, 2))
        self.assertEqual(helper.bmp_to_png(tiny_bmp(bpp=32))[:8], b"\x89PNG\r\n\x1a\n")
        self.assertIsNone(helper.bmp_to_png(b"not a picture"))

    def test_the_firmware_logo_place_goes_into_the_script(self):
        self.firmware()
        self.job("splash", "tune")
        text = self.file(f"{self.theme}/doubletake.script")
        self.assertIn("bgrt_x = 800; bgrt_y = 300; bgrt_w = 4; bgrt_h = 2;", text)

    def test_the_black_round_the_logo_is_trimmed(self):
        # A 10 x 6 picture, black but for a 4 x 2 logo at (3, 2): the ring is sized to the logo.
        import struct as st
        width, height = 10, 6
        stride = (width * 3 + 3) & ~3
        rows = []
        for y in range(height):  # top-down here; written bottom-up below
            row = b"".join(bytes((255, 255, 255)) if 3 <= x < 7 and 2 <= y < 4 else b"\x00\x00\x00" for x in range(width))
            rows.append(row + b"\x00" * (stride - len(row)))
        pixels = b"".join(reversed(rows))
        bmp = (st.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54)
               + st.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 0, 0, 0, 0) + pixels)
        self.firmware(x="100", y="50")
        with open(self.sys.path(f"{helper.BGRT_DIR}/image"), "wb") as fh:
            fh.write(bmp)
        self.assertEqual(helper.firmware_logo(self.sys), (103, 52, 4, 2))
        self.assertTrue(os.path.isfile(self.sys.path(f"{self.theme}/firmware.png")))

    def test_without_it_the_screen_is_centred(self):
        self.firmware(status="0")
        self.job("splash", "tune")
        self.assertIn("bgrt_x = 0; bgrt_y = 0; bgrt_w = 0; bgrt_h = 0;", self.file(f"{self.theme}/doubletake.script"))


def struct_unpack(png):
    import struct as st
    return st.unpack_from(">II", png, 16)


if __name__ == "__main__":
    unittest.main()


class BootMenuTests(HelperBase):
    """The boot menu's DoubleTake maintenance (desktop) entry."""

    USUAL = ("title Red Hat Enterprise Linux (5.14.0-687.56.1.el9_8.x86_64) 9.8 (Plow)\n"
             "version 5.14.0-687.56.1.el9_8.x86_64\n"
             "linux /vmlinuz-5.14.0-687.56.1.el9_8.x86_64\n"
             "initrd /initramfs-5.14.0-687.56.1.el9_8.x86_64.img $tuned_initrd\n"
             "options root=/dev/mapper/rhel-root ro rhgb quiet loglevel=3 $tuned_params\n"
             "grub_users $grub_users\ngrub_arg --unrestricted\ngrub_class rhel\n")

    def setUp(self):
        super().setUp()
        self.entries = os.path.join(self.root, "boot/loader/entries")
        os.makedirs(self.entries)
        with open(os.path.join(self.entries, "abc-5.14.0-687.56.1.el9_8.x86_64.conf"), "w") as fh:
            fh.write(self.USUAL)
        with open(os.path.join(self.entries, "abc-0-rescue.conf"), "w") as fh:
            fh.write(self.USUAL.replace("Plow)", "rescue)"))
        os.makedirs(os.path.join(self.root, "boot/grub2"))
        with open(os.path.join(self.root, "boot/grub2/grub.cfg"), "w") as fh:
            fh.write("# the menu as it was\n")
        os.makedirs(os.path.join(self.root, "etc/gdm"))
        os.makedirs(os.path.join(self.root, "usr/share/wayland-sessions"))
        for name in ("gnome.desktop", "gnome-kiosk-script.desktop"):
            open(os.path.join(self.root, "usr/share/wayland-sessions", name), "w").close()
        self.default = "/boot/vmlinuz-5.14.0-687.56.1.el9_8.x86_64"
        real = self.rec.__call__

        def run(args, stdin=None, timeout=120):
            if args[:2] == ["grubby", "--default-kernel"]:
                self.rec.calls.append((list(args), stdin))
                return 0, self.default + "\n", ""
            if args[0] == "grub2-mkconfig":
                self.rec.calls.append((list(args), stdin))
                with open(os.path.join(self.root, args[2].lstrip("/")), "w") as fh:
                    fh.write("# the new menu\n")
                return 0, "", ""
            return real(args, stdin, timeout)

        self.sys.run = run
        self.job("install", "--library", LIBRARY)

    def entry(self):
        return self.file("/boot/loader/entries/" + helper.ENTRY_FILE)

    def test_a_copy_of_the_usual_entry_with_one_word_more(self):
        code, result = self.job("boot-entry", "on")
        self.assertTrue(result["ok"], result)
        text = self.entry()
        self.assertIn("title DoubleTake maintenance (desktop)\n", text)
        self.assertIn("linux /vmlinuz-5.14.0-687.56.1.el9_8.x86_64\n", text)
        self.assertIn("options root=/dev/mapper/rhel-root ro rhgb quiet loglevel=3 $tuned_params doubletake.maintenance=1\n", text)
        self.assertIn("grub_arg --unrestricted\n", text)
        self.assertEqual(self.file("/boot/grub2/grub.cfg"), "# the menu as it was\n")  # the menu itself untouched
        self.assertTrue(self.rec.ran("systemctl", "enable", helper.BOOT_UNIT_NAME))

    def test_the_usual_entry_stays_the_default(self):
        self.rec.answers["grub2-editenv list"] = (0, "saved_entry=abc-5.14.0-687.56.1.el9_8.x86_64\nboot_success=1\n", "")
        self.job("boot-entry", "on")
        self.assertFalse(self.rec.ran("grub2-editenv", "set"))  # already right: left alone
        self.rec.calls.clear()
        self.rec.answers["grub2-editenv list"] = (1, "", "invalid environment block")
        self.job("boot-entry", "on")
        self.assertTrue(self.rec.ran("grub2-editenv", "create"))
        self.assertTrue(self.rec.ran("grub2-editenv", "set", "saved_entry=abc-5.14.0-687.56.1.el9_8.x86_64"))

    def test_the_entry_follows_a_new_kernel(self):
        self.job("boot-entry", "on")
        with open(os.path.join(self.entries, "abc-5.14.0-700.el9_8.x86_64.conf"), "w") as fh:
            fh.write(self.USUAL.replace("687.56.1", "700"))
        self.default = "/boot/vmlinuz-5.14.0-700.el9_8.x86_64"
        self.job("boot-entry", "refresh")
        self.assertIn("linux /vmlinuz-5.14.0-700.el9_8.x86_64\n", self.entry())

    def test_refresh_does_nothing_when_off(self):
        code, result = self.job("boot-entry", "refresh")
        self.assertTrue(result["skipped"])
        self.assertFalse(os.path.exists(os.path.join(self.entries, helper.ENTRY_FILE)))

    def test_the_0_8_3_entry_is_removed(self):
        self.sys.write(helper.GRUB_SCRIPT, "#!/bin/sh\n", mode=0o755)
        self.job("boot-entry", "on")
        self.assertFalse(os.path.exists(os.path.join(self.root, helper.GRUB_SCRIPT.lstrip("/"))))
        self.assertEqual(self.file("/boot/grub2/grub.cfg"), "# the new menu\n")

    def test_unknown_kernel_changes_nothing(self):
        self.default = "/boot/vmlinuz-9.9.9"
        code, result = self.job("boot-entry", "on")
        self.assertFalse(result["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.entries, helper.ENTRY_FILE)))

    def test_nothing_chosen_nothing_changes(self):
        self.sys.write(helper.GDM_CONF, helper.gdm_autologin("", OWNER))
        result = helper.boot_check(self.sys, "BOOT_IMAGE=/vmlinuz root=x ro quiet")
        self.assertEqual(result, {"ok": True, "maintenance": False})
        self.assertIn(f"AutomaticLogin={OWNER}", self.file(helper.GDM_CONF))

    def test_a_visit_chosen_in_the_menu(self):
        self.sys.write(helper.GDM_CONF, helper.gdm_autologin("", OWNER))
        state = self.sys.state()
        state["session"] = "kiosk"
        self.sys.save_state(state)
        result = helper.boot_check(self.sys, "BOOT_IMAGE=/vmlinuz root=x ro quiet doubletake.maintenance=1")
        self.assertTrue(result["maintenance"], result)
        self.assertNotIn("AutomaticLogin=", self.file(helper.GDM_CONF))  # the login screen asks for the password
        state = self.sys.state()
        self.assertEqual((state["session"], state["boot_maintenance"]), ("desktop", True))
        code, result = self.job("boot-maintenance", "done")
        self.assertTrue(result["done"], result)
        self.assertIn(f"AutomaticLogin={OWNER}", self.file(helper.GDM_CONF))
        state = self.sys.state()
        self.assertEqual(state["session"], "kiosk")
        self.assertNotIn("boot_maintenance", state)

    def test_reset_takes_the_entry_out(self):
        self.job("boot-entry", "on")
        self.job("reset")
        self.assertFalse(os.path.exists(os.path.join(self.entries, helper.ENTRY_FILE)))
        self.assertTrue(os.path.exists(os.path.join(self.entries, "abc-5.14.0-687.56.1.el9_8.x86_64.conf")))


class RunAgainTests(HelperBase):
    def test_install_again_with_the_sftp_view_mounted(self):
        self.job("install", "--library", LIBRARY)
        real = os.chmod

        def chmod(path, mode, *a, **k):
            if str(path).endswith("srv/doubletake/Photos"):
                raise OSError(30, "Read-only file system", str(path))
            return real(path, mode, *a, **k)

        from unittest import mock
        with mock.patch.object(helper.os, "chmod", chmod):
            code, result = self.job("install", "--library", LIBRARY)
        self.assertTrue(result["ok"], result)


class PrepareDriveTests(HelperBase):
    def setUp(self):
        super().setUp()
        self.job("install", "--library", LIBRARY)

    def drive(self, name="sdb", usb=True, gb=500, mounts="", kind="SD"):
        block = os.path.join(self.root, "sys/class/block", name)
        os.makedirs(os.path.join(block, "device"), exist_ok=True)
        with open(os.path.join(block, "device", "type"), "w") as fh:
            fh.write(kind + "\n")
        with open(os.path.join(block, ".realpath"), "w") as fh:
            fh.write(f"/sys/devices/pci0000:00/0000:00:14.0/{'usb2/2-1/' if usb else 'ata1/'}block/{name}\n")
        with open(os.path.join(block, "size"), "w") as fh:
            fh.write(f"{int(gb * 1e9 / 512)}\n")
        os.makedirs(os.path.join(self.root, "proc/self"), exist_ok=True)
        with open(os.path.join(self.root, "proc/self/mounts"), "w") as fh:
            fh.write(mounts)

    def test_a_usb_drive_is_erased_and_given_one_exfat_partition(self):
        self.drive(mounts="/dev/sdb1 /run/media/u/EFI vfat rw 0 0\n/dev/sdb2 /run/media/u/My\\040Drive exfat rw 0 0\n")
        code, result = self.job("prepare-drive", "/dev/sdb", "DT-COPY2")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["partition"], "/dev/sdb1")
        order = [a[0] for a, _ in self.rec.calls if a[0] in ("umount", "wipefs", "sfdisk", "mkfs.exfat")]
        self.assertEqual(order, ["umount", "umount", "wipefs", "sfdisk", "mkfs.exfat"])
        table = [s for a, s in self.rec.calls if a[0] == "sfdisk"][0]
        self.assertIn("label: dos", table)  # 500 GB: MBR
        self.assertTrue(self.rec.ran("mkfs.exfat", "-L", "DT-COPY2", "/dev/sdb1"))

    def test_a_big_drive_gets_gpt(self):
        self.drive(gb=4000)
        self.job("prepare-drive", "/dev/sdb", "DT-COPY2")
        table = [s for a, s in self.rec.calls if a[0] == "sfdisk"][0]
        self.assertIn("label: gpt", table)

    def test_the_microsd_card(self):
        self.drive(name="mmcblk0", usb=False, gb=256)
        code, result = self.job("prepare-drive", "/dev/mmcblk0", "DT-COPY2")
        self.assertEqual(result["partition"], "/dev/mmcblk0p1")

    def test_never_the_tablets_built_in_storage(self):
        # eMMC shows up as mmcblk too, but says MMC: never erased or formatted.
        self.drive(name="mmcblk0", usb=False, gb=64, kind="MMC")
        result = self.job("prepare-drive", "/dev/mmcblk0", "DT-COPY2")[1]
        self.assertIn("built-in storage", result["error"])
        self.assertFalse(self.rec.ran("wipefs"))
        self.assertIn("built-in", self.job("format-card", "/dev/mmcblk0p1", "FIELDCARD")[1]["error"])
        self.assertFalse(self.rec.ran("mkfs.exfat"))

    def test_never_an_internal_disk(self):
        self.drive(usb=False)
        code, result = self.job("prepare-drive", "/dev/sdb", "DT-COPY2")
        self.assertFalse(result["ok"])
        self.assertFalse(self.rec.ran("wipefs"))

    def test_never_the_system_or_the_photos(self):
        self.drive(mounts="/dev/sdb2 / xfs rw 0 0\n")
        self.assertIn("system", self.job("prepare-drive", "/dev/sdb", "DT-COPY2")[1]["error"])
        self.drive(mounts=f"/dev/sdb1 {HOME}/Pictures xfs rw 0 0\n")
        self.assertIn("photos", self.job("prepare-drive", "/dev/sdb", "DT-COPY2")[1]["error"])
        self.assertFalse(self.rec.ran("wipefs"))

    def test_never_a_cameras_card_in_a_reader(self):
        self.drive(gb=256, mounts="/dev/sdb1 /run/media/u/Untitled exfat rw 0 0\n")
        os.makedirs(os.path.join(self.root, "run/media/u/Untitled/DCIM/Camera01"))
        result = self.job("prepare-drive", "/dev/sdb", "DT-COPY2")[1]
        self.assertFalse(result["ok"])
        self.assertIn("camera", result["error"])
        self.assertFalse(self.rec.ran("wipefs"))

    def test_doubletakes_own_card_with_a_dcim_folder_can_be_prepared(self):
        self.drive(gb=256, mounts="/dev/sdb1 /run/media/u/DT-COPY2 exfat rw 0 0\n")
        os.makedirs(os.path.join(self.root, "run/media/u/DT-COPY2/DCIM"))
        os.makedirs(os.path.join(self.root, "run/media/u/DT-COPY2/.fieldstation"))
        open(os.path.join(self.root, "run/media/u/DT-COPY2/.fieldstation/card.json"), "w").close()
        self.assertTrue(self.job("prepare-drive", "/dev/sdb", "DT-COPY2")[1]["ok"])

    def test_only_whole_disks_and_sensible_labels(self):
        self.drive()
        for args in (("/dev/sdb1", "DT-COPY2"), ("/dev/nvme0n1", "DT-COPY2"), ("/dev/sdb", "bad;label"), ("/dev/sdb; rm", "X")):
            self.assertFalse(self.job("prepare-drive", *args)[1]["ok"], args)


class ShutdownFadeTests(HelperBase):
    JOURNAL = ("1791900000.000 rynzler systemd-logind[900]: System is powering down.\n"
               "1791900002.500 rynzler systemd[1]: Stopping ...\n"
               "1791900006.000 rynzler systemd-shutdown[1]: Syncing filesystems.\n")

    def setUp(self):
        super().setUp()
        theme = os.path.join(self.root, "usr/share/plymouth/themes/doubletake")
        os.makedirs(theme)
        with open(os.path.join(theme, "doubletake.script"), "w") as fh:
            fh.write("x = 1;\nshutdown_fade = 50;  # ticks; doubletake-helper splash tune\n")
        self.rec.answers = {"journalctl": (0, self.JOURNAL, "")}

    def test_the_fade_matches_the_last_shutdown(self):
        code, result = self.job("splash", "tune")
        self.assertEqual((result["shutdown_seconds"], result["fade_seconds"]), (6.0, 4.5))
        self.assertIn("shutdown_fade = 225;", self.file("/usr/share/plymouth/themes/doubletake/doubletake.script"))

    def test_kept_within_limits(self):
        self.assertEqual(helper.last_shutdown_seconds("1.0 a: System is rebooting.\n41.0 b: done\n"), 40.0)
        self.rec.answers = {"journalctl": (0, "1.0 a: System is rebooting.\n41.0 b: done\n", "")}
        self.assertEqual(self.job("splash", "tune")[1]["fade_seconds"], 8.0)

    def edition_script(self, edition, picture=True, hostname="rynzler"):
        theme = os.path.join(self.root, "usr/share/plymouth/themes/doubletake")
        with open(os.path.join(theme, "doubletake.script"), "w") as fh:
            fh.write('edition = "";  # doubletake-helper splash tune\nshutdown_fade = 50;  # ticks\n')
        picture_path = os.path.join(theme, "wordmark-tron.png")
        if picture:
            open(picture_path, "w").close()
        elif os.path.exists(picture_path):
            os.remove(picture_path)
        os.makedirs(os.path.join(self.root, "etc"), exist_ok=True)
        with open(os.path.join(self.root, "etc/hostname"), "w") as fh:
            fh.write(hostname + "\n")
        state = self.sys.state()
        state.pop("edition", None)
        if edition:
            state["edition"] = edition
        self.sys.save_state(state)

    SCRIPT = "/usr/share/plymouth/themes/doubletake/doubletake.script"

    def boot_on(self, initrd_line='edition = "";  # doubletake-helper splash tune\n'):
        state = self.sys.state()
        state["splash"] = True
        self.sys.save_state(state)
        self.rec.answers.update({"grubby --default-kernel": (0, "/boot/vmlinuz-5.14.0-611.el9.x86_64\n", ""),
                                 "uname": (0, "5.14.0-611.el9.x86_64\n", ""),
                                 "lsinitrd": (0, initrd_line, "")})

    def test_switched_on_by_hand_and_rebuilt_there_and_then(self):
        self.edition_script("")
        self.boot_on()
        code, result = self.job("edition", "tron")
        self.assertTrue(result["ok"])
        self.assertIn('edition = "-tron";', self.file(self.SCRIPT))
        self.assertTrue(self.rec.ran("dracut", "-f", "/boot/initramfs-5.14.0-611.el9.x86_64.img"))
        self.assertEqual(self.job("edition", "status")[1]["edition"], "tron")
        self.rec.answers["lsinitrd"] = (0, 'edition = "-tron";  # doubletake-helper splash tune\n', "")
        code, result = self.job("edition", "off")
        self.assertIn('edition = "";', self.file(self.SCRIPT))
        self.assertEqual(result["rebuilt"], ["5.14.0-611.el9.x86_64"])
        self.assertEqual(self.job("edition", "status")[1]["edition"], "")

    def test_the_tablets_name_no_longer_matters(self):
        self.edition_script("", hostname="tron")
        self.job("splash", "tune")
        self.assertIn('edition = "";', self.file(self.SCRIPT))
        self.edition_script("tron", hostname="doubletake")
        self.job("splash", "tune")
        self.assertIn('edition = "-tron";', self.file(self.SCRIPT))
        self.assertFalse([c for c in self.rec.calls if c[0][0] == "dracut"])  # the boot screen is off here

    def test_only_a_known_edition_is_accepted(self):
        code, result = self.job("edition", "electron")
        self.assertFalse(result["ok"])

    def test_a_start_up_image_without_the_line_is_rebuilt_next_time(self):
        # The script already has the line (a rebuild was cut short by a restart): the image is checked.
        self.edition_script("tron")
        self.job("splash", "tune")
        self.boot_on()
        self.job("splash", "tune")
        self.assertTrue(self.rec.ran("dracut", "-f", "/boot/initramfs-5.14.0-611.el9.x86_64.img"))
        self.rec.calls.clear()
        self.rec.answers["lsinitrd"] = (0, 'edition = "-tron";  # doubletake-helper splash tune\n', "")
        self.job("splash", "tune")
        self.assertFalse(self.rec.ran("dracut"))  # carries it: left alone

    def test_an_upgrade_cannot_wipe_the_line_or_the_fade(self):
        self.edition_script("tron")
        self.job("splash", "tune")  # the line, and the fade timed to the last shutdown
        theme = "/usr/share/plymouth/themes/doubletake"
        # The new package: a fresh template, with neither.
        self.sys.write(f"{theme}/doubletake-template.script",
                       'edition = "";  # doubletake-helper splash tune\nshutdown_fade = 50;  # ticks\nnew = 1;\n')
        self.sys.write(f"{theme}/doubletake.plymouth", "[Plymouth Theme]\n")
        self.rec.answers["plymouth-set-default-theme"] = (0, "doubletake\n", "")
        self.assertTrue(self.job("splash", "refresh")[1]["ok"])
        text = self.file(self.SCRIPT)
        self.assertIn('edition = "-tron";', text)
        self.assertIn("shutdown_fade = 225;", text)
        self.assertIn("new = 1;", text)  # and the template's own changes are in

    def test_the_still_fallback_becomes_the_full_screen_once_the_plugin_is_there(self):
        self.edition_script("")
        theme = "/usr/share/plymouth/themes/doubletake"
        self.sys.write(f"{theme}/doubletake-script.plymouth", "ModuleName=script\n")
        self.sys.write(f"{theme}/doubletake.plymouth", "ModuleName=two-step\n")
        self.boot_on(initrd_line='edition = "";  # doubletake-helper splash tune\n')
        self.job("splash", "tune")
        self.assertIn("two-step", self.file(f"{theme}/doubletake.plymouth"))  # no plugin yet: left
        self.sys.write("/usr/lib64/plymouth/script.so", "")
        self.job("splash", "tune")
        self.assertIn("script", self.file(f"{theme}/doubletake.plymouth"))
        self.assertTrue(self.rec.ran("dracut", "-f", "/boot/initramfs-5.14.0-611.el9.x86_64.img"))

    def test_without_the_pictures_it_stays_field_station(self):
        self.edition_script("tron", picture=False)  # an install without the pictures
        self.job("splash", "tune")
        self.assertIn('edition = "";', self.file(self.SCRIPT))

    def test_no_shutdown_found_changes_nothing(self):
        self.rec.answers = {"journalctl": (0, "1.0 a: something\n", "")}
        self.assertTrue(self.job("splash", "tune")[1]["skipped"])
        self.assertIn("shutdown_fade = 50;", self.file("/usr/share/plymouth/themes/doubletake/doubletake.script"))
