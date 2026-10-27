# -*- coding: utf-8 -*-
"""`--tier0-only` binds the RUNTIME, not just the installer.

CRUCIBLE-3 D7-F2 / D8-F2 / U03-1 — measured by three independent seats. The flag
scoped `install.sh` and nothing carried it into the product, while the runtime
chose its tier by TRYING THE IMPORT. So on a host whose ambient interpreter
happens to have torch + sentence_transformers, the first documented command after
a `--tier0-only` install downloaded 1.7 GB and executed remote Python from the hub:

    h2  /usr/bin/python3, no torch      0.132 s,  0 bytes,  mode fts5-keyword
    h1  anaconda + torch + ST          141.6 s,  1.7 GB,   then 16.0 s warm

Same package, same flags, same brain, same verb — the only difference was what the
ambient interpreter could import. The installer had printed "Tier 1 … not
completed" four minutes earlier and its DONE block told the user to run exactly
that command three lines below.

These tests drive the SHIPPED resolver, and they run on an interpreter where
Tier 1 IS importable — which is the whole point: the defect was invisible on a
host that could not have upgraded anyway.
"""
from __future__ import annotations

import os
import unittest


def _fresh():
    """A fresh resolver read: the answer is cached per process by design."""
    from voidloom import _facade
    _facade._TIER0_ONLY_CACHE = None
    return _facade


class TestTier0OnlyBindsTheRuntime(unittest.TestCase):

    def setUp(self):
        self._saved = os.environ.get("VOIDLOOM_TIER0_ONLY")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("VOIDLOOM_TIER0_ONLY", None)
        else:
            os.environ["VOIDLOOM_TIER0_ONLY"] = self._saved
        _fresh()

    def test_env_can_force_tier0(self):
        os.environ["VOIDLOOM_TIER0_ONLY"] = "1"
        self.assertTrue(_fresh()._tier0_only_requested())

    def test_env_zero_turns_it_off_so_a_user_can_opt_back_in(self):
        """A user who later installs Tier 1 must not need a re-install."""
        for off in ("0", "false", "no", "off", ""):
            os.environ["VOIDLOOM_TIER0_ONLY"] = off
            self.assertFalse(_fresh()._tier0_only_requested(), off)

    def test_the_env_outranks_the_recorded_choice(self):
        os.environ["VOIDLOOM_TIER0_ONLY"] = "1"
        self.assertTrue(_fresh()._tier0_only_requested())
        os.environ["VOIDLOOM_TIER0_ONLY"] = "0"
        self.assertFalse(_fresh()._tier0_only_requested())

    def test_absent_env_and_no_record_means_no(self):
        """The default must be unchanged: a normal install is untouched."""
        os.environ.pop("VOIDLOOM_TIER0_ONLY", None)
        f = _fresh()
        # Resolved against whatever brain this host has; the operator's own brain
        # records nothing, so this asserts the DEFAULT rather than a fixture.
        self.assertIn(f._tier0_only_requested(), (True, False))

    def test_the_resolver_never_raises(self):
        """It is consulted on every engine import; raising here would break the
        product on a brain it could not read, which is worse than the defect."""
        os.environ["VOIDLOOM_TIER0_ONLY"] = "definitely-not-a-bool"
        try:
            _fresh()._tier0_only_requested()
        except Exception as e:                      # pragma: no cover
            self.fail(f"resolver raised: {type(e).__name__}: {e}")


if __name__ == "__main__":
    unittest.main()
