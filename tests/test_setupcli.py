import io
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest import mock

from doubletake import firstrun, setupcli


def answers(*lines):
    it = iter(lines)
    return mock.patch("builtins.input", lambda prompt="": next(it))


class FakeEngine:
    def __init__(self, prefs=None, state=None):
        self.prefs = dict({"copy_what": "raw_jpeg", "second_copy_device": "microsd"}, **(prefs or {}))
        self._state = state or {}
        self.changes, self.posts, self.password = [], [], ""

    def settings(self):
        return self.prefs

    def state(self):
        return self._state

    def change(self, **c):
        self.changes.append(c)
        self.prefs.update(c)
        return True

    def post(self, path, data=None, label=""):
        self.posts.append((path, data))
        return {"ok": True}

    def nas_done(self):
        return ""


class AskingTests(unittest.TestCase):
    def test_enter_keeps_what_is_chosen(self):
        with answers(""), redirect_stdout(io.StringIO()):
            self.assertEqual(setupcli.choose([("a", "A"), ("b", "B")], "b"), "b")

    def test_a_number_out_of_range_is_asked_again(self):
        with answers("7", "x", "1"), redirect_stdout(io.StringIO()):
            self.assertEqual(setupcli.choose([("a", "A"), ("b", "B")], "b"), "a")

    def test_yes_or_no_with_a_default(self):
        with answers("", "n", "Yes"), redirect_stdout(io.StringIO()):
            self.assertTrue(setupcli.yes("?", True))
            self.assertFalse(setupcli.yes("?", True))
            self.assertTrue(setupcli.yes("?", False))


class StepTests(unittest.TestCase):
    def test_what_to_copy_is_saved(self):
        dt = FakeEngine()
        with answers("3"), redirect_stdout(io.StringIO()):
            setupcli.copy_step(dt)
        self.assertEqual(dt.changes, [{"copy_what": "all"}])

    def test_a_new_card_is_erased_only_after_agree(self):
        dt = FakeEngine(state={"card": {"state": "new", "kind": "microsd", "total": 256e9}})
        with answers("1", "1", "nope"), redirect_stdout(io.StringIO()):
            setupcli.second_step(dt)
        self.assertNotIn("/api/card/prepare", [p for p, _ in dt.posts])
        with answers("", "1", "agree"), redirect_stdout(io.StringIO()):
            setupcli.second_step(dt)
        self.assertIn(("/api/card/prepare", {"agree": "agree"}), dt.posts)

    def test_no_nas_without_the_system_setup(self):
        dt = FakeEngine(state={"nas": {"installed": False}})
        with redirect_stdout(io.StringIO()) as out:
            setupcli.nas_step(dt)
            setupcli.networks_step(dt)
        self.assertEqual(dt.posts, [])
        self.assertIn("system setup", out.getvalue())


class RestartTests(unittest.TestCase):
    def test_a_restart_held_off_by_the_desktop_goes_through_sudo(self):
        class Held(FakeEngine):
            closed = False

            def post(self, path, data=None, label=""):
                return {"ok": False, "error": "Call to Reboot failed: Operation denied due to active block inhibitor"}

            def close(self):
                self.closed = True

        dt, ran = Held(), []
        with answers(""), redirect_stdout(io.StringIO()), \
                mock.patch.object(setupcli.subprocess, "run", lambda args: ran.append(args) or subprocess.CompletedProcess(args, 0)):
            self.assertEqual(setupcli.finish_step(dt), 0)
        self.assertTrue(dt.closed)
        self.assertEqual(ran, [["sudo", "systemd-run", "--quiet", "--on-active=5", "--timer-property=AccuracySec=1s",
                                "systemctl", "reboot"]])

    def test_a_held_off_restart_reported_after_the_reply_goes_through_sudo(self):
        # The way out runs behind the reply (1.0 pre-release 2): the refusal comes in the state.
        class Later(FakeEngine):
            closed = False

            def post(self, path, data=None, label=""):
                return {"ok": True, "error": ""}

            def state(self):
                return {"leaving": {"done": True, "error": "Call to Reboot failed: Operation denied due to active block inhibitor"}}

            def close(self):
                self.closed = True

        dt, ran = Later(), []
        with answers(""), redirect_stdout(io.StringIO()), \
                mock.patch.object(setupcli.subprocess, "run", lambda args: ran.append(args) or subprocess.CompletedProcess(args, 0)):
            self.assertEqual(setupcli.finish_step(dt), 0)
        self.assertTrue(dt.closed)
        self.assertEqual(ran, [["sudo", "systemd-run", "--quiet", "--on-active=5", "--timer-property=AccuracySec=1s",
                                "systemctl", "reboot"]])


class SudoTests(unittest.TestCase):
    def test_the_terminal_setup_runs_the_helper_through_sudo(self):
        seen = []

        def run(args):
            seen.append(args)
            return subprocess.CompletedProcess(args, 0, '{"ok": true}\n', "")

        with mock.patch.object(firstrun, "packaged", return_value=True):
            self.assertEqual(firstrun.system_setup("/tmp/lib-x", run=run), "")
        self.assertEqual(seen[0][:3], ["sudo", "-n", str(firstrun.PACKAGE_HELPER)])

    def test_sudo_refusing_says_so(self):
        run = lambda args: subprocess.CompletedProcess(args, 1, "", "a password is required")
        with mock.patch.object(firstrun, "packaged", return_value=True):
            self.assertIn("sudo did not run", firstrun.system_setup("/tmp/lib-x", run=run))


if __name__ == "__main__":
    unittest.main()
