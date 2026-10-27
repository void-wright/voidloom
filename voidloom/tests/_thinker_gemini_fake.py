# -*- coding: utf-8 -*-
"""A fake `agy` (Antigravity CLI) for the gemini thinker tests -- in the _thinker_fakes style: a small python script
whose behaviour is read from a JSON file BESIDE it (<path>.json), because the seam hands a child a MINIMAL environment.

It speaks the shapes MEASURED on the real agy 1.3.4 (Round 1 L-G, 2026-10-16, iterations/r1_LG_log.md):
  * no sign-in file under $HOME/.gemini/antigravity-cli/  -> rc 1, stdout {"status":"ERROR","response":"",
    "error":"authentication failed or timed out",...}, stderr "Authentication required. ..." (the real one waits 60 s
    for a pasted code first; the fake answers at once)
  * an unknown --agent name -> rc 1 '{"status":"ERROR","error":"unknown agent \\"x\\" (available: ...)"}'
  * `agy agents` lists $HOME/.gemini/config/agents/*.md by their `name:`
  * a success envelope {"conversation_id","status","response","error":"",...} (+ "model" when `report_model` says so:
    whether the real success envelope names the model is MEASURED by the owner's sign-in script, T7)
Nothing here reaches a network, the real agy, or the owner's ~/.gemini.

Behaviour keys (all optional):
  good_token      the only sign-in file content that answers (default: any non-empty file answers)
  fallback_token  a path read when $HOME has no sign-in file -- emulates a program that finds ANOTHER sign-in (the
                  owner's, through the password database); the negative control must catch it
  report_model    null (default: no model key -- MEASURED, owner run T7), "argv" (echo --model), or a fixed name
  known_models    the --model values agy accepts (default the 3.8 Flash base + its -low/-medium/-high); any other is
                  rc 1 "... is not recognized as a known model ..." (MEASURED, T7); enforce_model false accepts all
  listing         what `agy models` prints (default the 3.8 Flash variants, one "<id>\t<label>" line each)
  tool_steps_default / tool_steps_locked  stream-json tool step events for the default agent (default 1) and for
                  --agent (default 0) -- MEASURED T4: 2 events (ACTIVE + DONE) for one write_to_file step
  registry        tool names agy knows; an agent file naming another is rc 3 "unknown component" (MEASURED, T5b)
  mode            answer | write | write_home | fail | quota | sleep
  reply           the answer when the prompt carries no probe challenge
  login_token     what an interactive run (no -p) writes as the sign-in file; login_fails: exit 1 writing nothing
  log             a file OUTSIDE the call's folders: one JSON line per invocation
  version         what --version prints (default "1.3.4-fake")
"""
import json
import os
import sys
from pathlib import Path

FAKE_AGY_SRC = r'''
import json, os, re, sys, time
from pathlib import Path
cfg_path = Path(__file__).with_name(Path(__file__).name + ".json")
cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
argv = sys.argv[1:]
home = Path(os.environ.get("HOME", "/nonexistent"))
tok = home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
agents_dir = home / ".gemini" / "config" / "agents"
def opt(name):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None
def log(extra):
    if cfg.get("log"):
        with open(cfg["log"], "a") as fh:
            fh.write(json.dumps(extra) + "\n")
agent_text = None
if opt("--agent"):
    f = agents_dir / (opt("--agent") + ".md")
    agent_text = f.read_text() if f.is_file() else None
hidden = {}
for p in cfg.get("probe_visible") or []:
    hidden[p] = os.path.exists(p) and (not os.path.isdir(p) or bool(os.listdir(p)))
log({"argv": argv, "env": dict(os.environ), "cwd": os.getcwd(), "cwd_listing": sorted(os.listdir(os.getcwd())),
     "agent_file": agent_text, "token_present": tok.is_file(), "visible": hidden,
     "stdin_tty": os.isatty(0), "t0": time.time()})
if os.environ.get("AGY_CLI_DISABLE_AUTO_UPDATE") != "true" and cfg.get("update_marker"):
    Path(cfg["update_marker"]).write_text("the updater would have run\n")      # =1 is IGNORED by the real agy
if argv[:1] == ["--version"]:
    print(cfg.get("version", "1.3.4-fake"))
    sys.exit(0)
FLASH = ["gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low"]
if argv[:1] == ["models"]:
    print("Fetching available models...")
    for line in cfg.get("listing", ["%s\tGemini 3.8 Flash (%s)" % (m, m.rsplit("-", 1)[1].title()) for m in FLASH]):
        print(line)
    sys.exit(0)
if argv[:1] in (["agents"], ["agent"]):
    for f in sorted(agents_dir.glob("*.md")) if agents_dir.is_dir() else []:
        m = re.search(r"^name: (\S+)", f.read_text(), re.M)
        print(m.group(1) if m else f.stem)
    sys.exit(0)
def env(status, response="", error="", model=None):
    o = {"conversation_id": "fake", "status": status, "response": response, "error": error,
         "duration_seconds": 0.01, "num_turns": 1 if status != "ERROR" else 0,
         "usage": {"input_tokens": 1, "output_tokens": 1, "thinking_tokens": 0, "cache_read_tokens": 0,
                   "total_tokens": 2}}
    if model is not None:
        o["model"] = model
    print(json.dumps(o))
if "-p" not in argv:
    # the interactive TUI: the human signs in here (the fake writes the sign-in file at once)
    if cfg.get("login_fails"):
        sys.stderr.write("sign-in cancelled (fake)\n")
        sys.exit(1)
    tok.parent.mkdir(parents=True, exist_ok=True)
    tok.write_text(cfg.get("login_token", "fake-brain-sign-in"))
    print("Signed in to a Google account (fake). Type /exit to leave.")
    sys.exit(0)
prompt = opt("-p") or ""
if opt("--agent") and agent_text is None:
    names = sorted(f.stem for f in agents_dir.glob("*.md")) if agents_dir.is_dir() else []
    msg = 'unknown agent "%s" (available: %s)' % (opt("--agent"), ", ".join(names))
    env("ERROR", error=msg)
    sys.stderr.write("error: %s\n" % msg)
    sys.exit(1)
if agent_text is not None:
    m = re.search(r"^tools: \[(.*)\]$", agent_text, re.M)
    names = [x.strip() for x in (m.group(1).split(",") if m else []) if x.strip()]
    reg = cfg.get("registry", ["search_web", "read_url_content", "write_to_file", "run_command"])
    bad = [n for n in names if n not in reg]
    if bad:
        sys.stderr.write('error: failed to construct executor: failed to resolve components: unknown component: tool '
                         '"%s" not found in registry\n' % bad[0])
        sys.exit(3)
model_arg = opt("--model")
if model_arg and cfg.get("enforce_model", True) and model_arg not in cfg.get("known_models", ["gemini-3.8-flash"] + FLASH):
    msg = ('invalid model selection (--model "%s" --effort ""): model %s is not recognized as a known model or custom '
           'model in settings Available models: Gemini 3.8 Flash (Medium)' % (model_arg, model_arg))
    env("ERROR", error="error: " + msg)
    sys.stderr.write("error: %s\n" % msg)
    sys.exit(1)
have = tok.read_text().strip() if tok.is_file() else ""
if not have and cfg.get("fallback_token") and os.path.isfile(cfg["fallback_token"]):
    have = Path(cfg["fallback_token"]).read_text().strip()
good = cfg.get("good_token")
if not have or (good is not None and have != good):
    env("ERROR", error="authentication failed or timed out")
    sys.stderr.write("Authentication required. Please visit the URL to log in: (fake)\nerror: authentication failed or timed out\n")
    sys.exit(1)
mode = cfg.get("mode", "answer")
if mode == "quota":
    env("ERROR", error="RESOURCE_EXHAUSTED: quota exceeded for gemini-3.8-flash (fake)")
    sys.exit(1)
if mode == "fail":
    env("ERROR", error=cfg.get("said", "fake failure"))
    sys.exit(int(cfg.get("rc", 1)))
if mode == "sleep":
    time.sleep(float(cfg.get("sleep", 1)))
m = re.search(r"Create an empty file named (\S+) in the current directory", prompt)
if m and mode == "write":
    Path(m.group(1)).write_text("a tool ran\n")
if m and mode == "write_home":
    (home / m.group(1)).write_text("a tool ran in the persistent home\n")
word = None
m2 = re.search(r"What is (\d+) plus 1\? Reply with only the number\.", prompt)
if m2:
    word = str(int(m2.group(1)) + (2 if cfg.get("wrong_sum") else 1))
rm = cfg.get("report_model")
model = opt("--model") if rm == "argv" else rm
answer = word or cfg.get("reply", "an answer from the fake agy")
if opt("--output-format") == "stream-json":
    n = int(cfg.get("tool_steps_locked", 0) if opt("--agent") else cfg.get("tool_steps_default", 1))
    print(json.dumps({"event": "init", "init": {"conversation_id": "fake"}}))
    for i in range(n):
        for state in ("ACTIVE", "DONE"):
            print(json.dumps({"event": "step_update", "step_update": {"conversation_id": "fake", "step_index": 2 + i,
                              "state": state, "step_type": "tool", "tool_name": "write_to_file"}}))
    print(json.dumps({"event": "step_update", "step_update": {"conversation_id": "fake", "step_index": 9,
                      "state": "DONE", "step_type": "model_response"}}))
    res = {"conversation_id": "fake", "status": "SUCCESS", "response": "" if n else answer, "num_turns": 1,
           "usage": {"input_tokens": 1}}
    if n:
        res["denied_actions"] = [{"action": "write_file", "display_name": "WriteToFile"}]
    if cfg.get("leak_token") and have:
        res["response"] = "the sign-in is " + have
    print(json.dumps({"event": "result", "result": res}))
    sys.exit(0)
env("SUCCESS", response=answer, model=model)
'''


def make_fake_agy(bindir, name="agy", **behaviour):
    """Write an executable fake agy into bindir. Returns its absolute path."""
    bindir = Path(bindir)
    bindir.mkdir(parents=True, exist_ok=True)
    p = bindir / name
    p.write_text("#!%s\n%s" % (sys.executable, FAKE_AGY_SRC), encoding="utf-8")
    os.chmod(str(p), 0o755)
    set_behaviour(p, **behaviour)
    return str(p)


def set_behaviour(fake_path, **behaviour):
    Path(str(fake_path) + ".json").write_text(json.dumps(behaviour), encoding="utf-8")


def save_brain_sign_in(w, value="fake-brain-sign-in"):
    """The brain's own agy sign-in file, as a successful `thinker login gemini` leaves it."""
    th = Path(w.thinker_home)
    gh = th / "gemini-home"
    f = gh / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    f.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(str(th), 0o700)
    os.chmod(str(gh), 0o700)
    f.write_text(value)
    os.chmod(str(f), 0o600)
    return f
