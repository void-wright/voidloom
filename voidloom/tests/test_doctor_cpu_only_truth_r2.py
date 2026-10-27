"""Round 2 reflection N2: doctor said 'CPU only: no job of this brain uses a card' while a card stayed visible."""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "organs"))
from voidloom import cli  # noqa: E402


def _fake_vd(monkeypatch, *, declared_cpu, hidden, basis):
    vd = types.SimpleNamespace(
        accelerator_report=lambda confirm=False: {"backend": "cpu", "torch_device": "cpu", "basis": basis,
                                                  "declared": {"value": "cpu" if declared_cpu else None}},
        cpu_declaration=lambda: ("env" if declared_cpu else None),
        hides_every_cuda_card=lambda environ=None: hidden,
        is_degraded=lambda b: "degraded" in b,
    )
    hr = types.SimpleNamespace(accelerator_proof_label=lambda backend, rec: ("PROVEN", "test"))
    monkeypatch.setitem(sys.modules, "voidloom_device", vd)
    monkeypatch.setitem(sys.modules, "hybrid_retrieval", hr)


def test_a_degraded_cpu_answer_with_a_visible_card_is_not_called_cpu_only(monkeypatch):
    _fake_vd(monkeypatch, declared_cpu=False, hidden=False, basis="degraded: env file unreadable")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")  # gpu-pin-gate: a fake resolver; nothing imports torch here
    out = cli._doctor_accelerator()
    assert out["cpu_only"] is False and out["degraded"] is True, out
    assert "no job of this brain uses a card" not in out["line"] and "DEGRADED" in out["line"]


def test_a_cpu_declaration_is_cpu_only(monkeypatch):
    _fake_vd(monkeypatch, declared_cpu=True, hidden=True, basis="declared: cpu")
    out = cli._doctor_accelerator()
    assert out["cpu_only"] is True and "no job of this brain uses a card" in out["line"], out
