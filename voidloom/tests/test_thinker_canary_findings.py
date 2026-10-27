# -*- coding: utf-8 -*-
"""Round 2 wave 2B (model-choice unit, ruler VL-MC-v1.1): what the LIVE canaries found in the seam, each pinned.

  W2B-01  THE PROOF DECODES DETERMINISTICALLY on an HTTP model server. The proof's form -- "What is N plus 1? Reply with
          only the number." -- was measured exact 5/5 on a 0.5B model at TEMPERATURE 0 (Round 0, P11), but the seam
          sent the server's DEFAULT sampling. Live, through the REAL installer question, answer d) -> an ollama server
          with qwen2.5:0.5b: the model answered '2873+1=2874' and '9196 + 1 = 9197' (0 of 3 exact), so the owner's
          working model FAILED the proof that gates recording it, and the installer refused the answer. The proof's
          calls (thinker_probe / thinker_check / token_doctor) now carry the catalog's `probe_body` -- options.temperature
          0 on /api/chat, temperature 0 on /v1 -- and ONLY they: an organ's call keeps the model's own sampling.

  W2B-02  THE SUM WRITTEN OUT IS AN ANSWER. `ollama run` has no switch for deterministic decoding, and the same model
          through the template answered '1122 + 1 = 1123' (1 of 3 live answer calls, and the installer's own proof:
          refused). The reply carries the nonce, so the echo rule read it as an echo -- yet no echo, cat, tee or fixed
          string can write M. The proof now accepts exactly 'N + 1 = M' / 'N+1=M' / 'N plus 1 equals M' with M the
          answer and nothing else; a wrong M, extra words or the challenge's own text still fail.

The doubles below answer the way the live model did: the equation form under default sampling, the bare number at
temperature 0 -- so the assertion reads the proof's OUTCOME, not only the request's shape.
"""
import json
import re
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
import thinker

SCRATCH = Path.home() / ".crucible_scratch"


class _Sampled(BaseHTTPRequestHandler):
    """A local model server that answers the proof's sum the way qwen2.5:0.5b did live: 'N+1=M' at default sampling,
    'M' at temperature 0; any other prompt gets a plain answer. Every request body is recorded."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        self.server.requests.append({"path": self.path, "body": body})
        prompt = " ".join(str(m.get("content", "")) for m in body.get("messages", []))
        temp = (body.get("options") or {}).get("temperature", body.get("temperature"))
        m = re.search(r"What is (\d+) plus 1\? Reply with only the number\.", prompt)
        if m:
            n = int(m.group(1))
            text = str(n + 1) if temp == 0 else "%d+1=%d" % (n, n + 1)
        else:
            text = "A plain answer from the sampled double, long enough for an organ."
        words = len(prompt.split())
        if self.path == "/api/chat":
            out = {"model": body.get("model"), "message": {"role": "assistant", "content": text}, "done": True,
                   "prompt_eval_count": words + 36}
        else:
            out = {"choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
                   "usage": {"prompt_tokens": words + 36}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_w2b_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


@pytest.fixture
def sampled():
    srv = HTTPServer(("127.0.0.1", 0), _Sampled)
    srv.requests = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.mark.parametrize("api", ["ollama", "openai"])
def test_W2B_01_a_working_local_model_passes_the_proof_and_is_recorded(w, sampled, api):
    srv, url = sampled
    env = thinker.run(["set", "local", "--endpoint", url, "--api", api, "--model", "tiny", "--probe-env", "direct",
                       "--answered-by", "test"])
    assert env["_exit_code"] == 0, (env["_human"], env["data"])
    rec = thinker.read_record()
    assert (rec["backend"], rec["probe_exact"]) == ("local-http-%s" % api, "true"), rec
    probes = [r["body"] for r in srv.requests]
    temps = [(b.get("options") or {}).get("temperature", b.get("temperature")) for b in probes]
    assert temps and all(t == 0 for t in temps), temps


@pytest.mark.parametrize("api", ["ollama", "openai"])
def test_W2B_01_an_organ_call_keeps_the_models_own_sampling(w, sampled, api):
    srv, url = sampled
    assert thinker.run(["set", "local", "--endpoint", url, "--api", api, "--model", "tiny", "--probe-env", "direct",
                        "--answered-by", "test"])["_exit_code"] == 0
    n = len(srv.requests)
    r = thinker.think("Distill these notes into one sentence.", purpose="sleep_loop", timeout_s=30)
    assert r.text.startswith("A plain answer")
    body = srv.requests[n]["body"]
    assert "temperature" not in (body.get("options") or {}) and "temperature" not in body, body
    if api == "ollama":
        assert body["options"]["num_ctx"] == 16384 and body["truncate"] is False, body


def test_W2B_01_the_catalog_carries_probe_body_and_the_loader_refuses_a_bad_one(tmp_path):
    cat = thinker.load_catalog()
    assert cat["backends"]["local-http-ollama"]["probe_body"] == {"options.temperature": 0}
    assert cat["backends"]["local-http-openai"]["probe_body"] == {"temperature": 0}
    bad = json.loads(json.dumps(cat))
    bad["backends"]["local-http-ollama"]["probe_body"] = {"options": {"temperature": 0}}
    p = tmp_path / "thinkers.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(thinker.CatalogError, match="probe_body"):
        thinker.load_catalog(p)


EQUATION_RUNNER = r"""#!%s
# a command-template runner that answers the proof's sum the way `ollama run` + qwen2.5:0.5b did live: written out
import json, re, sys
from pathlib import Path
cfg = json.loads(Path(__file__).with_name("runner.json").read_text())
prompt = sys.argv[-1]
m = re.search(r"What is (\d+) plus 1\? Reply with only the number\.", prompt)
if m:
    n = int(m.group(1))
    print(cfg["form"].format(n=n, m=n + 1 + cfg.get("off", 0)))
else:
    print("done")
"""


@pytest.mark.parametrize("form,off,passes", [
    ("{n} + 1 = {m}", 0, True), ("{n}+1={m}", 0, True), ("{n} plus 1 equals {m}", 0, True), ("{m}", 0, True),
    ("{n} + 1 = {m}", 1, False), ("The answer: {n} + 1 = {m}", 0, False), ("{n}", 0, False),
    ("What is {n} plus 1? Reply with only the number.", 0, False)])
def test_W2B_02_the_sum_written_out_with_exactly_the_answer_passes_and_nothing_looser(w, form, off, passes):
    import os
    import sys
    runner = Path(w.bin) / "ollama"
    runner.write_text(EQUATION_RUNNER % sys.executable)
    os.chmod(str(runner), 0o755)
    (Path(w.bin) / "runner.json").write_text(json.dumps({"form": form, "off": off}))
    cat = thinker.load_catalog()
    cand = thinker.make_candidate(cat, "local", command="ollama run {model} {prompt}", model="tiny")
    res = thinker.probe_candidate(cand, timeout_s=30, cat=cat)
    assert bool(res["passed"]) is passes, res
    if passes and form != "{m}":
        assert res["answer_form"] == "equation", res
