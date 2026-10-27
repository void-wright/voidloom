"""THE RERANKER GATE (handoff round 1, lane L2; owner ruling 2026-10-14, contracts C1/C3/C4).

The reranker runs ONLY when a non-CPU accelerator is present AND the machine has >= 15.0 GiB RAM as the OS presents
it. Never on CPU-only. Configuration can only turn it OFF. Qwen3-Reranker-0.6B is canonical; bge is the fallback.

No model is ever loaded here: the accelerator is doubled at hybrid_retrieval._resolve_accelerator (the C1 seam), the
loaders are spies that fail the test if called, and the Qwen scorer runs on a fake model."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_dreamfix_tranche_a as T  # noqa: E402  (the dream-fix suite's fixtures: Brain, fake_vec)
import claude_memory as cm  # noqa: E402
import hybrid_retrieval as hr  # noqa: E402
import qwen_reranker as qr  # noqa: E402
import wizard_config as wc  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GIB = 1 << 30


@pytest.fixture(autouse=True)
def _no_config(tmp_path, monkeypatch):
    """Hermetic: no operator wizard-config.json, no inherited switch."""
    monkeypatch.setenv("WIZARD_CONFIG_PATH", str(tmp_path / "absent-wizard-config.json"))
    for k in ("VOIDLOOM_RERANKER", "WIZARD_RERANKER_ENABLED", "WIZARD_ACTIVE_PROFILE", "WIZARD_RERANKER_GATE",
              "WIZARD_RERANKER"):
        monkeypatch.delenv(k, raising=False)
    # the owner's WIZARD_RERANKER line is read from the env file at recall time (round 2, m5): never the operator's file
    monkeypatch.setenv("WIZARD_ENV_FILE", str(tmp_path / "absent-wizard-library-env"))
    wc.reload_config()
    yield
    wc._config_instance = None


def _machine(monkeypatch, backend, ram_gib, calls=None):
    dev = {"cuda": "cuda:0", "rocm": "cuda:0", "mps": "mps", "xpu": "xpu:0", "cpu": "cpu"}[backend]
    ram = int(ram_gib * GIB) if ram_gib is not None else None

    def fake(confirm, torch_mod=None):
        if calls is not None:
            calls.append(confirm)
        return backend, dev, "test:%s" % backend, False, ram
    monkeypatch.setattr(hr, "_resolve_accelerator", fake)


def _detect():
    spec = importlib.util.spec_from_file_location("vl_detect_for_gate", str(ROOT / "capability" / "detect.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------------------------------------ the rule itself
TABLE = [
    ("cuda", 23.47, True), ("cuda", 15.0, True), ("cuda", 14.99, False), ("mps", 16.0, True), ("mps", 8.0, False),
    ("rocm", 15.55, True), ("xpu", 12.0, False), ("cpu", 64.0, False), ("cpu", 8.0, False), ("cuda", None, False),
]


@pytest.mark.parametrize("backend,ram,expect", TABLE)
def test_on_only_with_an_accelerator_and_15_gib(monkeypatch, backend, ram, expect):
    _machine(monkeypatch, backend, ram)
    g = hr.reranker_gate()
    assert g["enabled"] is expect, g
    assert g["threshold_gib"] == 15.0 and g["why"]
    if backend == "cpu":
        assert g["decided_by"] == "cpu-only" and "never" in g["why"]


@pytest.mark.parametrize("backend,ram,expect", TABLE)
def test_the_live_gate_and_the_installers_sweep_are_one_rule(monkeypatch, backend, ram, expect):
    """capability/detect.py's reranker_fits (L1, the installer's sweep) and the recall path's gate agree row by row."""
    _machine(monkeypatch, backend, ram)
    fits = _detect().reranker_fits({"backend": backend, "ram_bytes": int(ram * GIB) if ram else None})
    assert fits["fits"] is hr.reranker_gate()["enabled"] is expect
    assert fits["threshold_gib"] == hr.RERANKER_RAM_THRESHOLD_GIB


def test_configuration_can_only_turn_it_off(monkeypatch, tmp_path):
    _machine(monkeypatch, "cpu", 64.0)
    cfg = tmp_path / "wizard-config.json"
    cfg.write_text(json.dumps({"reranker_enabled": True, "reranker_gate": {"accelerator": "cuda", "ram_gib": 64.0,
                                                                          "threshold_gib": 15.0}}))
    monkeypatch.setenv("WIZARD_CONFIG_PATH", str(cfg))
    wc.reload_config()
    g = hr.reranker_gate()
    assert g["enabled"] is False and g["decided_by"] == "cpu-only"     # a copied config cannot switch it on


def test_a_written_false_turns_it_off(monkeypatch, tmp_path):
    """The installer's sweep writes `"reranker_enabled": false`; it must be read (False == 0 was a 'sentinel')."""
    calls = []
    _machine(monkeypatch, "cuda", 23.47, calls)
    cfg = tmp_path / "wizard-config.json"
    cfg.write_text(json.dumps({"reranker_enabled": False}))
    monkeypatch.setenv("WIZARD_CONFIG_PATH", str(cfg))
    wc.reload_config()
    g = hr.reranker_gate()
    assert g["enabled"] is False and g["decided_by"] == "config", g
    assert calls == [False], "torch was asked although the switch was already off"


def test_the_laptop_profile_is_honoured(monkeypatch):
    _machine(monkeypatch, "cuda", 23.47)
    monkeypatch.setenv("WIZARD_ACTIVE_PROFILE", "laptop")
    wc.reload_config()
    assert hr.reranker_gate()["decided_by"] == "config"


@pytest.mark.parametrize("val", ["off", "OFF", "none", "0", "false"])
def test_voidloom_reranker_off_turns_it_off_for_the_process(monkeypatch, val):
    _machine(monkeypatch, "cuda", 23.47)
    monkeypatch.setenv("VOIDLOOM_RERANKER", val)
    g = hr.reranker_gate()
    assert g["enabled"] is False and g["decided_by"] == "process"


def test_a_resolver_fault_is_off_never_on(monkeypatch):
    def boom(confirm, torch_mod=None):
        raise RuntimeError("x")
    monkeypatch.setattr(hr, "_resolve_accelerator", boom)
    assert hr.reranker_gate()["enabled"] is False


def test_this_hosts_cpu_route_is_off_without_importing_torch():
    """Live, no doubles: CUDA_VISIBLE_DEVICES='' -> the C1 resolver answers cpu and the gate says off; torch is not
    imported (on this host torch 2.6 segfaults in torch.cuda.is_available() when every card is hidden)."""
    code = ("import sys; sys.path.insert(0, %r); import hybrid_retrieval as h; g = h.reranker_gate(); "
            "print(g['enabled'], g['accelerator'], 'torch' in sys.modules)" % str(ROOT / "organs"))
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="", WIZARD_CONFIG_PATH="/nonexistent/wizard-config.json")
    env.pop("VOIDLOOM_RERANKER", None)
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    enabled, accel, torch_loaded = out.stdout.split()
    assert (enabled, accel) == ("False", "cpu"), out.stdout
    # With NVIDIA's tools present, a hidden device list makes the CPU answer final and torch is never asked. With
    # none (a hosted CI runner, an AMD/Intel/Apple machine) the resolver is DESIGNED to ask torch whether another
    # backend exists (voidloom_device._confirm, the 'promotable' CPU answer) -- so only the first case pins torch.
    if shutil.which("nvidia-smi"):
        assert torch_loaded == "False", out.stdout


# --------------------------------------------------------------------------------- the switch in the recall path
def _no_model_loads(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a reranker model was asked for while the gate is off")
    monkeypatch.setattr(hr, "_load_reranker", refuse)
    monkeypatch.setattr(hr, "_qwen_rerank", refuse)


@pytest.fixture
def brain(tmp_path):
    b = T.Brain(tmp_path)
    for i in range(14):
        b.add("gradle build cache daemon note %d about memory pressure" % i, project="build")
    for i in range(6):
        b.add("unrelated garden memory %d about tomatoes" % i, project="garden")
    return b


def test_on_cpu_reranker_score_loads_nothing_and_keeps_rrf_order(monkeypatch, brain):
    """RED on f275d09: the CPU route went straight into the Qwen reranker (~80 s a query)."""
    _machine(monkeypatch, "cpu", 23.47)   # the machine is stated, not inherited from the host running the suite
    _no_model_loads(monkeypatch)
    conn = T._connect(brain.path)
    ids = [r[0] for r in conn.execute("SELECT id FROM memories ORDER BY id DESC")]
    out = hr.reranker_score(conn, "gradle build cache", ids, top_k=5)
    assert [m for m, _s in out] == ids[:5]


def test_a_gated_hybrid_recall_is_the_no_rerank_recall_and_says_so(monkeypatch, brain):
    _machine(monkeypatch, "cpu", 23.47)   # the machine is stated, not inherited from the host running the suite
    _no_model_loads(monkeypatch)
    conn = T._connect(brain.path)
    q = "gradle build cache daemon"
    gated = hr.recall_hybrid(conn, q, T.fake_vec(q), top_k=6, mode="hybrid")
    plain = hr.recall_hybrid(conn, q, T.fake_vec(q), top_k=6, mode="no-rerank")
    assert [(r["id"], r["score"]) for r in gated] == [(r["id"], r["score"]) for r in plain] and gated
    assert {r["retrieval_path"] for r in gated} <= {"no-rerank", "bm25-only", "vector-only"}
    assert "hybrid" not in {r["retrieval_path"] for r in gated}


def test_on_an_accelerator_the_reranker_still_runs(monkeypatch, brain):
    """The gate ON leaves the reranker path exactly as it was: Qwen first."""
    _machine(monkeypatch, "cuda", 23.47)
    seen = []
    monkeypatch.setattr(hr, "_qwen_rerank", lambda q, ids, txt, k, ts: seen.append(ids) or [(i, 1.0) for i in ids[:k]])
    monkeypatch.setattr(hr, "_load_reranker", lambda *a, **k: pytest.fail("bge loaded although Qwen answered"))
    conn = T._connect(brain.path)
    q = "gradle build cache daemon"
    res = hr.recall_hybrid(conn, q, T.fake_vec(q), top_k=4, mode="hybrid")
    assert seen and res and {r["retrieval_path"] for r in res} <= {"hybrid", "bm25-only", "vector-only"}


def _cmd_recall(db, ns):
    buf = io.StringIO()
    with mock.patch.object(cm, "DB_PATH", Path(db)), \
            mock.patch.object(cm, "_encode", lambda t, p="search_document": T.fake_vec(t)), \
            contextlib.redirect_stdout(buf):
        cm.cmd_recall(ns)
    return json.loads(buf.getvalue().strip().splitlines()[-1])


def _link_everything(brain):
    """Edges from every memory to every other, so a multi-hop expansion has something to expand into."""
    ids = [r[0] for r in brain.c.execute("SELECT id FROM memories")]
    for a in ids:
        for b in ids:
            if a != b:
                brain.c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, valid_from, "
                                "created_at, extraction_method, metadata) VALUES (?, ?, 'depends_on', 0.9, "
                                "datetime('now'), datetime('now'), 'manual', '{}')", (a, b))
    brain.c.commit()


def test_the_engine_reports_the_gated_mode_and_why(monkeypatch, brain):
    _machine(monkeypatch, "cpu", 23.47)   # the machine is stated, not inherited from the host running the suite
    _link_everything(brain)
    _no_model_loads(monkeypatch)
    ns = T._recall_ns("gradle build cache daemon", top_k=5)
    ns.multi_hop = 2
    out = _cmd_recall(brain.path, ns)
    assert out["status"] == "ok" and out["mode"] == "no-rerank", out.get("mode")
    gate = out["rerank"]["gate"]
    assert gate["enabled"] is False and "CPU-only" in gate["why"] and gate["threshold_gib"] == 15.0
    assert "multi_hop" not in out, "the expansion ran with nothing to rank it"


def test_control_leg_with_the_gate_on_the_expansion_runs(monkeypatch, brain):
    """The other half of the test above: on an accelerator the same recall expands (so its absence above is the
    gate's doing, not the fixture's)."""
    _link_everything(brain)
    _machine(monkeypatch, "cuda", 23.47)
    monkeypatch.setattr(hr, "_qwen_rerank", lambda q, ids, txt, k, ts: [(i, 1.0 / (n + 1)) for n, i in enumerate(ids[:k])])
    ns = T._recall_ns("gradle build cache daemon", top_k=5)
    ns.multi_hop = 2
    out = _cmd_recall(brain.path, ns)
    assert out["mode"] == "hybrid" and "multi_hop" in out and "gate" not in out.get("rerank", {}), out.get("mode")


def test_the_engines_no_rerank_flag_still_wins_on_an_accelerator(monkeypatch, brain):
    _machine(monkeypatch, "cuda", 23.47)
    _no_model_loads(monkeypatch)
    ns = T._recall_ns("gradle build cache daemon", top_k=5)
    ns.no_rerank = True
    assert _cmd_recall(brain.path, ns)["mode"] == "no-rerank"


# ------------------------------------------------------------------------------------------- the CLI and facade
@pytest.mark.parametrize("argv", [["recall", "-q", "x", "--no-rerank"], ["batch-recall", "--items", "[]", "--no-rerank"]])
def test_no_rerank_is_a_flag_on_recall_and_batch_recall(argv):
    from voidloom import cli
    args = cli.build_parser().parse_args(argv)
    assert args.no_rerank is True


def test_no_rerank_switches_this_process_off(monkeypatch):
    from voidloom import cli
    monkeypatch.setenv("VOIDLOOM_RERANKER", "qwen")
    cli._no_rerank_this_process(argparse.Namespace(no_rerank=True))
    assert os.environ["VOIDLOOM_RERANKER"] == "off"
    _machine(monkeypatch, "cuda", 23.47)
    assert hr.reranker_gate()["decided_by"] == "process"


def test_the_facade_threads_no_rerank_to_the_engine():
    from voidloom import _facade
    assert _facade._recall_namespace("q", 5, no_rerank=True).no_rerank is True
    assert _facade._recall_namespace("q", 5).no_rerank is False


# --------------------------------------------------------------------------- doctor, banner, proof label (C4)
def test_doctor_has_an_accelerator_and_reranker_section(monkeypatch, tmp_path):
    from voidloom import cli
    _machine(monkeypatch, "mps", 16.0)
    (tmp_path / "INSTALL_RECORD").write_text("accelerator=mps\nproof_label=PROVEN-BY-PROXY\n")
    monkeypatch.setattr(cli._paths, "brain_dir", lambda override=None: tmp_path)
    monkeypatch.setattr(cli._paths, "organ_dir", lambda override=None: ROOT / "organs")
    d = cli._doctor_reranker()
    assert d["enabled"] is True and d["accelerator"] == "mps"
    assert d["proof_label"] == "PROVEN-BY-PROXY" and d["proof_label_source"] == "INSTALL_RECORD"
    assert "reranker ON" in d["line"] and "PROVEN-BY-PROXY" in d["line"]


def test_doctor_without_a_record_states_the_codes_label_and_the_reason(monkeypatch, tmp_path):
    from voidloom import cli
    _machine(monkeypatch, "cpu", 8.0)
    monkeypatch.setattr(cli._paths, "brain_dir", lambda override=None: tmp_path)
    monkeypatch.setattr(cli._paths, "organ_dir", lambda override=None: ROOT / "organs")
    d = cli._doctor_reranker()
    assert d["enabled"] is False and d["proof_label"] == "PROVEN" and d["proof_label_source"] == "code"
    assert "reranker OFF" in d["line"] and "CPU-only" in d["line"]


def test_on_but_not_cached_is_said(monkeypatch):
    _machine(monkeypatch, "cuda", 23.47)
    monkeypatch.setattr(hr, "_reranker_offline_ok", lambda repo: False)
    st = hr.reranker_status()
    assert st["model_cached"] is False and "not cached" in st["notes"][0]
    assert "not cached" in hr.reranker_banner_line(st)


def test_a_sweep_that_disagrees_with_the_live_machine_is_said(monkeypatch, tmp_path):
    _machine(monkeypatch, "cpu", 8.0)
    cfg = tmp_path / "wizard-config.json"
    cfg.write_text(json.dumps({"reranker_enabled": True, "reranker_gate": {"accelerator": "cuda", "ram_gib": 31.0,
                                                                          "threshold_gib": 15.0}}))
    monkeypatch.setenv("WIZARD_CONFIG_PATH", str(cfg))
    wc.reload_config()
    st = hr.reranker_status()
    assert st["stored_agrees"] is False and any("the live answer decides" in n for n in st["notes"])


def test_the_session_banner_carries_the_reranker_line(monkeypatch):
    monkeypatch.setenv("VOIDLOOM_HOOK_REEXEC", "1")
    monkeypatch.setenv("WIZARD_LIBRARY_HOME", str(ROOT))
    spec = importlib.util.spec_from_file_location("vl_hook_for_gate", str(ROOT / "hooks" / "session_start_inject.py"))
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    _machine(monkeypatch, "cpu", 8.0)
    line = hook._reranker_banner_suffix()
    assert line.startswith("\n") and "reranker OFF" in line and "[PROVEN]" in line


def test_the_hook_prints_the_line_in_the_banner_it_emits(tmp_path):
    """End to end: the SessionStart hook's systemMessage (the owner's terminal) ends with the reranker line. A brain
    that does not exist still gets a banner, so no brain is built; HOME and the heartbeat dir are throwaway."""
    (tmp_path / "home").mkdir()
    env = dict(os.environ, HOME=str(tmp_path / "home"), VOIDLOOM_HEARTBEAT_DIR=str(tmp_path / "hb"),
               WIZARD_LIBRARY_HOME=str(ROOT), WIZARD_LIBRARY_DB=str(tmp_path / "absent.db"), CUDA_VISIBLE_DEVICES="",
               WIZARD_CONFIG_PATH=str(tmp_path / "absent-wizard-config.json"), VOIDLOOM_HOOK_REEXEC="1")
    env.pop("VOIDLOOM_RERANKER", None)
    out = subprocess.run([sys.executable, str(ROOT / "hooks" / "session_start_inject.py")], input="{}", env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    banner = json.loads(out.stdout).get("systemMessage") or ""
    assert banner.splitlines()[-1].startswith("reranker OFF") and "CPU-only" in banner.splitlines()[-1], banner[-400:]


def test_the_status_surfaces_never_import_torch():
    code = ("import sys; sys.path.insert(0, %r); import hybrid_retrieval as h; st = h.reranker_status(); "
            "h.reranker_banner_line(st); print('torch' in sys.modules)" % str(ROOT / "organs"))
    env = dict(os.environ, WIZARD_CONFIG_PATH="/nonexistent/wizard-config.json")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "False", (out.stdout, out.stderr)


# ------------------------------------------------------------------------------ canonical name, logits_to_keep
def test_qwen_is_the_canonical_reranker_everywhere_the_code_names_one(monkeypatch):
    assert hr.CANONICAL_RERANKER_MODEL == qr.QWEN_RERANKER_MODEL_NAME == "Qwen/Qwen3-Reranker-0.6B"
    assert wc.WizardConfig().reranker_model == hr.CANONICAL_RERANKER_MODEL
    assert all(p["reranker_model"] == hr.CANONICAL_RERANKER_MODEL for p in wc._PROFILES.values())
    assert hr.reranker_model_name() in (hr.CANONICAL_RERANKER_MODEL, hr._RERANKER_MODEL_NAME)
    for src in ("hybrid_retrieval.py", "claude_memory.py", "qwen_reranker.py"):
        text = (ROOT / "organs" / src).read_text()
        for stale in ("bge default; Qwen opt-in", "bge CrossEncoder path (the DEFAULT", "RRF + bge-reranker"):
            assert stale not in text, (src, stale)


def test_the_reranker_gate_rides_in_wizard_config(monkeypatch, tmp_path):
    cfg = tmp_path / "wizard-config.json"
    cfg.write_text(json.dumps({"reranker_gate": {"accelerator": "mps", "ram_gib": 16.0, "threshold_gib": 15.0,
                                                 "decided_at": "2026-10-15T00:00:00Z"}}))
    monkeypatch.setenv("WIZARD_CONFIG_PATH", str(cfg))
    assert wc.reload_config().reranker_gate["accelerator"] == "mps"
    monkeypatch.setenv("WIZARD_RERANKER_GATE", '{"accelerator": "cpu"}')
    assert wc.reload_config().reranker_gate == {"accelerator": "cpu"}


class _FakeModel:
    def __init__(self):
        self.kwargs = []

    def forward(self, input_ids=None, attention_mask=None, logits_to_keep=0, **kw):
        return None

    def __call__(self, **kw):
        import torch
        self.kwargs.append(sorted(kw))
        b, n = kw["input_ids"].shape
        keep = kw.get("logits_to_keep") or n
        logits = torch.zeros(b, keep, 8)
        logits[:, -1, 3] = 2.0      # "yes"
        return type("O", (), {"logits": logits})()


class _FakeTok:
    def __call__(self, bodies, **kw):
        return {"input_ids": [[1, 2, 3][: 1 + len(b) % 3] for b in bodies]}

    def pad(self, enc, padding=True, return_tensors="pt"):
        import torch
        n = max(len(x) for x in enc["input_ids"])
        ids = [[0] * (n - len(x)) + x for x in enc["input_ids"]]
        return {"input_ids": torch.tensor(ids), "attention_mask": torch.ones(len(ids), n, dtype=torch.long)}


def test_the_scorer_asks_the_lm_head_for_the_last_position_only():
    m = _FakeModel()
    bundle = {"model": m, "tokenizer": _FakeTok(), "prefix_tokens": [5], "suffix_tokens": [6],
              "token_true_id": 3, "token_false_id": 4, "device": "cpu"}
    scores = qr.score_pairs("q", ["a", "bb", "ccc"], _bundle=bundle)
    assert scores and all(s > 0.5 for s in scores)
    assert m.kwargs and all("logits_to_keep" in k for k in m.kwargs), m.kwargs


def test_logits_to_keep_is_read_from_the_signature():
    class Old:
        def forward(self, input_ids=None, num_logits_to_keep=0, **kw):
            pass

    class Neither:
        def forward(self, input_ids=None, **kw):
            pass
    assert qr.logits_to_keep_kwargs(Old()) == {"num_logits_to_keep": 1}
    assert qr.logits_to_keep_kwargs(Neither()) == {}


def test_the_installed_transformers_qwen3_takes_logits_to_keep():
    """The real-library half: the kwarg exists on the Qwen3ForCausalLM this host resolves (no model is loaded)."""
    tr = pytest.importorskip("transformers")
    cls = getattr(tr, "Qwen3ForCausalLM", None)
    if cls is None:
        pytest.skip("this transformers has no Qwen3ForCausalLM")
    holder = type("H", (), {"forward": cls.forward})()
    assert qr.logits_to_keep_kwargs(holder) in ({"logits_to_keep": 1}, {"num_logits_to_keep": 1})


# --------------------------------------------------------------------------- round 2 (lane R2a): m5, M2, m2, M5
def _env_file(monkeypatch, tmp_path, text):
    f = tmp_path / "wizard-library-env"
    f.write_text(text)
    monkeypatch.setenv("WIZARD_ENV_FILE", str(f))
    return f


@pytest.mark.parametrize("val", ["off", "OFF", "0", "false", "no", "off  # I want speed"])
def test_an_owner_line_added_after_the_install_closes_the_gate_at_recall_time(monkeypatch, tmp_path, val):
    """m5: install.sh read WIZARD_RERANKER once; the recall-time gate ignored a line added later."""
    calls = []
    _machine(monkeypatch, "cuda", 23.47, calls)
    f = _env_file(monkeypatch, tmp_path, "# >>> wizard-library managed >>>\nX=1\n# <<< wizard-library managed <<<\n"
                                         "WIZARD_RERANKER=%s\n" % val)
    g = hr.reranker_gate()
    assert g["enabled"] is False and g["decided_by"] == "owner", g
    assert "WIZARD_RERANKER=" in g["why"] and str(f) in g["why"]
    assert calls == [False], "torch was asked although the owner's line had already switched it off"


def test_an_owner_line_in_the_environment_closes_it_too(monkeypatch):
    _machine(monkeypatch, "cuda", 23.47)
    monkeypatch.setenv("WIZARD_RERANKER", "off")
    g = hr.reranker_gate()
    assert g["enabled"] is False and g["decided_by"] == "owner" and "the environment" in g["why"]


@pytest.mark.parametrize("backend,ram,expect", [("cpu", 64.0, False), ("cuda", 8.0, False), ("cuda", 23.47, True)])
def test_an_owner_on_line_never_opens_the_gate(monkeypatch, tmp_path, backend, ram, expect):
    _machine(monkeypatch, backend, ram)
    _env_file(monkeypatch, tmp_path, "WIZARD_RERANKER=on\n")
    g = hr.reranker_gate()
    assert g["enabled"] is expect and g["decided_by"] != "owner", g


def _on(monkeypatch, osname):
    monkeypatch.setattr(hr.platform, "system", lambda: osname)


@pytest.mark.parametrize("osname,backend,label", [
    ("Linux", "cpu", "PROVEN"), ("Linux", "cuda", "PROVEN"), ("Linux", "rocm", "SUPPORTED-UNPROVEN"),
    ("Darwin", "cpu", "SUPPORTED-UNPROVEN"), ("Darwin", "mps", "SUPPORTED-UNPROVEN"),
    ("Darwin", "cuda", "SUPPORTED-UNPROVEN"), ("Windows", "cpu", "SUPPORTED-UNPROVEN")])
def test_the_codes_label_is_proven_only_where_linux_proved_it(monkeypatch, osname, backend, label):
    """M2 (R2a's call sites): every reader of the code's label is OS-aware."""
    _on(monkeypatch, osname)
    assert hr.code_label(backend) == label


def test_a_mac_banner_never_says_proven(monkeypatch, tmp_path):
    _on(monkeypatch, "Darwin")
    _machine(monkeypatch, "cpu", 16.0)
    assert hr.reranker_banner_line(hr.reranker_status()).endswith("cpu [SUPPORTED-UNPROVEN]")
    rec = tmp_path / "INSTALL_RECORD"
    rec.write_text("accelerator=cpu\nproof_label=\n")          # an Intel Mac's record before m1
    st = hr.reranker_status(install_record=str(rec))
    assert st["proof_label"] == "SUPPORTED-UNPROVEN" and st["proof_label_source"] == "code"


def test_after_an_mps_flip_the_banner_says_mps_failed_not_the_cpu(monkeypatch, tmp_path):
    """m2: the record of a flip holds accelerator=cpu, proof_label=FAILED-ON-THIS-MACHINE, accelerator_failed=mps."""
    _on(monkeypatch, "Darwin")
    _machine(monkeypatch, "cpu", 16.0)
    rec = tmp_path / "INSTALL_RECORD"
    rec.write_text("accelerator=cpu\nproof_label=FAILED-ON-THIS-MACHINE\naccelerator_failed=mps\n")
    line = hr.reranker_banner_line(hr.reranker_status(install_record=str(rec)))
    assert line.endswith("cpu [SUPPORTED-UNPROVEN; mps FAILED-ON-THIS-MACHINE]"), line


def test_an_owner_kept_failed_backend_keeps_its_failed_label(monkeypatch, tmp_path):
    """keep-owner: the owner's WIZARD_ACCELERATOR=mps kept the failed device -- the label IS about the live backend."""
    _on(monkeypatch, "Darwin")
    _machine(monkeypatch, "mps", 16.0)
    rec = tmp_path / "INSTALL_RECORD"
    rec.write_text("accelerator=mps\nproof_label=FAILED-ON-THIS-MACHINE\naccelerator_failed=mps\n")
    assert hr.reranker_status(install_record=str(rec))["proof_label"] == "FAILED-ON-THIS-MACHINE"


def test_a_record_about_another_backend_does_not_label_this_one(monkeypatch, tmp_path):
    _machine(monkeypatch, "cpu", 16.0)
    rec = tmp_path / "INSTALL_RECORD"
    rec.write_text("accelerator=cuda\nproof_label=PROVEN-BY-PROXY\n")
    st = hr.reranker_status(install_record=str(rec))
    assert (st["proof_label"], st["proof_label_source"]) == (hr.code_label("cpu"), "code")


def _doctor_rows(tmp_path, env_extra):
    code = ("import json, sys; from pathlib import Path; sys.path.insert(0, %r); from voidloom import cli; "
            "cli._paths.organ_dir = lambda override=None: Path(%r); cli._paths.brain_dir = lambda override=None: Path(%r); "
            "print(json.dumps({'acc': cli._doctor_accelerator(), 'pin': cli._doctor_gpu_pin(), "
            "'torch': 'torch' in sys.modules}))" % (str(ROOT), str(ROOT / "organs"), str(tmp_path)))
    env = {"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
           "WIZARD_ENV_FILE": str(tmp_path / "absent-env"), "WIZARD_CONFIG_PATH": str(tmp_path / "absent.json")}
    env.update(env_extra)
    out = subprocess.run([sys.executable, "-B", "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_doctor_shows_cpu_only_on_a_cpu_declared_brain(tmp_path):
    """M5 / criterion 2: WIZARD_ACCELERATOR=cpu beside a visible card -> cpu_only True, and the pin row says it is not
    used. A pin of card 1 (the reference host's compute card; nothing here imports torch) is only reported."""
    d = _doctor_rows(tmp_path, {"WIZARD_ACCELERATOR": "cpu",
                                # gpu-pin-gate: a subprocess that never imports torch; the value only has to be a visible card
                                "CUDA_VISIBLE_DEVICES": "1"})
    acc, pin = d["acc"], d["pin"]
    assert acc["backend"] == "cpu" and acc["cpu_only"] is True and acc["cpu_declared"] is True, acc
    assert acc["label"] == hr.code_label("cpu") and "CPU only" in acc["line"], acc
    assert pin.get("not_used", "").startswith("not used: CPU declared"), pin
    assert d["torch"] is False


def test_doctor_without_a_cpu_declaration_has_no_not_used_line(tmp_path):
    d = _doctor_rows(tmp_path, {"CUDA_VISIBLE_DEVICES": ""})
    assert d["acc"]["cpu_declared"] is False and "not_used" not in d["pin"], d
