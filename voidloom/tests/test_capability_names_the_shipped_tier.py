"""The hardware detector names what the package ships, and its block does not look like the brain's live tier.

A GPU host gets the shipped GPU embedder from the table, from the block and from the shell fallback. The third tier stays
selectable by an explicit override and is never a hardware choice. `install_time_*` says what the keys are: a record
of what was chosen at install, where `active_embedding*` read like the lock's own answer.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "capability"))

import detect  # noqa: E402


def probe(mem_mb):
    return {"gpu": {"available": True, "count": 1, "memory_total": mem_mb, "compute_cap": 8.6},
            "torch_cuda": {"usable": True}, "accelerator": {"backend": "cuda"}}


class TheTable(unittest.TestCase):
    def test_control_every_row_names_a_shipped_tier(self):
        for row in detect.TIER_TABLE:
            self.assertIn(row.embed_model, ("nomic", "qwen3-0.6b"), row.name)
            self.assertNotIn("jina", row.rationale.lower(), row.name)

    def test_saboteur_the_gpu_rows_choose_qwen(self):
        rows = {r.name: r for r in detect.TIER_TABLE}
        self.assertEqual(rows["GPU-HIGH"].embed_model, "qwen3-0.6b")
        self.assertEqual(rows["GPU-MID"].embed_model, "qwen3-0.6b")
        self.assertIn("Qwen3-Embedding-0.6B", rows["GPU-HIGH"].rationale)

    def test_control_the_third_tier_stays_selectable_by_name(self):
        self.assertIn("jina", detect.EMBED_MODELS)


class TheBlock(unittest.TestCase):
    def test_saboteur_it_names_the_choice_not_the_live_tier(self):
        block = detect.capability_block({"chosen_embed_model": "nomic", "tier": "t"})
        self.assertEqual(block["install_time_embedding_choice"]["model"], "nomic")
        self.assertEqual(block["install_time_tier_map"]["recall"], "nomic")
        self.assertEqual([k for k in block if k.startswith("active_embedding")], [])
        self.assertNotIn("active_embedding", json.dumps(block))

    def test_control_the_detector_prints_no_old_key(self):
        r = subprocess.run([sys.executable, str(ROOT / "capability" / "detect.py"), "--json", "--manifest-block"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertNotIn("active_embedding", r.stdout)


class TheShellFallback(unittest.TestCase):
    """python3 is taken away; nvidia-smi is a stub that succeeds or fails."""

    def run_it(self, smi_rc, **env):
        with tempfile.TemporaryDirectory() as t:
            bin_dir = Path(t)
            smi = bin_dir / "nvidia-smi"
            smi.write_text(f"#!/bin/sh\nexit {smi_rc}\n")
            smi.chmod(smi.stat().st_mode | stat.S_IEXEC)
            e = {"PATH": f"{bin_dir}:/usr/bin:/bin", "WIZARD_PYTHON_BIN": str(bin_dir / "no-python"), **env}
            r = subprocess.run(["bash", str(ROOT / "capability" / "detect_embed_model.sh")],
                               capture_output=True, text=True, env=e, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_saboteur_a_gpu_host_is_told_the_shipped_tier(self):
        self.assertEqual(self.run_it(0), "qwen3-0.6b")

    def test_control_no_gpu_is_nomic(self):
        self.assertEqual(self.run_it(1), "nomic")

    def test_control_an_explicit_override_still_wins(self):
        self.assertEqual(self.run_it(0, WIZARD_EMBED_MODEL="jina"), "jina")


if __name__ == "__main__":
    unittest.main()
