"""Every-card-hidden must not crash torch (2026-10-15, WSL2 + torch 2.6.0+cu124: is_available() -> SIGSEGV)."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORGANS = ROOT / "organs"


def _run(code, env_extra):
    env = {k: v for k, v in os.environ.items() if k not in ("CUDA_VISIBLE_DEVICES", "PYTORCH_NVML_BASED_CUDA_CHECK")}
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)


class HiddenCudaProbe(unittest.TestCase):
    def test_pin_sets_the_nvml_check_when_every_card_is_hidden(self):
        for hidden in ("", "-1", "none", "NoDevFiles", "-1,1"):
            r = _run("import sys; sys.path.insert(0, %r); import voidloom_device as d; d.apply_cuda_pin();"
                     "import os; print(os.environ.get('PYTORCH_NVML_BASED_CUDA_CHECK'))" % str(ORGANS),
                     {"CUDA_VISIBLE_DEVICES": hidden})
            self.assertEqual(r.stdout.strip().splitlines()[-1:], ["1"], (hidden, r.stdout, r.stderr))

    def test_pin_leaves_a_visible_card_alone(self):
        r = _run("import sys; sys.path.insert(0, %r); import voidloom_device as d; d.apply_cuda_pin();"
                 "import os; print(os.environ.get('PYTORCH_NVML_BASED_CUDA_CHECK'))" % str(ORGANS),
                 # gpu-pin-gate: a subprocess that never imports torch; the value only has to be a non-empty visible set
                 {"CUDA_VISIBLE_DEVICES": "1"})
        self.assertNotEqual(r.stdout.strip().splitlines()[-1:], ["1"], r.stdout)

    def test_after_the_pin_torch_answers_false_instead_of_crashing(self):
        try:
            import importlib.util
            if importlib.util.find_spec("torch") is None:
                self.skipTest("torch not installed")
        except Exception:
            self.skipTest("torch not importable")
        # Round 2 (M1): every spelling of "no device" segfaulted at 6bb45ee except '' and '-1' (rc 139 measured);
        # '7' is an index no machine here lists (the resolver's own class -- nvidia-smi's table, when it can be asked).
        for hidden in ("", "-1", "none", "NoDevFiles", "-1,1", "7"):
            r = _run("import sys; sys.path.insert(0, %r); import voidloom_device as d; d.apply_cuda_pin();"
                     "import torch; print(torch.cuda.is_available())" % str(ORGANS),
                     {"CUDA_VISIBLE_DEVICES": hidden})
            self.assertEqual(r.returncode, 0, "%r rc=%s stderr=%s" % (hidden, r.returncode, r.stderr[-400:]))
            self.assertEqual(r.stdout.strip().splitlines()[-1], "False", hidden)

    def test_a_cpu_declaration_leaves_torch_no_card_to_take(self):
        """Round 2 (M3): with a card visible and WIZARD_ACCELERATOR=cpu, torch must not see CUDA -- peft's
        infer_device() asks torch.cuda.is_available() and initialised CUDA on the visible card at 6bb45ee."""
        try:
            import importlib.util
            if importlib.util.find_spec("torch") is None:
                self.skipTest("torch not installed")
        except Exception:
            self.skipTest("torch not importable")
        r = _run("import sys; sys.path.insert(0, %r); import voidloom_device as d; d.apply_accelerator_pin();"
                 "import os, torch; print(repr(os.environ.get('CUDA_VISIBLE_DEVICES')), torch.cuda.is_available(),"
                 " torch.cuda.is_initialized(), d.torch_device())" % str(ORGANS),
                 # gpu-pin-gate: the test's own visible card (this host's compute card); the product must HIDE it
                 {"CUDA_VISIBLE_DEVICES": "1", "WIZARD_CUDA_DEVICE": "1", "WIZARD_ACCELERATOR": "cpu"})
        self.assertEqual(r.returncode, 0, "rc=%s stderr=%s" % (r.returncode, r.stderr[-400:]))
        self.assertEqual(r.stdout.strip().splitlines()[-1], "'' False False cpu")


if __name__ == "__main__":
    unittest.main()
