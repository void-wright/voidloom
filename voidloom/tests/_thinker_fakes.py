# -*- coding: utf-8 -*-
"""Test doubles for the thinker seam: fake vendor CLIs and a fake local model server.

Nothing here reaches a network, a real CLI or a real credential. Every double speaks the TRANSPORT SHAPE Round 0
measured (model-choice round0/facts.json): claude prints one JSON envelope with the answer at .result and says a
failure on STDOUT with rc 1; codex prints the answer alone on stdout and its transcript on stderr; kimi prints
stream-json with role=assistant lines (and a text mode that prefixes '• '); ollama's /api/chat reports
prompt_eval_count and truncates silently past num_ctx unless truncate:false.

A double is a small python script whose behaviour is read from a JSON file BESIDE it (<name>.json), because the
seam hands a child a MINIMAL environment -- an env var set by a test would never arrive, which is the point.

Behaviour keys (all optional):
  mode        answer | fail | write | hang | doctrine | yes | flood   (default answer; yes = exec /usr/bin/yes,
              flood = write `bytes` bytes (default 200 MB) and exit -- Round 2 wave 1, MC-R1-12)
  reply       the answer when the prompt carries no "exactly the word X" and no probe challenge ("What is N plus 1?
              Reply with only the number." -- the double answers N+1, as a model would)
  said        what a failure prints (on stdout for claude, stderr for codex/kimi) ; rc (default 1)
  good_key    kimi/claude: the only credential value that answers; any other -> an auth failure
  log         a file OUTSIDE the call's cwd to append one JSON line per invocation (argv, cwd, env, stdin)
  sleep       seconds to sleep before answering (mode answer) -- for the lock-serialization test
"""
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

FAKE_SRC = r'''
import json, os, re, sys, time
from pathlib import Path
KIND = %(kind)r
cfg_path = Path(__file__).with_name(Path(__file__).name + ".json")
cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
argv = sys.argv[1:]
prompt = argv[-1] if argv else ""
if KIND == "kimi" and "-p" in argv:
    prompt = argv[argv.index("-p") + 1]
if KIND == "ollama-run":
    prompt = argv[-1]
try:
    _st0, _dn = os.fstat(0), os.stat("/dev/null")
    stdin_is_devnull = (_st0.st_rdev == _dn.st_rdev and _st0.st_ino == _dn.st_ino)
except Exception:
    stdin_is_devnull = None
try:
    stdin_tty = os.isatty(0)
    stdin_data = "" if stdin_tty else sys.stdin.read()
except Exception:
    stdin_tty, stdin_data = None, None
agent = None
if "--agent-file" in argv:
    try:
        agent = Path(argv[argv.index("--agent-file") + 1]).read_text()
    except OSError:
        agent = None
cwd_listing = sorted(os.listdir(os.getcwd()))
vendor_home = {}
for _k in ("KIMI_CODE_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
    _d = os.environ.get(_k)
    if _d and os.path.isdir(_d):
        vendor_home[_k] = {"listing": sorted(os.listdir(_d)),
                           "config": (Path(_d) / "config.toml").read_text() if (Path(_d) / "config.toml").is_file() else None,
                           "agents_md": (Path(_d) / "AGENTS.md").is_file()}
def _log(extra):
    if cfg.get("log"):
        with open(cfg["log"], "a") as fh:
            fh.write(json.dumps(extra) + "\n")
_log({"kind": KIND, "argv": argv, "cwd": os.getcwd(), "cwd_listing_at_start": cwd_listing, "env": dict(os.environ),
      "stdin_tty": stdin_tty, "stdin_len": None if stdin_data is None else len(stdin_data), "agent_file": agent,
      "stdin_is_devnull": stdin_is_devnull,
      "vendor_home": vendor_home,
      "pid": os.getpid(), "pgid": os.getpgid(0), "t0": time.time()})
import atexit
atexit.register(lambda: _log({"kind": KIND, "end": True, "pid": os.getpid(), "t1": time.time()}))
if KIND == "codex" and argv[:1] == ["login"]:
    ch = Path(os.environ.get("CODEX_HOME", "."))
    if argv[1:2] == ["status"]:
        if (ch / "auth.json").is_file():
            print("Logged in using ChatGPT")
            sys.exit(0)
        print("Not logged in")
        sys.exit(1)
    if cfg.get("login_fails"):
        sys.stderr.write("device code sign-in is not enabled (fake)\n")
        sys.exit(1)
    print("Open https://example.invalid/device and enter the code FAKE-CODE (fake)")
    (ch / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": "fake-test-only"}))
    os.chmod(str(ch / "auth.json"), 0o600)
    sys.exit(0)
mode = cfg.get("mode", "answer")
m = re.search(r"exactly the word (\S+?)[ .]", prompt + " ")
word = m.group(1) if m else None
m = re.search(r"What is (\d+) plus 1\? Reply with only the number\.", prompt)
if m:                       # the probe's challenge (Round 1 iteration 2): a model computes it; an echo cannot
    word = str(int(m.group(1)) + 1)

def out_answer(text):
    if KIND == "claude":
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                          "session_id": "fake", "usage": {"input_tokens": 1}}))
    elif KIND == "codex":
        sys.stderr.write("OpenAI Codex (fake)\n--------\nworkdir: %%s\nmodel: fake\nsandbox: read-only\n--------\nuser\n%%s\ncodex\n%%s\ntokens used\n12\n" %% (os.getcwd(), prompt[:200], text))
        print(text)
    elif KIND == "kimi":
        if "--output-format" in argv and argv[argv.index("--output-format") + 1] == "stream-json":
            print(json.dumps({"role": "meta", "type": "system.version", "version": "0.0-fake"}))
            print(json.dumps({"role": "assistant", "content": text}))
            print(json.dumps({"role": "meta", "type": "session.resume_hint", "content": "To resume this session: kimi -r fake"}))
        else:
            print("• " + text + "\n")
            sys.stderr.write("kimi 0.0-fake\nTo resume this session: kimi -r fake\n")
    else:
        print(text + "\n")

def out_fail(said, rc):
    if KIND == "claude":
        print(said)
    else:
        sys.stderr.write(said + "\n")
    sys.exit(rc)

good = cfg.get("good_key")
if good is not None:
    have = os.environ.get("KIMI_MODEL_API_KEY") if KIND == "kimi" else os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if have != good:
        out_fail("error: provider.auth_error: 401 The API Key appears to be invalid or may have expired (fake)", 1)
if mode == "yes":
    # Round 2 wave 1 (MC-R1-12): a `yes`-class program -- the real /usr/bin/yes, writing forever as fast as it can
    os.execv(cfg.get("yes", "/usr/bin/yes"), ["yes", prompt[:40] or "y"])
if mode == "flood":
    # a finite writer: `bytes` bytes on stdout (default 200 MB) in 1 MB writes, then exit 0 -- it never holds them
    _left, _blk = int(cfg.get("bytes", 200 * 1024 * 1024)), b"x" * (1024 * 1024)
    try:
        while _left > 0:
            os.write(1, _blk[:min(_left, len(_blk))])
            _left -= len(_blk)
    except OSError:
        pass
    os._exit(0)
if mode == "fail":
    out_fail(cfg.get("said", "fake failure"), int(cfg.get("rc", 1)))
if mode == "hang":
    time.sleep(3600)
if mode == "write":
    Path("tool-was-used.txt").write_text("a tool ran\n")
if mode == "doctrine":
    # EMULATES the ambient-instruction channels a real CLI honours unless told not to (Round 0 C01/C02, #21972):
    # the ancestor CLAUDE.md walk from the cwd, the user's CLAUDE.md, ~/.agents/AGENTS.md, and a SessionStart hook.
    safe = "--safe-mode" in argv
    zebra = False
    if not safe and KIND == "claude":
        d = Path(os.getcwd())
        for p in [d] + list(d.parents):
            f = p / "CLAUDE.md"
            if f.is_file() and "ZEBRA" in f.read_text(errors="replace"):
                zebra = True
        cfgdir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path(os.environ.get("HOME", "/")) / ".claude"))
        for f in (cfgdir / "CLAUDE.md", Path(os.environ.get("HOME", "/")) / ".agents" / "AGENTS.md"):
            if f.is_file() and "ZEBRA" in f.read_text(errors="replace"):
                zebra = True
        s = cfgdir / "settings.json"
        if s.is_file():
            for h in (json.loads(s.read_text()).get("hooks", {}).get("SessionStart") or []):
                for hh in h.get("hooks", []):
                    os.system(hh.get("command", "true"))
    if KIND == "codex" and "hooks" not in argv:
        ch = Path(os.environ.get("CODEX_HOME") or (Path(os.environ.get("HOME", "/")) / ".codex"))
        if (ch / "AGENTS.md").is_file() and "ZEBRA" in (ch / "AGENTS.md").read_text(errors="replace"):
            zebra = True
    reply = word or cfg.get("reply", "an answer")
    out_answer(("ZEBRA " if zebra else "") + reply)
    sys.exit(0)
if cfg.get("touch"):
    with open(cfg["touch"], "a") as fh:     # emulates a backend that refreshes (rewrites) a login file
        fh.write("refreshed\n")
if cfg.get("sleep"):
    time.sleep(float(cfg["sleep"]))
out_answer(word if word else cfg.get("reply", "an answer from the fake backend"))
'''


def make_fake(bindir, name, kind=None, **behaviour):
    """Write an executable fake `name` into bindir (kind: claude|codex|kimi|ollama-run; default = name).
    Returns its absolute path. Re-calling with new behaviour rewrites only the JSON beside it."""
    bindir = Path(bindir)
    bindir.mkdir(parents=True, exist_ok=True)
    p = bindir / name
    p.write_text("#!%s\n%s" % (sys.executable, FAKE_SRC % {"kind": kind or name}), encoding="utf-8")
    os.chmod(str(p), 0o755)
    set_behaviour(p, **behaviour)
    return str(p)


def set_behaviour(fake_path, **behaviour):
    Path(str(fake_path) + ".json").write_text(json.dumps(behaviour), encoding="utf-8")


def read_log(path):
    try:
        return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    except OSError:
        return []


class _Handler(BaseHTTPRequestHandler):
    server_version = "fake-ollama/0"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        self.server.requests.append({"path": self.path, "body": body})
        cfg = self.server.cfg
        words = sum(len(str(m.get("content", "")).split()) for m in body.get("messages", []))
        ctx = int((body.get("options") or {}).get("num_ctx") or cfg.get("default_ctx", 4096))
        evaluated = words + 36
        if evaluated > ctx:
            if body.get("truncate") is False and self.path == "/api/chat":
                return self._send(400, {"error": "input length exceeds the context length",
                                        "type": "exceed_context_size_error"})
            evaluated = cfg.get("truncated_to", 2050)
        if cfg.get("status"):
            return self._send(int(cfg["status"]), {"error": cfg.get("error", "fake")})
        text = cfg.get("reply", "a local answer")
        import re
        prompt = " ".join(str(m.get("content", "")) for m in body.get("messages", []))
        m = re.search(r"exactly the word (\S+?)[ .]", prompt + " ")
        if m:
            text = m.group(1)
        m = re.search(r"What is (\d+) plus 1\? Reply with only the number\.", prompt)
        if m:
            text = str(int(m.group(1)) + 1)
        if cfg.get("omit_count"):
            evaluated = None
        if self.path == "/api/chat":
            out = {"model": body.get("model"), "message": {"role": "assistant", "content": text}, "done": True}
            if evaluated is not None:
                out["prompt_eval_count"] = evaluated
            return self._send(200, out)
        out = {"id": "x", "object": "chat.completion", "model": body.get("model"),
               "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}]}
        if evaluated is not None:
            out["usage"] = {"prompt_tokens": evaluated, "completion_tokens": 1, "total_tokens": evaluated + 1}
        return self._send(200, out)

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def fake_model_server(**cfg):
    """(server, base_url). server.cfg is live-editable; server.requests records every body. shutdown() to stop."""
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    srv.cfg = cfg
    srv.requests = []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def dead_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return "http://127.0.0.1:%d" % port


# ---------------------------------------------------------------------------------------------------------------
# A scratch world for in-process thinker tests: HOME, a brain, a credential home, a bin dir -- all under SCRATCH
# ---------------------------------------------------------------------------------------------------------------
def world(monkeypatch, root):
    """Point everything the seam reads at `root` (a fresh scratch dir). Returns a namespace of paths."""
    import tempfile
    from types import SimpleNamespace
    root = Path(root)
    home = root / "home"
    brain = home / ".claude" / "memory"
    brain.mkdir(parents=True)
    (home / "tmp").mkdir()
    bindir = root / "bin"
    bindir.mkdir()
    for k in ("VOIDLOOM_ENV_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "WIZARD_ENV_FILE", "WIZARD_LIBRARY_HOME",
              "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "KIMI_MODEL_API_KEY", "CODEX_HOME",
              "KIMI_CODE_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WIZARD_LIBRARY_DB", str(brain / "claude_memory.db"))
    monkeypatch.setenv("TMPDIR", str(home / "tmp"))
    monkeypatch.setenv("PATH", "%s:/usr/local/bin:/usr/bin:/bin" % bindir)
    monkeypatch.setattr(tempfile, "tempdir", None)
    return SimpleNamespace(root=root, home=home, brain=brain, bin=bindir,
                           thinker_home=home / ".voidloom" / "thinker", log=root / "fake.log")


def save_cred(w, backend, value="fake-credential-0123456789abcdef"):
    th = Path(w.thinker_home)
    th.mkdir(parents=True, exist_ok=True)
    os.chmod(str(th), 0o700)
    if backend == "codex":
        (th / "codex").mkdir(exist_ok=True)
        os.chmod(str(th / "codex"), 0o700)
        (th / "codex" / "auth.json").write_text('{"auth_mode": "chatgpt", "tokens": "fake-test-only"}')
        os.chmod(str(th / "codex" / "auth.json"), 0o600)
        return
    name = {"claude": "CLAUDE_CODE_OAUTH_TOKEN", "kimi": "KIMI_MODEL_API_KEY"}[backend]
    f = th / ("%s.env" % backend)
    f.write_text("%s=%s\n" % (name, value))
    os.chmod(str(f), 0o600)


def record(thinker, backend, *extra):
    """`thinker set <backend> --probe-env direct` in-process; asserts it recorded."""
    env = thinker.run(["set", backend, "--probe-env", "direct", "--answered-by", "test"] + list(extra))
    assert env["_exit_code"] == 0, env
    return env
