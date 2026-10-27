"""The suite's children can never reach the terminal the suite was run from (model-choice round 1, Wave B's bounded item).

Measured under a pty (iterations/scratch_records/r1_waveC/pty_suite_proof.py): without conftest.py's detachment an
installer test run FROM A TERMINAL printed the overnight-thinker question on the operator's terminal and waited for an
answer; with it, not asked, passed. This binds the mechanism: every subprocess this suite starts is a session leader
(no controlling terminal, so /dev/tty cannot be opened), unless the caller chose its own session handling -- the
scripted-terminal drivers pass preexec_fn (setsid + TIOCSCTTY) on purpose, and that must still work.
"""
import os
import subprocess
import sys
import unittest

PROBE = ("import os\n"
         "try:\n"
         "    fd = os.open('/dev/tty', os.O_RDWR); os.close(fd); tty = 'OPENED'\n"
         "except OSError:\n"
         "    tty = 'NONE'\n"
         "print(os.getsid(0) == os.getpid(), tty)\n")


class TestTheSuitesChildrenAreDetached(unittest.TestCase):

    @unittest.skipUnless(os.name == "posix", "sessions and /dev/tty are POSIX")
    def test_a_child_is_a_session_leader_with_no_terminal(self):
        out = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, timeout=60).stdout.split()
        self.assertEqual(out, ["True", "NONE"], "a child of the suite could reach the operator's terminal: %r" % out)

    @unittest.skipUnless(os.name == "posix", "sessions are POSIX")
    def test_a_caller_that_chooses_its_own_session_handling_is_left_alone(self):
        def pre():
            os.environ["VOIDLOOM_PREEXEC_RAN"] = "1"      # runs in the child only
        p = subprocess.run([sys.executable, "-c", "import os; print(os.environ.get('VOIDLOOM_PREEXEC_RAN'))"],
                           capture_output=True, text=True, timeout=60, preexec_fn=pre)
        self.assertEqual(p.stdout.strip(), "1", "a caller's own preexec_fn must still run (the pty drivers need it)")


if __name__ == "__main__":
    unittest.main()
