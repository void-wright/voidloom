"""The ONE compute resolver (handoff round 1, lane L1; contracts C1/C2): the accelerator table, by fakes and by torch.

Before this round ten device-choice sites in eight files each wrote `"cuda:0" if _cuda_usable(torch) else "cpu"`, so a
Mac, an AMD card or an Intel GPU could only ever be CPU, silently. organs/voidloom_device.py now owns the answer:

  resolve_accelerator() -> (backend, torch_device, basis, display_shared, ram_bytes)   stdlib, before `import torch`
  torch_device() -> str                                                                 pin first, then torch confirms

WHAT THESE TESTS PROVE, AND WHAT THEY DO NOT. The fakes prove CONTROL FLOW: the backend chosen, the basis, the pin
written, the display rule, the refusals. They prove nothing about kernels, numerics or memory on that silicon -- which
is why MPS, ROCm and XPU carry SUPPORTED-UNPROVEN. The real-torch class proves that every torch attribute the resolver
reads either exists on the torch installed here or is read through the guard that answers None.

No test here loads a model or initialises a GPU: real torch is only ever imported under CUDA_VISIBLE_DEVICES=''.
"""
import ast
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"
if str(ORGANS) not in sys.path:
    sys.path.insert(0, str(ORGANS))

import voidloom_device as vd  # noqa: E402

_CLEAR = ("CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "ZE_AFFINITY_MASK",
          "WIZARD_CUDA_DEVICE", "WIZARD_DISPLAY_INDEX", "WIZARD_ACCELERATOR", "GPU_DEVICE_ORDINAL")


# --------------------------------------------------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------------------------------------------------
def fake_torch(*, cuda=False, hip=None, cuda_version=None, mps=False, xpu=None):
    """A torch-shaped namespace. xpu=None -> the build has NO torch.xpu (pre-2.4 torch); True/False -> is_available."""
    t = types.SimpleNamespace()
    t.version = types.SimpleNamespace(cuda=cuda_version, hip=hip)
    t.cuda = types.SimpleNamespace(is_available=lambda: cuda, device_count=lambda: 1 if cuda else 0)
    t.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps, is_built=lambda: mps))
    if xpu is not None:
        t.xpu = types.SimpleNamespace(is_available=lambda: xpu, device_count=lambda: 1 if xpu else 0)
    return t


class _Exploding:
    """A 'torch' that fails the test the moment anything reads it."""
    def __getattr__(self, name):
        raise AssertionError("torch was consulted (torch.%s) on a path that must not touch it" % name)


@pytest.fixture
def host(tmp_path, monkeypatch):
    """An isolated machine: its own env file, config, sysfs, /dev/kfd, /proc/meminfo, PATH and platform."""
    for k in _CLEAR:
        monkeypatch.delenv(k, raising=False)
    envf = tmp_path / "wizard-library-env"
    cfg = tmp_path / "wizard-config.json"
    monkeypatch.setenv("WIZARD_ENV_FILE", str(envf))
    monkeypatch.setenv("WIZARD_CONFIG_PATH", str(cfg))
    monkeypatch.setenv("VOIDLOOM_ENV_DIR", str(tmp_path))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", "%s:/usr/bin:/bin" % bindir)
    monkeypatch.setattr(vd, "NVIDIA_SMI_DIRS", ())
    drm = tmp_path / "sys_class_drm"
    drm.mkdir()
    monkeypatch.setattr(vd, "SYSFS_DRM", str(drm))
    monkeypatch.setattr(vd, "DEV_KFD", str(tmp_path / "dev_kfd"))
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16303212 kB\nMemFree:  1 kB\n")
    monkeypatch.setattr(vd, "MEMINFO_PATH", str(meminfo))
    plat = {"v": ("Linux", "x86_64")}
    monkeypatch.setattr(vd, "_platform", lambda: plat["v"])
    vd.reset_cache()

    class H:
        path = tmp_path

        def env_file(self, *lines):
            envf.write_text("".join(l + "\n" for l in lines), encoding="utf-8")

        def config(self, data):
            cfg.write_text(json.dumps(data), encoding="utf-8")

        def platform(self, system, machine):
            plat["v"] = (system, machine)

        def nvidia(self, cards):
            """cards: [(index, uuid, display_active)]"""
            idx = "\\n".join(str(c[0]) for c in cards)
            tab = "\\n".join("%d, %s, %s" % c for c in cards)
            smi = bindir / "nvidia-smi"
            smi.write_text(textwrap.dedent("""\
                #!/bin/sh
                case "$*" in
                  *index,uuid,display_active*) printf '%s\\n' ;;
                  *--query-gpu=index*) printf '%s\\n' ;;
                  *) exit 0 ;;
                esac
                """) % (tab, idx), encoding="utf-8")
            os.chmod(str(smi), 0o755)

        def drm_card(self, n, vendor, boot_vga=None):
            d = drm / ("card%d" % n) / "device"
            d.mkdir(parents=True)
            (d / "vendor").write_text(vendor + "\n")
            if boot_vga is not None:
                (d / "boot_vga").write_text("1\n" if boot_vga else "0\n")

        def kfd(self):
            (tmp_path / "dev_kfd").write_text("")

    yield H()
    vd.reset_cache()


def _r(**kw):
    vd.reset_cache()
    return vd.resolve_accelerator(**kw)


# --------------------------------------------------------------------------------------------------------------------
# C1 / C2 shape
# --------------------------------------------------------------------------------------------------------------------
class TestContract:
    def test_c1_tuple_shape_and_c2_values(self, host):
        b, dev, basis, shared, ram = _r()
        assert (b, dev) == ("cpu", "cpu")
        assert isinstance(basis, str) and isinstance(shared, bool)
        assert ram == 16303212 * 1024
        assert vd.ACCELERATOR_VALUES == ("auto", "cuda", "rocm", "mps", "xpu", "cpu")
        assert vd.ACCEL_WRITER_VALUES == ("auto", "cpu")
        assert set(vd.TORCH_DEVICE) == set(vd.BACKENDS)

    def test_stdlib_stage_never_consults_torch_and_never_writes_the_environment(self, host):
        host.nvidia([(0, "GPU-aaaa", "Enabled"), (1, "GPU-bbbb", "Disabled")])
        host.env_file("WIZARD_CUDA_DEVICE=1")
        before = dict(os.environ)
        assert _r()[0] == "cuda"
        assert dict(os.environ) == before

    def test_labels(self):
        assert vd.backend_label("cuda", "Linux") == "PROVEN" and vd.backend_label("cpu", "Linux") == "PROVEN"
        for b in ("mps", "rocm", "xpu"):
            assert vd.backend_label(b, "Linux") == "SUPPORTED-UNPROVEN"


# --------------------------------------------------------------------------------------------------------------------
# NVIDIA (observed, tested): behaviour of the CUDA path is unchanged
# --------------------------------------------------------------------------------------------------------------------
class TestCuda:
    def test_declared_compute_card_beside_the_monitor(self, host):
        host.nvidia([(0, "GPU-aaaa", "Enabled"), (1, "GPU-bbbb", "Disabled")])
        host.env_file("WIZARD_CUDA_DEVICE=1", "WIZARD_DISPLAY_INDEX=0")
        b, dev, basis, shared, _ = _r()
        assert (b, dev, shared) == ("cuda", "cuda:0", False), basis
        assert vd.torch_device(fake_torch(cuda=True, cuda_version="12.4")) == "cuda:0"
        assert os.environ["CUDA_VISIBLE_DEVICES"] == "1"           # the pin landed BEFORE torch was asked

    def test_cpu_wheel_on_an_nvidia_box_is_cpu_and_says_so(self, host):
        host.nvidia([(0, "GPU-aaaa", "Enabled")])
        assert vd.torch_device(fake_torch(cuda=False)) == "cpu"
        assert "unconfirmed" in vd.resolve_accelerator()[2]

    def test_rocm_build_is_not_cuda(self, host):
        host.nvidia([(0, "GPU-aaaa", "Enabled")])
        vd.reset_cache()
        assert vd.torch_device(fake_torch(cuda=True, hip="6.2")) == "cpu"

    def test_empty_ambient_is_cpu_and_torch_is_never_asked(self, host, monkeypatch):
        host.nvidia([(0, "GPU-aaaa", "Enabled"), (1, "GPU-bbbb", "Disabled")])
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
        vd.reset_cache()
        assert vd.torch_device(_Exploding()) == "cpu"

    def test_ambient_display_card_is_refused(self, host, monkeypatch):
        host.nvidia([(0, "GPU-aaaa", "Enabled"), (1, "GPU-bbbb", "Disabled")])
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
        b, dev, basis, _s, _ = _r()
        assert (b, dev) == ("cpu", "cpu") and "refused" in basis


# --------------------------------------------------------------------------------------------------------------------
# The declaration, its layers, conflicts and degradations (C1: 'cpu' on any degraded basis)
# --------------------------------------------------------------------------------------------------------------------
class TestDeclaration:
    def test_declared_cpu_never_touches_torch(self, host):
        host.nvidia([(0, "GPU-aaaa", "Disabled")])
        host.env_file("WIZARD_ACCELERATOR=cpu")
        vd.reset_cache()
        assert vd.torch_device(_Exploding()) == "cpu"
        assert vd.resolve_accelerator()[2].startswith("declared:cpu (env-file)")

    def test_env_file_is_last_wins(self, host):
        host.env_file("WIZARD_ACCELERATOR=cuda", "WIZARD_ACCELERATOR=cpu")
        assert vd.declared_accelerator() == ("cpu", "env-file")

    def test_environment_beats_file_and_file_beats_config(self, host, monkeypatch):
        host.config({"accelerator": "xpu"})
        assert vd.declared_accelerator() == ("xpu", "config-file")
        host.env_file("WIZARD_ACCELERATOR=cpu")
        assert vd.declared_accelerator() == ("cpu", "env-file")
        monkeypatch.setenv("WIZARD_ACCELERATOR", "AUTO")
        assert vd.declared_accelerator() == ("auto", "env")

    def test_empty_is_unset_not_a_value(self, host, monkeypatch):
        monkeypatch.setenv("WIZARD_ACCELERATOR", "")
        assert vd.declared_accelerator() == ("auto", "default")

    def test_unparseable_value_is_degraded_cpu(self, host, monkeypatch):
        host.nvidia([(0, "GPU-aaaa", "Disabled")])
        monkeypatch.setenv("WIZARD_ACCELERATOR", "gpu")
        b, dev, basis, _s, _ = _r()
        assert (b, dev) == ("cpu", "cpu") and basis.startswith("degraded:accelerator:")

    def test_damaged_env_file_is_degraded_cpu(self, host):
        host.nvidia([(0, "GPU-aaaa", "Disabled")])
        (host.path / "wizard-library-env").write_bytes(b"WIZARD_ACCELERATOR=cuda\x00\n")
        b, _d, basis, _s, _ = _r()
        assert b == "cpu" and "degraded:" in basis

    def test_conflict_with_the_no_gpu_ruling(self, host):
        host.nvidia([(0, "GPU-aaaa", "Disabled")])
        host.env_file("WIZARD_ACCELERATOR=cuda", "WIZARD_CUDA_DEVICE=-1")
        b, _d, basis, _s, _ = _r()
        assert b == "cpu" and basis.startswith("degraded:conflict")
        host.env_file("WIZARD_CUDA_DEVICE=-1")
        b, _d, basis, _s, _ = _r()
        assert b == "cpu" and basis.startswith("declared-no-gpu")

    def test_degraded_compute_index_is_cpu_not_card_0(self, host):
        host.nvidia([(0, "GPU-aaaa", "Enabled"), (1, "GPU-bbbb", "Disabled")])
        host.env_file("WIZARD_CUDA_DEVICE=one")
        b, _d, basis, _s, _ = _r()
        assert b == "cpu" and "degraded" in basis


# --------------------------------------------------------------------------------------------------------------------
# AMD / ROCm (SUPPORTED-UNPROVEN): torch.version.hip + torch.cuda; HIP_VISIBLE_DEVICES
# --------------------------------------------------------------------------------------------------------------------
class TestRocm:
    def test_candidate_until_torch_confirms(self, host):
        host.kfd()
        host.drm_card(0, "0x1002", boot_vga=True)
        b, dev, basis, shared, _ = _r()
        assert (b, dev) == ("cpu", "cpu") and "candidate" in basis
        assert vd.accelerator_report()["candidates"] == ["rocm"]
        vd.reset_cache()
        assert vd.torch_device(fake_torch(cuda=True, hip="6.2")) == "cuda:0"
        b, dev, basis, shared, _ = vd.resolve_accelerator()
        assert b == "rocm" and shared is True and "confirmed" in basis
        assert os.environ["HIP_VISIBLE_DEVICES"] == "0"
        assert vd.accelerator_report()["label"] == "SUPPORTED-UNPROVEN"

    def test_cuda_wheel_on_an_amd_box_stays_cpu(self, host):
        host.kfd()
        host.drm_card(0, "0x1002")
        vd.reset_cache()
        assert vd.torch_device(fake_torch(cuda=False, hip=None, xpu=False)) == "cpu"
        assert "HIP_VISIBLE_DEVICES" not in os.environ

    def test_two_devices_nothing_declared_refuses(self, host):
        host.env_file("WIZARD_ACCELERATOR=rocm")
        host.drm_card(0, "0x1002", boot_vga=True)
        host.drm_card(1, "0x1002", boot_vga=False)
        b, _d, basis, _s, _ = _r()
        assert b == "cpu" and "refused" in basis

    def test_two_devices_declared_index_is_pinned(self, host):
        host.env_file("WIZARD_ACCELERATOR=rocm", "WIZARD_CUDA_DEVICE=1", "WIZARD_DISPLAY_INDEX=0")
        host.drm_card(0, "0x1002")
        host.drm_card(1, "0x1002")
        vd.reset_cache()
        assert vd.torch_device(fake_torch(cuda=True, hip="6.2")) == "cuda:0"
        assert os.environ["HIP_VISIBLE_DEVICES"] == "1"
        assert vd.resolve_accelerator()[3] is False                 # compute card is not the declared display

    def test_headless_opt_out(self, host):
        host.env_file("WIZARD_ACCELERATOR=rocm", "WIZARD_DISPLAY_INDEX=none")
        host.drm_card(0, "0x1002")
        host.drm_card(1, "0x1002")
        assert _r()[0] == "rocm"

    def test_empty_cuda_visible_devices_is_cpu_on_rocm_too(self, host, monkeypatch):
        """clr rocdevice.cpp:391 reads an EMPTY value as unset (every card visible); Voidloom's rule is no device."""
        host.env_file("WIZARD_ACCELERATOR=rocm")
        host.drm_card(0, "0x1002")
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
        vd.reset_cache()
        assert vd.torch_device(_Exploding()) == "cpu"

    def test_ambient_hip_pin_is_honoured_not_rewritten(self, host, monkeypatch):
        host.env_file("WIZARD_ACCELERATOR=rocm", "WIZARD_CUDA_DEVICE=0")
        host.drm_card(0, "0x1002")
        host.drm_card(1, "0x1002")
        monkeypatch.setenv("HIP_VISIBLE_DEVICES", "1")
        vd.reset_cache()
        assert vd.torch_device(fake_torch(cuda=True, hip="6.2")) == "cuda:0"
        assert os.environ["HIP_VISIBLE_DEVICES"] == "1"

    def test_ambient_pin_reaching_the_display_is_refused(self, host, monkeypatch):
        host.env_file("WIZARD_ACCELERATOR=rocm", "WIZARD_DISPLAY_INDEX=0")
        host.drm_card(0, "0x1002")
        host.drm_card(1, "0x1002")
        monkeypatch.setenv("HIP_VISIBLE_DEVICES", "0")
        b, _d, basis, _s, _ = _r()
        assert b == "cpu" and "refused" in basis

    def test_rocm_declared_on_a_mac_is_degraded(self, host):
        host.platform("Darwin", "arm64")
        host.env_file("WIZARD_ACCELERATOR=rocm")
        b, _d, basis, _s, _ = _r()
        assert b == "cpu" and "degraded:declared-unavailable" in basis


# --------------------------------------------------------------------------------------------------------------------
# Intel XPU (SUPPORTED-UNPROVEN): torch.xpu (or IPEX); ZE_AFFINITY_MASK
# --------------------------------------------------------------------------------------------------------------------
class TestXpu:
    def test_intel_gpu_with_an_xpu_wheel(self, host):
        host.drm_card(0, "0x8086", boot_vga=True)
        vd.reset_cache()
        assert vd.torch_device(fake_torch(xpu=True)) == "xpu:0"
        assert os.environ["ZE_AFFINITY_MASK"] == "0"
        assert vd.resolve_accelerator()[0] == "xpu"

    def test_intel_igpu_with_a_plain_wheel_is_cpu(self, host):
        """The common laptop: an Intel iGPU in sysfs and a torch that cannot drive it -> CPU, never an XPU claim."""
        host.drm_card(0, "0x8086")
        vd.reset_cache()
        assert vd.torch_device(fake_torch(xpu=False)) == "cpu"
        vd.reset_cache()
        assert vd.torch_device(fake_torch(xpu=None)) == "cpu"      # pre-2.4 torch: no torch.xpu at all

    def test_ambient_affinity_mask_with_a_tile_is_honoured(self, host, monkeypatch):
        host.env_file("WIZARD_ACCELERATOR=xpu")
        host.drm_card(0, "0x8086")
        monkeypatch.setenv("ZE_AFFINITY_MASK", "0.1")
        vd.reset_cache()
        assert vd.torch_device(fake_torch(xpu=True)) == "xpu:0"
        assert os.environ["ZE_AFFINITY_MASK"] == "0.1"

    def test_bad_affinity_mask_is_cpu(self, host, monkeypatch):
        host.env_file("WIZARD_ACCELERATOR=xpu")
        monkeypatch.setenv("ZE_AFFINITY_MASK", "gpu0")
        assert _r()[0] == "cpu"


# --------------------------------------------------------------------------------------------------------------------
# Apple MPS (SUPPORTED-UNPROVEN until the CI probe / the friend's preflight)
# --------------------------------------------------------------------------------------------------------------------
class TestMps:
    def test_apple_silicon(self, host):
        host.platform("Darwin", "arm64")
        b, dev, _basis, shared, _ = _r()
        assert (b, dev, shared) == ("mps", "mps", True)
        assert any("unified memory" in n for n in vd.accelerator_report()["notes"])
        vd.reset_cache()
        assert vd.torch_device(fake_torch(mps=True)) == "mps"

    def test_mps_unavailable_is_cpu(self, host):
        host.platform("Darwin", "arm64")
        vd.reset_cache()
        assert vd.torch_device(fake_torch(mps=False)) == "cpu"

    def test_intel_mac_only_if_torch_confirms(self, host):
        host.platform("Darwin", "x86_64")
        assert _r()[0] == "cpu"
        vd.reset_cache()
        assert vd.torch_device(fake_torch(mps=True)) == "mps"

    def test_mps_declared_on_linux_is_degraded(self, host):
        host.env_file("WIZARD_ACCELERATOR=mps")
        b, _d, basis, _s, _ = _r()
        assert b == "cpu" and "degraded:declared-unavailable" in basis


# --------------------------------------------------------------------------------------------------------------------
# CPU, RAM, DirectML, the guard
# --------------------------------------------------------------------------------------------------------------------
class TestRest:
    def test_no_accelerator_at_all(self, host):
        b, dev, basis, shared, ram = _r()
        assert (b, dev, shared) == ("cpu", "cpu", False) and basis == "auto:no accelerator observed"
        vd.reset_cache()
        assert vd.torch_device(fake_torch(xpu=False)) == "cpu"

    def test_torch_not_importable_is_cpu(self, host, monkeypatch):
        host.platform("Darwin", "arm64")
        monkeypatch.setattr(vd, "_import_torch", lambda _m: None)
        vd.reset_cache()
        assert vd.torch_device() == "cpu"

    def test_ram_unreadable_is_none_or_sysconf_never_a_guess(self, host, monkeypatch):
        monkeypatch.setattr(vd, "MEMINFO_PATH", str(host.path / "absent"))
        got = vd.ram_bytes()
        try:
            want = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError, AttributeError):
            want = None
        assert got == want

    def test_directml_is_a_candidate_row_never_a_backend(self, host):
        dml = types.SimpleNamespace(device_count=lambda: 1, device=lambda: "privateuseone:0")
        row = vd.directml_probe(dml)
        assert row["present"] and row["count"] == 1 and row["device"] == "privateuseone:0"
        assert row["selectable"] is False and row["label"] == "SUPPORTED-UNPROVEN"
        assert vd.directml_probe(types.SimpleNamespace(device=lambda: "privateuseone:0"))["count"] is None
        assert "directml" not in vd.BACKENDS

    def test_empty_cache_follows_the_backend(self, host):
        host.platform("Darwin", "arm64")
        hit = []
        t = fake_torch(mps=True)
        t.mps = types.SimpleNamespace(empty_cache=lambda: hit.append("mps"))
        t.cuda.empty_cache = lambda: hit.append("cuda")
        vd.reset_cache()
        assert vd.empty_cache(t) is True and hit == ["mps"]
        host.platform("Linux", "x86_64")
        vd.reset_cache()
        assert vd.empty_cache(_Exploding()) is False               # CPU: nothing to release, torch not consulted

    def test_out_of_memory_is_recognised_on_every_backend(self):
        for msg in ("CUDA out of memory. Tried to allocate", "MPS backend out of memory (MPS allocated: 1 GB)",
                    "XPU out of memory", "HIP out of memory"):
            assert vd.is_out_of_memory(RuntimeError(msg)), msg
        assert not vd.is_out_of_memory(RuntimeError("shape mismatch"))

    def test_a_fault_inside_the_resolver_is_degraded_cpu(self, host, monkeypatch):
        def boom():
            raise RuntimeError("x")
        monkeypatch.setattr(vd, "_resolve_stdlib", boom)
        vd.reset_cache()
        b, _d, basis, _s, _ = vd.resolve_accelerator()
        assert b == "cpu" and basis.startswith("degraded:resolver-fault")


# --------------------------------------------------------------------------------------------------------------------
# The torch attributes: bound to the source, and to the torch installed here
# --------------------------------------------------------------------------------------------------------------------
_SRC = (ORGANS / "voidloom_device.py").read_text(encoding="utf-8")


class TestTorchAttributes:
    def test_every_torch_read_goes_through_the_guard_and_is_declared(self):
        tree = ast.parse(_SRC)
        used = set()
        direct = []
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_torch_attr":
                    if isinstance(n.args[1], ast.Constant):
                        used.add(n.args[1].value)
                    else:                       # a computed path must come from a declared table
                        assert ast.unparse(n.args[1]) == "path", ast.unparse(n)
                if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                        and n.value.id in ("torch", "torch_mod")):
                    direct.append((fn.name, n.attr, n.lineno))
        # The one sanctioned direct read: R-6's decider, inside its try (test_cuda_no_device_guard finds it by AST).
        assert {(f, a) for f, a, _l in direct} <= {("cuda_available", "cuda")}, direct
        used |= set(vd._EMPTY_CACHE.values())
        assert used <= set(vd.TORCH_ATTRS_READ), used - set(vd.TORCH_ATTRS_READ)
        # The declaration is exact: every read declared, every declared path read (cuda.is_available by R-6's decider).
        assert used | {"cuda.is_available"} == set(vd.TORCH_ATTRS_READ)

    def test_no_setdefault_of_any_pin_variable(self):
        """#15708: setdefault yields to an existing OR EMPTY value. The only setdefault allowed is on the resolver's
        own cache dict."""
        calls = [ast.unparse(n) for n in ast.walk(ast.parse(_SRC))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "setdefault"
                 and not (isinstance(n.func.value, ast.Name) and n.func.value.id == "_accel")]
        assert calls == []

    def test_real_torch_has_every_attribute_or_the_guard_answers_none(self):
        """Run against the torch INSTALLED here, in a child with no GPU visible (nothing initialises)."""
        import importlib.util
        if importlib.util.find_spec("torch") is None:
            pytest.skip("no torch on this interpreter: the guard path is the only one, and the fakes cover it")
        child = textwrap.dedent("""
            import json, sys
            sys.path.insert(0, sys.argv[1])
            import torch, voidloom_device as vd
            out = {"version": torch.__version__, "present": {}, "guarded": {}}
            for path in vd.TORCH_ATTRS_READ:
                cur, ok = torch, True
                for part in path.split("."):
                    if not hasattr(cur, part):
                        ok = False
                        break
                    cur = getattr(cur, part)
                out["present"][path] = ok
                out["guarded"][path] = (vd._torch_attr(torch, path) is None) if not ok else None
            out["backend_ok"] = {b: vd._backend_ok(b, torch)[0] for b in ("cuda", "rocm", "mps", "xpu")}
            vd.reset_cache()
            out["torch_device"] = vd.torch_device(torch)
            print(json.dumps(out))
        """)
        env = dict(os.environ, CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1")
        for k in ("WIZARD_ACCELERATOR", "HIP_VISIBLE_DEVICES", "ZE_AFFINITY_MASK"):
            env.pop(k, None)
        p = subprocess.run([sys.executable, "-c", child, str(ORGANS)], capture_output=True, text=True, env=env,
                           timeout=300, stdin=subprocess.DEVNULL)
        assert p.returncode == 0, p.stderr[-2000:]
        out = json.loads(p.stdout.strip().splitlines()[-1])
        # Present on every torch this package supports (>= 2.0): asserted, not hoped.
        for path in ("version.hip", "cuda.is_available", "backends.mps.is_available", "cuda.empty_cache"):
            assert out["present"][path], "torch %s lacks torch.%s" % (out["version"], path)
        # torch.xpu arrived in 2.4 and torch.mps in 2.0-2.1: wherever one is absent, the guard must answer None.
        for path, ok in out["present"].items():
            if not ok:
                assert out["guarded"][path] is True, path
        assert out["torch_device"] == "cpu"                         # no device visible: CPU, nothing initialised
        assert out["backend_ok"]["cuda"] is False


def test_a_cpu_process_never_imports_torch():
    """`CUDA_VISIBLE_DEVICES=''`: torch_device() answers 'cpu' WITHOUT importing torch (the pin is final)."""
    child = "import sys; sys.path.insert(0, sys.argv[1]); import voidloom_device as vd; " \
            "print(vd.torch_device(), 'torch' in sys.modules)"
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1")
    env.pop("WIZARD_ACCELERATOR", None)
    p = subprocess.run([sys.executable, "-c", child, str(ORGANS)], capture_output=True, text=True, env=env, timeout=120,
                       stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stderr
    if "nvidia-smi" in p.stdout:
        pytest.fail(p.stdout)
    dev, imported = p.stdout.split()
    assert dev == "cpu"
    # On a host whose stdlib stage sees no NVIDIA card the answer is promotable and torch MAY be asked; on a host
    # that lists cards, the empty pin is final and torch must not be imported.
    if vd.nvidia_smi() is not None:
        assert imported == "False"


# --------------------------------------------------------------------------------------------------------------------
# THE CLASS SWEEP: no device is chosen anywhere but the resolver
# --------------------------------------------------------------------------------------------------------------------
# The class, defined so a sweep (not a reviewer's suspicion) bounds it: an expression that CHOOSES a torch device by
# branching to "cpu" -- `X if <cond> else "cpu"` / `"cpu" if <cond> else X` where X is a string literal or a *_DEVICE
# name. f275d09 held ten, in eight files. The four in L2's files move when L2 applies the patch L1 hands it
# (L1_device_sites_organs.patch, in the lane's for_L2 folder), whose companion hunk empties this set.
PENDING_L2 = set()
_SWEPT_DIRS = ("organs", "capability", "install", "hooks", "voidloom", "tools", "cron")


def _device_choices(rel, src=None):
    tree = ast.parse(src if src is not None else (ROOT / rel).read_text(encoding="utf-8"))
    hits = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.IfExp):
            continue
        sides = (n.body, n.orelse)
        has_cpu = any(isinstance(x, ast.Constant) and x.value == "cpu" for x in sides)
        other = [x for x in sides if not (isinstance(x, ast.Constant) and x.value == "cpu")]
        devicey = any((isinstance(x, ast.Constant) and isinstance(x.value, str)
                       and x.value.split(":")[0] in ("cuda", "mps", "xpu", "rocm"))
                      or (isinstance(x, ast.Name) and x.id.upper().endswith("_DEVICE")) for x in other)
        if has_cpu and devicey:
            hits.append((rel, n.lineno))
    return hits


def test_no_device_is_chosen_outside_the_resolver():
    files = sorted(p.relative_to(ROOT).as_posix() for d in _SWEPT_DIRS for p in (ROOT / d).rglob("*.py")
                   if "__pycache__" not in p.parts and not p.relative_to(ROOT).as_posix().startswith("voidloom/tests/"))
    assert len(files) > 50, "the population is too small -- the sweep would pass vacuously"
    found = [h for f in files for h in _device_choices(f)]
    assert {f for f, _l in found} <= PENDING_L2, "a device chosen outside voidloom_device: %s" % found


def test_the_sweep_sees_the_shape_it_forbids():
    """The detector catches every spelling f275d09 used (a planted copy of each turns it red)."""
    for src in ('d = "cuda:0" if ok(t) else "cpu"', 'd = QWEN_RERANKER_DEVICE if ok(t) else "cpu"',
                'd = "cpu" if not ok else "mps"'):
        assert _device_choices("organs/planted.py", src), src
    assert not _device_choices("organs/planted.py", 'd = "fast" if ok else "cpu"')


def test_the_ten_sites_route_through_torch_device():
    for rel, n in (("organs/embedding_upgrade.py", 3), ("organs/model_garden.py", 1), ("organs/website_memory.py", 1),
                   ("organs/whisper_network.py", 1), ("organs/claude_memory.py", 1), ("organs/hybrid_retrieval.py", 1),
                   ("organs/qwen_reranker.py", 1), ("organs/wizard_config.py", 1)):
        s = (ROOT / rel).read_text(encoding="utf-8")
        assert "from voidloom_device import torch_device" in s, rel
        assert s.count("_torch_device(torch)") == n, rel


def test_an_organ_asks_the_resolver_not_cuda(host, monkeypatch):
    """Behaviour, not text: model_garden's resident device on an Apple-Silicon host with a torch whose MPS works is
    'mps' -- before this round it could only be 'cuda:0' or 'cpu'. And the transformers pipelines get that device."""
    import model_garden
    host.platform("Darwin", "arm64")
    monkeypatch.setitem(sys.modules, "torch", fake_torch(mps=True))
    vd.reset_cache()
    spec = types.SimpleNamespace(device="auto")
    assert model_garden._resolve_device(spec) == "mps"
    assert model_garden._resolve_device(types.SimpleNamespace(device="cpu")) == "cpu"
    src = (ORGANS / "model_garden.py").read_text(encoding="utf-8")
    assert 'device.startswith("cuda")' not in src


# --------------------------------------------------------------------------------------------------------------------
# capability/detect.py: the installer's probe asks the same resolver (accelerators[], ram, reranker_fits, tier rows)
# --------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def det(host, monkeypatch):
    sys.path.insert(0, str(ROOT / "capability"))
    import detect
    sys.path.remove(str(ROOT / "capability"))
    state = {"os": "Linux", "arch": "x86_64", "gpu": None}
    monkeypatch.setattr(detect, "probe_platform", lambda: {"os": state["os"], "arch": state["arch"], "wsl2": False})

    def gpu():
        if state["gpu"] is None:
            return {"count": 0, "memory_total": 0, "compute_cap": 0.0, "available": False, "reason": "fake: none"}
        return dict(state["gpu"], available=True, reason="ok")
    monkeypatch.setattr(detect, "probe_gpu", gpu)

    class D:
        mod = detect

        def platform(self, system, machine):
            state["os"], state["arch"] = system, machine
            host.platform(system, machine)

        def nvidia(self, cards, mem=11264):
            host.nvidia(cards)
            state["gpu"] = {"count": len(cards), "memory_total": mem, "compute_cap": 7.5}

        def run(self):
            vd.reset_cache()
            return detect.detect(probe_torch=False)
    return D()


class TestDetect:
    def test_apple_silicon_is_the_mps_tier_on_the_tier0_embedder(self, det, monkeypatch):
        det.platform("Darwin", "arm64")
        monkeypatch.setattr(vd, "ram_bytes", lambda: 16 * (1 << 30))
        p = det.run()
        assert p["tier"] == "MPS" and p["chosen_embed_model"] == "nomic"
        assert p["accelerator"]["backend"] == "mps" and p["accelerator"]["label"] == "PROVEN"
        assert p["reranker_fits"]["fits"] is True and p["reranker_fits"]["threshold_gib"] == 15.0
        assert p["degraded"] is False
        row = [r for r in p["accelerators"] if r["backend"] == "mps"][0]
        assert row["selected"] and row["torch_channel"]["index_url"] is None

    def test_an_8gb_mac_keeps_the_reranker_off(self, det, monkeypatch):
        det.platform("Darwin", "arm64")
        monkeypatch.setattr(vd, "ram_bytes", lambda: 8 * (1 << 30))
        p = det.run()
        assert p["reranker_fits"]["fits"] is False and "< 15.0 GiB" in p["reranker_fits"]["why"]

    def test_force_cpu_on_an_nvidia_box_is_the_cpu_tier(self, det, monkeypatch):
        det.nvidia([(0, "GPU-aaaa", "Enabled"), (1, "GPU-bbbb", "Disabled")])
        host_env = {"WIZARD_CUDA_DEVICE": "1"}
        for k, v in host_env.items():
            monkeypatch.setenv(k, v)
        assert det.run()["tier"] == "GPU-MID"                       # unchanged CUDA behaviour
        monkeypatch.setenv("WIZARD_ACCELERATOR", "cpu")
        p = det.run()
        assert p["tier"] == "CPU" and p["chosen_embed_model"] == "nomic"
        assert p["reranker_fits"]["fits"] is False

    def test_an_amd_candidate_names_its_channel_and_stays_cpu(self, det, host):
        host.kfd()
        host.drm_card(0, "0x1002")
        p = det.run()
        assert p["tier"] == "CPU" and p["accelerator"]["backend"] == "cpu"
        row = [r for r in p["accelerators"] if r["backend"] == "rocm"][0]
        assert row["candidate"] and not row["selected"]
        assert row["torch_channel"]["index_url"] == "https://download.pytorch.org/whl/rocm7.2"
        assert row["label"] == "SUPPORTED-UNPROVEN"

    def test_declared_rocm_and_xpu_tiers(self, det, host):
        host.drm_card(0, "0x1002")
        host.env_file("WIZARD_ACCELERATOR=rocm")
        p = det.run()
        assert (p["tier"], p["chosen_embed_model"]) == ("ROCM", "nomic")
        assert p["reranker_fits"]["fits"] is True                   # fixture RAM: 15.55 GiB >= 15.0
        host.env_file("WIZARD_ACCELERATOR=xpu")
        p = det.run()
        assert p["tier"] == "XPU" and p["accelerator"]["torch_device"] == "xpu:0"

    def test_directml_is_listed_on_windows_and_never_selectable(self, det):
        rows = det.mod.accelerator_rows({"backend": "cpu", "candidates": []}, "Windows")
        dml = [r for r in rows if r["backend"] == "directml"]
        assert dml and dml[0]["selectable"] is False and dml[0]["torch_channel"]["package"] == "torch-directml"

    def test_every_shape_carries_the_new_keys(self, det):
        for p in (det.run(), det.mod._emergency_floor("x")):
            for k in ("accelerator", "accelerators", "ram_bytes", "reranker_fits"):
                assert k in p, k
        blk = det.mod.capability_block(det.run())
        for k in ("accelerator", "accelerators", "ram_bytes", "reranker_fits"):
            assert k in blk, k

    def test_a_missing_resolver_vetoes_nothing(self, det, monkeypatch):
        det.nvidia([(0, "GPU-aaaa", "Disabled")])
        monkeypatch.setattr(det.mod, "_device_module", lambda: (_ for _ in ()).throw(ImportError("gone")))
        p = det.run()
        assert p["accelerator"]["resolver_ok"] is False and p["tier"] == "GPU-MID"


# --------------------------------------------------------------------------------------------------------------------
# tools/gpu_pin_gate.py: a literal pin in ANY backend's visibility variable is one workstation's answer, shipped
# --------------------------------------------------------------------------------------------------------------------
_PLANTED = {
    "a.py": 'import os\nos.environ["HIP_VISIBLE_DEVICES"] = "1"\n',
    "b.service": "[Service]\nEnvironment=ZE_AFFINITY_MASK=1\n",
    "c.sh": "export ROCR_VISIBLE_DEVICES=2\n",
    "d.plist": "<dict><key>HIP_VISIBLE_DEVICES</key><string>1</string></dict>\n",
    "e.md": "run `HIP_VISIBLE_DEVICES=1 voidloom`\n",
    "f.ps1": '$env:ZE_AFFINITY_MASK = "1"\n',
}


def _gate(root, *extra):
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "gpu_pin_gate.py"), "--dir", str(root), "--json", *extra],
                       capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    return p.returncode, json.loads(p.stdout)


def test_the_pin_gate_refuses_every_backends_pin(tmp_path):
    for name, body in _PLANTED.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    rc, rep = _gate(tmp_path)
    assert rc == 1
    assert sorted(rep["detail"]) == sorted(_PLANTED), rep["detail"]


def test_the_pin_gate_passes_device_zero_on_every_backend(tmp_path):
    (tmp_path / "ok.sh").write_text("export ZE_AFFINITY_MASK=0.1\nexport HIP_VISIBLE_DEVICES=0\n"
                                    "export ROCR_VISIBLE_DEVICES=\"${DEV}\"\n", encoding="utf-8")
    rc, rep = _gate(tmp_path)
    assert rc == 0 and rep["detail"] == {}, rep["detail"]


def test_the_resolvers_new_emitters_are_inventoried_and_covered():
    rc, rep = _gate(ROOT, "--emitters")
    assert rc == 0, rep["detail"]
    mine = [e for e in rep["emitter_details"] if e["file"] == "organs/voidloom_device.py"]
    keys = {e["source"] for e in mine}
    assert any("HIP_VISIBLE_DEVICES" in k for k in keys) and any("ZE_AFFINITY_MASK" in k for k in keys), keys
    assert all(e["covered"] for e in mine)


# --------------------------------------------------------------------------------------------------------------------
# Round 2, lane R2b: M1 (the hidden-card class), M2 (OS-aware labels), M3 (a CPU declaration hides every card)
# --------------------------------------------------------------------------------------------------------------------
_HIDDEN = ("", "-1", "none", "NoDevFiles", "-1,1", "7")      # '7': an index nvidia-smi does not list (measured rc 139)
_SHOWN = ("1", "0", "1,-1", "GPU-bbbb")


class TestRound2R2b:
    # ---- M2 ---------------------------------------------------------------------------------------------------------
    def test_backend_label_follows_the_evidence(self):
        """PROVEN where a real machine ran it: Linux cuda + cpu (the reference host), an Apple Silicon Mac's mps + cpu
        (installed and used end to end, owner-witnessed, 2026-10). Everything else -- an Intel Mac, Windows, an unknown
        OS or architecture -- stays SUPPORTED-UNPROVEN."""
        for osname in ("Windows", "unknown"):
            for b in vd.BACKENDS:
                assert vd.backend_label(b, osname) == "SUPPORTED-UNPROVEN", (b, osname)
        assert [vd.backend_label(b, "Linux") for b in ("cuda", "cpu")] == ["PROVEN", "PROVEN"]
        assert vd.backend_label("mps", "Darwin", "arm64") == "PROVEN"
        assert vd.backend_label("cpu", "Darwin", "arm64") == "PROVEN"
        assert vd.backend_label("cpu", "Darwin", "aarch64") == "PROVEN"
        assert vd.backend_label("cpu", "Darwin", "x86_64") == "SUPPORTED-UNPROVEN"      # an Intel Mac: never run
        assert vd.backend_label("cpu", "Darwin", "") == "SUPPORTED-UNPROVEN"            # unknown chip: claim nothing
        assert vd.backend_label("mps", "Darwin", "x86_64") == "SUPPORTED-UNPROVEN"      # an Intel Mac's MPS: never run
        assert vd.backend_label("mps", "Darwin", "") == "SUPPORTED-UNPROVEN"
        for b in ("cuda", "rocm", "xpu"):
            assert vd.backend_label(b, "Darwin", "arm64") == "SUPPORTED-UNPROVEN", b

    def test_the_code_labels_agree_with_the_installers_proof_table(self):
        sys.path.insert(0, str(ROOT / "install"))
        try:
            import accelerator_plan as ap
        finally:
            sys.path.remove(str(ROOT / "install"))
        for b in vd.BACKENDS:
            for osname in ("Linux", "Darwin", "Windows"):
                assert vd.backend_label(b, osname) == ap.proof_label(b, osname), (b, osname)
                for arch in ("arm64", "x86_64"):
                    assert vd.backend_label(b, osname, arch) == ap.proof_label(b, osname, arch), (b, osname, arch)
        assert set(vd.PROOF_LABEL_WORDS) == set(ap.LABELS)

    @pytest.mark.parametrize("machine,label", [("arm64", "PROVEN"), ("x86_64", "SUPPORTED-UNPROVEN")])
    def test_a_macs_cpu_route_reads_its_chips_evidence(self, host, monkeypatch, machine, label):
        host.platform("Darwin", machine)
        monkeypatch.setenv("WIZARD_ACCELERATOR", "cpu")
        rep = vd.accelerator_report()
        assert rep["backend"] == "cpu" and rep["label"] == label
        host.platform("Linux", "x86_64")
        vd.reset_cache()
        assert vd.accelerator_report()["label"] == "PROVEN"

    def test_proof_label_for_honours_only_a_record_for_the_same_backend(self, host):
        host.platform("Darwin", "x86_64")
        assert vd.proof_label_for("cpu", "", None) == "SUPPORTED-UNPROVEN"                 # an Intel Mac's empty label
        assert vd.proof_label_for("cpu", "  ", "cpu") == "SUPPORTED-UNPROVEN"
        assert vd.proof_label_for("cpu", "FAILED-ON-THIS-MACHINE", "mps") == "SUPPORTED-UNPROVEN"   # after the flip
        assert vd.proof_label_for("mps", "FAILED-ON-THIS-MACHINE", "mps") == "FAILED-ON-THIS-MACHINE"
        assert vd.proof_label_for("mps", "PROVEN-BY-PROXY", None) == "PROVEN-BY-PROXY"
        # the installer's flip record: accelerator=cpu, proof_label=FAILED-ON-THIS-MACHINE, accelerator_failed=mps
        assert vd.proof_label_for("cpu", "FAILED-ON-THIS-MACHINE", "cpu", failed_backend="mps") == "SUPPORTED-UNPROVEN"
        assert vd.proof_label_for("mps", "FAILED-ON-THIS-MACHINE", "cpu", failed_backend="mps") == "FAILED-ON-THIS-MACHINE"
        assert vd.proof_label_for("cpu", "proven!", "cpu") == "SUPPORTED-UNPROVEN"           # not a C4 word
        host.platform("Linux", "x86_64")
        assert vd.proof_label_for("cpu", None, None) == "PROVEN"
        assert vd.proof_label_for("cuda", "", "cuda", osname="Darwin") == "SUPPORTED-UNPROVEN"

    def test_detect_rows_and_floors_claim_nothing_off_linux(self, det, monkeypatch):
        rows = det.mod.accelerator_rows({"backend": "cpu", "candidates": []}, "Darwin")
        # no chip named: nothing can claim the Apple Silicon evidence
        assert {r["backend"]: r["label"] for r in rows} == {b: "SUPPORTED-UNPROVEN" for b in vd.BACKENDS}
        rows = {r["backend"]: r["label"] for r in det.mod.accelerator_rows({"backend": "cpu"}, "Linux")}
        assert rows["cuda"] == rows["cpu"] == "PROVEN" and rows["mps"] == "SUPPORTED-UNPROVEN"
        assert det.mod._emergency_floor("x")["accelerator"]["label"] == "SUPPORTED-UNPROVEN"
        det.platform("Darwin", "x86_64")
        monkeypatch.setenv("WIZARD_ACCELERATOR", "cpu")
        p = det.run()
        assert p["accelerator"]["label"] == "SUPPORTED-UNPROVEN"
        assert all(r["label"] == "SUPPORTED-UNPROVEN" for r in p["accelerators"])

    def test_detect_with_no_resolver_claims_nothing(self, det, monkeypatch):
        def boom():
            raise ImportError("no resolver")
        monkeypatch.setattr(det.mod, "_device_module", boom)
        assert det.mod.probe_accelerator()["label"] == "SUPPORTED-UNPROVEN"

    # ---- M1 ---------------------------------------------------------------------------------------------------------
    def test_the_hidden_class_is_the_resolvers_no_device_class(self, host):
        host.nvidia([(0, "GPU-aaaa", "Enabled"), (1, "GPU-bbbb", "Disabled")])
        assert vd.hides_every_cuda_card({}) is False                                         # unset: enumerates
        for v in _HIDDEN + _SHOWN:
            env = {"CUDA_VISIBLE_DEVICES": v}
            assert vd.hides_every_cuda_card(env) is (v in _HIDDEN), v
            assert vd.hides_every_cuda_card(env) is (not vd.cuda_selects_a_device(env)[0]), v

    @pytest.mark.parametrize("hidden", _HIDDEN)
    def test_the_pin_sets_the_nvml_check_for_every_hidden_spelling(self, host, monkeypatch, hidden):
        host.nvidia([(0, "GPU-aaaa", "Enabled"), (1, "GPU-bbbb", "Disabled")])
        monkeypatch.delenv("PYTORCH_NVML_BASED_CUDA_CHECK", raising=False)
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", hidden)
        vd.apply_cuda_pin()
        assert os.environ.get("PYTORCH_NVML_BASED_CUDA_CHECK") == "1", hidden

    def test_tier1_probe_child_env_uses_the_same_class(self, host, monkeypatch):
        host.nvidia([(0, "GPU-aaaa", "Enabled"), (1, "GPU-bbbb", "Disabled")])
        monkeypatch.delenv("PYTORCH_NVML_BASED_CUDA_CHECK", raising=False)
        sys.path.insert(0, str(ROOT / "install"))
        try:
            import tier1_probe as tp
        finally:
            sys.path.remove(str(ROOT / "install"))
        def args(pin, force_cpu=False):
            return types.SimpleNamespace(force_cpu=force_cpu, pin=pin, backend="", db="", organs=str(ORGANS))
        for v in _HIDDEN:
            if v == "":
                continue                                    # '' is the "no NVIDIA pin" shape, below
            assert tp._child_env(args(v), "P1").get("PYTORCH_NVML_BASED_CUDA_CHECK") == "1", v
        for v in _SHOWN:
            assert tp._child_env(args(v), "P1").get("PYTORCH_NVML_BASED_CUDA_CHECK") is None, v
        assert tp._child_env(args("", force_cpu=True), "P1")["PYTORCH_NVML_BASED_CUDA_CHECK"] == "1"
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "none")             # inherited by a pin-less P6 child
        env = tp._child_env(types.SimpleNamespace(force_cpu=False, pin="", backend="", db="", organs=str(ORGANS),
                                                  tier="nomic"), "P6")
        assert env["CUDA_VISIBLE_DEVICES"] == "none" and env["PYTORCH_NVML_BASED_CUDA_CHECK"] == "1"
        # gpu-pin-gate: a "no device" spelling handed to a predicate, never to a process; it hides every card
        assert tp._hides_every_card({"CUDA_VISIBLE_DEVICES": "none"}, str(ORGANS)) is True
        # gpu-pin-gate: a "no device" spelling handed to a predicate (fallback path), never to a process
        assert tp._hides_every_card({"CUDA_VISIBLE_DEVICES": "NoDevFiles"}, "/nonexistent-organs") is True
        assert tp._hides_every_card({}, str(ORGANS)) is False

    # ---- M3 ---------------------------------------------------------------------------------------------------------
    @pytest.mark.parametrize("where", ["env", "env-file", "config-file"])
    def test_a_cpu_declaration_hides_every_card(self, host, monkeypatch, where):
        host.nvidia([(0, "GPU-aaaa", "Enabled"), (1, "GPU-bbbb", "Disabled")])
        monkeypatch.delenv("PYTORCH_NVML_BASED_CUDA_CHECK", raising=False)
        # gpu-pin-gate: an in-process test of the resolver on a FAKE nvidia-smi; no torch, no card is touched
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
        if where == "env":
            monkeypatch.setenv("WIZARD_ACCELERATOR", "cpu")
        elif where == "env-file":
            host.env_file("WIZARD_ACCELERATOR=cpu")
        else:
            host.config({"accelerator": "cpu"})
        assert vd.cpu_declaration().startswith("declared:cpu")
        vd.apply_accelerator_pin()
        assert os.environ["CUDA_VISIBLE_DEVICES"] == ""
        assert os.environ.get("PYTORCH_NVML_BASED_CUDA_CHECK") == "1"
        assert vd.torch_device(torch_mod=_Exploding()) == "cpu"

    def test_a_degraded_declaration_does_not_take_the_card(self, host, monkeypatch):
        """The boundary, pinned: only a DECLARED cpu hides the cards; a degraded one stays a reported basis."""
        host.nvidia([(0, "GPU-aaaa", "Enabled"), (1, "GPU-bbbb", "Disabled")])
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")  # gpu-pin-gate: fake nvidia-smi, in-process, no torch
        monkeypatch.setenv("WIZARD_ACCELERATOR", "quantum")
        assert vd.declared_accelerator()[0] is None and vd.cpu_declaration() is None
        vd.apply_cuda_pin()
        assert os.environ["CUDA_VISIBLE_DEVICES"] == "1"

    @pytest.mark.parametrize("decl", [None, "auto", "cuda"])
    def test_a_gpu_route_is_untouched(self, host, monkeypatch, decl):
        host.nvidia([(0, "GPU-aaaa", "Enabled"), (1, "GPU-bbbb", "Disabled")])
        monkeypatch.delenv("PYTORCH_NVML_BASED_CUDA_CHECK", raising=False)
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")  # gpu-pin-gate: fake nvidia-smi, in-process, no torch
        if decl:
            monkeypatch.setenv("WIZARD_ACCELERATOR", decl)
        assert vd.cpu_declaration() is None
        assert vd.apply_cuda_pin() == ("1", "ambient")
        assert os.environ["CUDA_VISIBLE_DEVICES"] == "1"
        assert "PYTORCH_NVML_BASED_CUDA_CHECK" not in os.environ
