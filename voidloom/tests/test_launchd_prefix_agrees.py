"""The launchd label prefix is ONE fact stated in two shell files.

Before 2026-09-17 install.sh and cron/platform-detection.sh each carried their
own prefix, both reverse-DNS under the author's organisation and differing in
the first label -- two literals for one product, so a macOS host that ran both
would have grown two families of launch agents. Neither name was the product's. The repair put the literal in
cron/_config_dir.sh (sourced by every cron script) and left install.sh its own
copy, because install.sh is the installer and does not source package files
at that point. Two copies of one fact is an unversioned coupling; this test
is the version.

Red at parent: the pre-repair tree fails on the first assertion.

This file binds the CHECKOUT (install.sh is root=null: it ships, it is not laid).
On an installed brain the module SKIPS with the reason (`pytestmark`) --
UNMEASURABLE, not PASS.
"""
import os
import re
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
_LIT = re.compile(r'^LAUNCHD_PREFIX="([^"]*)"', re.M)

pytestmark = pytest.mark.skipif(
    not (ROOT / "install.sh").is_file() or not (ROOT / ".git").exists(),
    reason="NOT A CHECKOUT: %s has no install.sh/.git (install.sh is root=null: ships, not laid). "
           "Run from the package checkout or set VOIDLOOM_ROOT=<checkout>." % ROOT)


def _prefix(rel: str) -> str:
    text = (ROOT / rel).read_text()
    found = _LIT.findall(text)
    assert len(found) == 1, f"{rel}: expected exactly one LAUNCHD_PREFIX literal, found {found}"
    return found[0]


def test_install_and_cron_agree_on_the_prefix():
    a = _prefix("install.sh")
    b = _prefix("cron/_config_dir.sh")
    assert a == b, f"install.sh says {a!r}, cron/_config_dir.sh says {b!r}"


def test_prefix_names_the_product_not_an_organisation():
    p = _prefix("cron/_config_dir.sh")
    assert p == "voidloom", p          # the product's own name, exactly -- nothing else may be appended


def test_platform_detection_uses_the_variable_not_a_literal():
    """Every launchd label the generator emits is built from ${LAUNCHD_PREFIX}."""
    text = (ROOT / "cron/platform-detection.sh").read_text()
    literal_labels = re.findall(r'"(?:ai|com)\.[a-z]+\.wizard-library\.[a-z-]+"', text)
    assert literal_labels == [], literal_labels
    assert text.count("${LAUNCHD_PREFIX}.") >= 6, text.count("${LAUNCHD_PREFIX}.")


def test_cron_scripts_source_the_one_resolver():
    """No cron script may carry its own config-dir literal."""
    for rel in ("cron/platform-detection.sh",
                "cron/setup-sleep-loop-cron.sh",
                "cron/setup-dreaming-cron.sh"):
        text = (ROOT / rel).read_text()
        assert '. "${SCRIPT_DIR}/_config_dir.sh"' in text, rel
        assert 'CONFIG_DIR="${HOME}/.' not in text, f"{rel} carries its own config-dir literal"
