"""`voidloom doctor` prints the peft the package pins: the fix a person pastes installs what was tested."""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from voidloom.tests.test_doctor_brain_verdict import JINA, JINA_DIM, ROOT, _Brain


def required_pin() -> str:
    text = Path(ROOT / "install" / "requirements.txt").read_text(encoding="utf-8")
    pins = re.findall(r"^peft==\S+", text, flags=re.M)
    assert len(pins) == 1, pins
    return pins[0]


class TheFixIsThePin(_Brain):
    LOCK = None
    TABLE, DIM = JINA, JINA_DIM

    def test_saboteur_the_printed_pin_is_the_requirements_pin(self):
        self.conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {JINA} USING vec0(memory_id INTEGER PRIMARY KEY, "
                          f"embedding FLOAT[{JINA_DIM}])")
        self.memory(whash="unresolved")
        with mock.patch.dict("sys.modules", {"peft": None}):
            reason = self.doctor()["reason"]
        self.assertIn("needs peft", str(reason))
        self.assertEqual(re.findall(r"install (\S*peft\S*)", reason), [required_pin()])


if __name__ == "__main__":
    unittest.main()
