#!/usr/bin/env python3
"""golden_vectors.py -- the embedder's answer on THIS machine's device, compared with a CPU-fp32 reference (probe P6).

Handoff round 1, lane L4. A backend nothing here has executed on (MPS, ROCm, XPU) can be wrong in ways no fake can
show: an op that falls back, a dtype that drifts, a kernel that returns zeros. The brain would then hold vectors from
a space that is not the one its other vectors live in -- silently, and for good. So before an install embeds anything,
it embeds a fixed set of texts through the package's OWN encode path (organs/claude_memory._encode: the bytes the
brain stores) on the device the organs chose, and compares them with vectors made once on a CPU in fp32:

    per text   cosine(device, reference) >= the fixture's min_cosine
    ranking    every query's top-3 among the documents is the reference's top-3, in order

The fixtures (install/fixtures/golden_vectors_<tier>.json) carry the texts, the reference vectors (rounded to 7
places), how and where they were made, and the tolerance. The tolerance was SET ONCE by the first CUDA-vs-CPU
measurement on the reference host (pre-registered outcome rule 3, SYNTHESIS §5) and is frozen: it is not re-tuned to
make a later run pass. The measured numbers are in the fixture's `calibration`.

    golden_vectors.py generate --organs DIR --tier nomic|qwen --out FILE    (run with CUDA_VISIBLE_DEVICES='': the CPU)
    golden_vectors.py measure  --organs DIR --tier nomic|qwen --fixture FILE   -> one JSON line: the comparison
    golden_vectors.py calibrate --fixture FILE --measured JSON                 (records a measurement + the tolerance)

Imported by install/tier1_probe.py (P6). Stdlib at import time; torch is reached only through the organs' encoder.
"""
from __future__ import print_function

import json
import math
import os
import platform
import struct
import sys

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_DIR = os.path.join(HERE, "fixtures")
DEFAULT_MIN_COSINE = 0.999        # [A3]; replaced by the calibrated value recorded in each fixture
TIERS = ("nomic", "qwen")         # jina is non-commercial and never a default: no fixture, P6 says so

# Fixed texts. Documents are stored with the engine's "search_document" prefix, queries with "search_query" -- the two
# encode paths the brain uses. Chosen so that each query has ONE clearly relevant document and a stable top-3.
DOCUMENTS = [
    "The sleep loop consolidates episodic memories into semantic summaries every night.",
    "Flow cytometry counts cells by passing them one at a time through a laser beam.",
    "A reranker scores each query and passage pair together to order search results.",
    "SQLite stores the whole database in a single file on disk.",
    "Apple Silicon Macs share one pool of unified memory between the CPU and the GPU.",
    "The installer writes the environment file with a managed block between two markers.",
    "Photosynthesis converts light energy into chemical energy stored in glucose.",
    "A golden vector is a reference embedding used to check that a new device computes the same answer.",
]
QUERIES = [
    "how does the brain consolidate memories overnight",
    "what does a reranker do",
    "does the Mac GPU have its own memory",
    "how do plants make sugar from sunlight",
]


def fixture_path(tier):
    return os.path.join(FIXTURE_DIR, "golden_vectors_%s.json" % tier)


def _unpack(b):
    return list(struct.unpack("%df" % (len(b) // 4), b))


def _cos(a, b):
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0


def _top3(qvecs, dvecs):
    out = []
    for q in qvecs:
        sims = sorted(((_cos(q, d), i) for i, d in enumerate(dvecs)), reverse=True)
        out.append([i for _s, i in sims[:3]])
    return out


def encode_all(organs, tier):
    """(doc_vectors, query_vectors, facts) through the package's own encoder, in this process, on the device the
    organs choose. The caller sets WIZARD_EMBED_MODEL=<tier> (and the device env) BEFORE calling."""
    if organs not in sys.path:
        sys.path.insert(0, organs)
    import claude_memory as cm
    if cm._active_embed_model() != tier:
        raise RuntimeError("the package's encoder resolved tier %r, not %r" % (cm._active_embed_model(), tier))
    docs = [_unpack(cm._encode(t, prefix="search_document")) for t in DOCUMENTS]
    queries = [_unpack(cm._encode(t, prefix="search_query")) for t in QUERIES]
    device = None
    try:
        if tier == "qwen":
            import embedding_upgrade as eu
            m = eu._qwen_model_cache.get("model")
        else:
            m = cm._model_cache.get("model")
        device = str(getattr(m, "device", None)) if m is not None else None
    except Exception:  # noqa: BLE001 - the device is a fact to report, never a reason to fail
        device = None
    return docs, queries, {"device": device, "dim": len(docs[0]) if docs else 0}


def compare(docs, queries, fixture):
    """The P6 verdict as facts: per-text cosine to the reference, the worst one, and the top-3 agreement."""
    ref_d, ref_q = fixture["documents"]["vectors"], fixture["queries"]["vectors"]
    tol = (fixture.get("tolerance") or {}).get("min_cosine", DEFAULT_MIN_COSINE)
    if len(docs[0]) != len(ref_d[0]):
        return {"ok": False, "reason": "the device's vectors are %d-wide; the reference is %d-wide"
                % (len(docs[0]), len(ref_d[0])), "min_cosine_required": tol}
    cos = [_cos(a, b) for a, b in zip(docs + queries, ref_d + ref_q)]
    worst = min(cos)
    top_dev, top_ref = _top3(queries, docs), _top3(ref_q, ref_d)
    same = top_dev == top_ref
    finite = all(math.isfinite(x) for v in docs + queries for x in v)
    ok = finite and worst >= tol and same
    reason = None
    if not finite:
        reason = "the device returned a non-finite value (NaN or inf)"
    elif worst < tol:
        reason = "the worst text agrees with the CPU reference at cosine %.6f, under the %.6f required" % (worst, tol)
    elif not same:
        reason = "the device ranks the documents differently from the reference (top-3 %s vs %s)" % (top_dev, top_ref)
    return {"ok": ok, "reason": reason, "min_cosine": round(worst, 7), "min_cosine_required": tol,
            "mean_cosine": round(sum(cos) / len(cos), 7), "top3_identical": same, "texts": len(cos)}


def load_fixture(tier):
    with open(fixture_path(tier), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _versions():
    out = {"python": platform.python_version(), "machine": platform.machine(), "system": platform.system()}
    try:
        import importlib.metadata as md
        for n in ("torch", "sentence-transformers", "transformers"):
            try:
                out[n] = md.version(n)
            except Exception:  # noqa: BLE001
                out[n] = None
    except ImportError:
        pass
    return out


def _r(v):
    return [round(x, 7) for x in v]


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="golden vectors: the embedder on this device vs a CPU-fp32 reference")
    sub = ap.add_subparsers(dest="verb")
    g = sub.add_parser("generate")
    g.add_argument("--organs", required=True)
    g.add_argument("--tier", required=True, choices=TIERS)
    g.add_argument("--out", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--organs", required=True)
    m.add_argument("--tier", required=True, choices=TIERS)
    m.add_argument("--fixture", default="")
    c = sub.add_parser("calibrate")
    c.add_argument("--fixture", required=True)
    c.add_argument("--measured", required=True, help="the JSON line `measure` printed")
    c.add_argument("--min-cosine", type=float, required=True, help="the tolerance this measurement sets, ONCE")
    a = ap.parse_args(argv)
    if a.verb in ("generate", "measure"):
        os.environ["WIZARD_EMBED_MODEL"] = a.tier
    if a.verb == "generate":
        if os.environ.get("CUDA_VISIBLE_DEVICES", None) != "":
            print("generate runs on the CPU only: start it with CUDA_VISIBLE_DEVICES='' (forced)", file=sys.stderr)
            return 2
        docs, queries, facts = encode_all(a.organs, a.tier)
        if facts.get("device") not in (None, "cpu"):
            print("the reference must be made on the CPU; the encoder used %s" % facts["device"], file=sys.stderr)
            return 2
        doc = {"tier": a.tier, "made_on": dict(_versions(), device=facts.get("device") or "cpu", dtype="float32"),
               "how": "install/golden_vectors.py generate, through organs/claude_memory._encode (the bytes the brain "
                      "stores), CUDA_VISIBLE_DEVICES=''",
               "tolerance": {"min_cosine": DEFAULT_MIN_COSINE, "top3_identical": True,
                             "set_by": "default [A3]; replaced once by the first CUDA-vs-CPU measurement"},
               "calibration": [],
               "documents": {"prefix": "search_document", "texts": DOCUMENTS, "vectors": [_r(v) for v in docs]},
               "queries": {"prefix": "search_query", "texts": QUERIES, "vectors": [_r(v) for v in queries]}}
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
            fh.write("\n")
        print(json.dumps({"ok": True, "out": a.out, "dim": facts["dim"], "device": facts.get("device")}))
        return 0
    if a.verb == "measure":
        fx_path = a.fixture or fixture_path(a.tier)
        with open(fx_path, "r", encoding="utf-8") as fh:
            fx = json.load(fh)
        docs, queries, facts = encode_all(a.organs, a.tier)
        r = compare(docs, queries, fx)
        r.update({"device": facts.get("device"), "versions": _versions()})
        print(json.dumps(r, sort_keys=True))
        return 0 if r["ok"] else 1
    if a.verb == "calibrate":
        with open(a.fixture, "r", encoding="utf-8") as fh:
            fx = json.load(fh)
        meas = json.loads(a.measured)
        fx.setdefault("calibration", []).append({k: meas.get(k) for k in ("device", "min_cosine", "mean_cosine",
                                                                         "top3_identical", "versions")})
        fx["tolerance"] = {"min_cosine": a.min_cosine, "top3_identical": True,
                           "set_by": "the first CUDA-vs-CPU measurement on the reference host (outcome rule 3: set "
                                     "once, frozen); see calibration[0]"}
        with open(a.fixture, "w", encoding="utf-8") as fh:
            json.dump(fx, fh, indent=1)
            fh.write("\n")
        print(json.dumps({"ok": True, "tolerance": fx["tolerance"]}))
        return 0
    ap.print_help()
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
