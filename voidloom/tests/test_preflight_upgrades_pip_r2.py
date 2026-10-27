"""Round 2 reflection N3: preflight installed torch with the venv's bundled pip (22.3.1 cannot resolve the CPU index:
'No matching distribution found for flit_core'), so it read RED on machines install.sh handles."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("preflight_under_test", ROOT / "install" / "preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pip_is_upgraded_in_the_venv_before_torch(tmp_path, monkeypatch):
    pf = _load()
    calls = []

    def fake_run(cmd, env=None, timeout=1800):
        calls.append(list(cmd))
        joined = " ".join(map(str, cmd))
        if " -m pip install" in joined and "--upgrade pip" not in joined:
            return 1, "", "stop here", 0.0          # the torch install: end the run, we only need the order
        return 0, "", "", 0.0

    monkeypatch.setattr(pf, "_run", fake_run)
    monkeypatch.setattr(pf, "machine_facts", lambda py: {"os": "Linux", "arch": "x86_64", "ram_gib": 16,
                                                         "candidate_python_version": "3.11"})
    monkeypatch.setattr(pf, "plan_for", lambda v: {"channel": {"index": "https://download.pytorch.org/whl/cpu"},
                                                   "backend": "cpu"})
    try:
        pf.main(["--out", str(tmp_path / "pf.json"), "--workdir", str(tmp_path)])
    except SystemExit:
        pass
    pips = [" ".join(map(str, c)) for c in calls if "-m pip install" in " ".join(map(str, c))]
    assert pips and "--upgrade pip" in pips[0], pips
    assert any("torch" in p for p in pips[1:]), pips
