"""Friend-package smoke (2026-10-16): preflight read RED on every fresh CPU/nomic machine.

Its "models" step snapshot-downloaded nomic-embed-text-v1.5 only; the model's code lives in a second repo
(nomic-ai/nomic-bert-2048, pulled by trust_remote_code) and the organs load a PINNED revision. The loader then went
local-only (the repo looked cached), P6 failed offline, and preflight.json said only "SystemExit: 1".
"""
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_models_step_warms_the_cache_through_the_organs_own_loader(tmp_path, monkeypatch):
    pf = _load("install/preflight.py", "preflight_models_under_test")
    seen = []

    def fake_run(cmd, env=None, timeout=1800):
        seen.append((list(map(str, cmd)), dict(env or {})))
        if any("_encode" in str(x) for x in cmd):
            return 1, "", "stop here: only the download step's shape is under test", 0.0
        return 0, "", "", 0.0

    monkeypatch.setattr(pf, "_run", fake_run)
    monkeypatch.setattr(pf, "machine_facts", lambda py: {"os": "Linux", "arch": "x86_64", "ram_gib": 16,
                                                         "candidate_python_version": "3.12"})
    monkeypatch.setattr(pf, "plan_for", lambda v: {"channel": {"index": "https://download.pytorch.org/whl/cpu"},
                                                   "backend": "cpu", "reranker": {"enabled": False}})
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")          # an inherited offline flag must not reach the download step
    try:
        pf.main(["--out", str(tmp_path / "pf.json"), "--workdir", str(tmp_path)])
    except SystemExit:
        pass
    warm = [(c, e) for c, e in seen if any("claude_memory" in x for x in c) and any("_encode" in x for x in c)]
    assert warm, [c for c, _e in seen]
    cmd, env = warm[0]
    assert not any("snapshot_download" in x and "nomic" in x for x in cmd)
    assert env.get("WIZARD_EMBED_MODEL") == "nomic" and env.get("WIZARD_ACCELERATOR") == "cpu"
    assert env.get("CUDA_VISIBLE_DEVICES") == "" and "HF_HUB_OFFLINE" not in env


def test_a_step_that_exits_through_an_organ_refusal_keeps_its_message(monkeypatch, capsys):
    tp = _load("install/tier1_probe.py", "tier1_probe_under_test")

    def boom(a):
        print(json.dumps({"status": "error", "message": "Failed to load embedding model: couldn't find them in "
                                                         "the cached files"}))
        sys.exit(1)

    monkeypatch.setitem(tp.STEPS, "PX", boom)
    rc = tp.one_step(types.SimpleNamespace(name="PX"))
    out = capsys.readouterr().out.strip().splitlines()
    rep = json.loads(out[-1])
    assert rc == 1 and len(out) == 1, out                     # the probe's own report is the ONLY stdout line
    assert rep["error"].startswith("SystemExit: 1") and "couldn't find them in the cached files" in rep["error"]


def test_a_model_load_never_prints_to_stdout(tmp_path):
    fake = tmp_path / "fakes"
    (fake / "sentence_transformers").mkdir(parents=True)
    (fake / "sentence_transformers" / "__init__.py").write_text(textwrap.dedent('''
        class SentenceTransformer:
            def __init__(self, *a, **k):
                print("<All keys matched successfully>")
            def encode(self, *a, **k):
                return [[0.0] * 768]
    '''))
    code = ("import sys; sys.path.insert(0, %r); import claude_memory as cm; cm._load_model(); "
            "print('ONLY-THIS')" % str(ROOT / "organs"))
    env = dict(os.environ, PYTHONPATH=str(fake), CUDA_VISIBLE_DEVICES="", PYTORCH_NVML_BASED_CUDA_CHECK="1",
               WIZARD_ACCELERATOR="cpu", HF_HUB_OFFLINE="1")
    p = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env=env, timeout=300)
    assert p.returncode == 0, p.stderr[-800:]
    assert p.stdout.strip() == "ONLY-THIS", p.stdout
    assert "<All keys matched successfully>" in p.stderr


@pytest.mark.skipif(os.environ.get("VOIDLOOM_NETWORK_TESTS") != "1",
                    reason="downloads ~550 MB from the Hugging Face hub; set VOIDLOOM_NETWORK_TESTS=1")
def test_empty_cache_then_offline_load_succeeds(tmp_path):
    """The smoke's failure, end to end: warm an EMPTY cache the way the preflight now does, then load offline."""
    organs = str(ROOT / "organs")
    base = dict(os.environ, HF_HOME=str(tmp_path / "hf"), WIZARD_EMBED_MODEL="nomic", WIZARD_ACCELERATOR="cpu",
                CUDA_VISIBLE_DEVICES="", PYTORCH_NVML_BASED_CUDA_CHECK="1")
    base.pop("HF_HUB_OFFLINE", None)
    base.pop("TRANSFORMERS_OFFLINE", None)
    warm = "import sys; sys.path.insert(0, %r); import claude_memory as cm; cm._encode('warm up', prefix='search_query')" % organs
    p = subprocess.run([sys.executable, "-B", "-c", warm], capture_output=True, text=True, env=base, timeout=2400)
    assert p.returncode == 0, p.stderr[-1500:]
    off = dict(base, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    p = subprocess.run([sys.executable, "-B", "-c", warm + "; print('OFFLINE-OK')"], capture_output=True, text=True,
                       env=off, timeout=900)
    assert p.returncode == 0 and "OFFLINE-OK" in p.stdout, (p.stdout[-800:], p.stderr[-1500:])
