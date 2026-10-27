#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""leak_gate.py -- the STANDING gate between this working tree and a public repository.

WHY THIS EXISTS
---------------
The repository went public on a fresh history because the private one carried, in
its commits, names that must never be published: client and organisation names, a
work email as the commit identity, personal paths. A fresh history fixes the past.
It does nothing for the NEXT commit: the identity git uses comes from the global
config, and the habits that wrote those names are still the habits that write
commits. A gate that runs once, on release day, guards one commit. This one runs on
every commit and every push, and again in CI.

WHAT IT CHECKS (each finding names file:line and the check, never the matched term)
  denylist   names on a PRIVATE list (organisations, clients, people). Plain text from
             $VOIDLOOM_DENYLIST or ~/.voidloom/org_denylist.txt, matched whole-word and
             as a substring (a short allowlist of known collisions, e.g. "tensorflow").
             In CI the list cannot ship, so $VOIDLOOM_DENYLIST_SHA256 carries the SHA-256
             of each lower-cased term and text is matched by hashing its word n-grams.
  identity   (commit modes) author and committer email. With
             `git config voidloom.requireNoreply true` (the maintainer's clone) anything
             but a GitHub noreply address is refused; always, an email matching the
             denylist is refused.
  paths      a literal home directory of a real user: /home/<name>/, /Users/<name>/,
             /mnt/c/Users/<name>/ -- placeholder names (you, user, someone, ...) pass.
  pii        tools/pii_gate.py's email / handle / person-at-organisation shapes.
  secrets    gitleaks, when it is installed (CI installs it; locally it warns).

MODES
  --staged          the staged diff (pre-commit) + the identity git will use
  --range "A..B"    every commit in the range (any `git rev-list` arguments, space-separated,
                    e.g. "SHA --not --remotes=origin"): added lines, messages, identities (pre-push)
  --tree            every tracked file (CI, release_check)

EXIT: 0 clean · 1 findings · 2 usage/configuration error. Never prints a denylist term.
GPU: none. NETWORK: none.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Known substring collisions: a denylist term inside an ordinary word. Each entry is a
# decision, reviewed like code -- an allowlist that grows to keep a gate quiet has
# stopped being a gate.
SUBSTRING_ALLOW = ("tensorflow",)

# Placeholder user names that appear in docs and test fixtures. Anything else after
# /home/ or /Users/ is treated as a real person's directory.
PLACEHOLDER_USERS = {
    "you", "user", "username", "yourname", "your-name", "someone", "owner", "x", "me",
    "runner", "dev", "alice", "bob", "example", "name", "first", "<user>", "$user", "${user}",
}
HOME_PATH_RE = re.compile(r"(?:/home/|/Users/|/mnt/c/Users/)([A-Za-z0-9._<>${}-]+)")
NOREPLY_RE = re.compile(r"@users\.noreply\.github\.com$", re.I)
BINARY_SUFFIXES = {".db", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".whl", ".pyc"}


# --------------------------------------------------------------------------- denylist
class Denylist:
    def __init__(self):
        self.terms: list[str] = []
        self.hashes: set[str] = set()
        self.max_words = 1
        self.source = "none"

    @classmethod
    def load(cls) -> "Denylist":
        d = cls()
        hashed = os.environ.get("VOIDLOOM_DENYLIST_SHA256", "").strip()
        path = Path(os.environ.get("VOIDLOOM_DENYLIST") or Path.home() / ".voidloom" / "org_denylist.txt")
        if path.is_file():
            for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
                ln = ln.strip().lower()
                if ln and not ln.startswith("#"):
                    d.terms.append(ln)
            d.source = "file"
        if hashed:
            d.hashes = {h.strip().lower() for h in re.split(r"[\s,]+", hashed) if h.strip()}
            d.source = "file+hashed" if d.terms else "hashed"
        # DERIVED FORMS (second public-release review, 2026-10-27): a multi-word name also leaks as its acronym,
        # its words run together, or a leading run of its words. Each becomes a term of its own (acronyms only from
        # three words up, so two-letter initials cannot fire on ordinary text). A single distinctive WORD of a name
        # cannot be derived safely -- list it on its own line in the private file.
        derived = []
        for t in d.terms:
            w = re.sub(r"[^a-z0-9]+", " ", t).split()
            if len(w) >= 2:
                derived.append("".join(w))
                derived.extend(" ".join(w[:k]) for k in range(2, len(w)))
                if len(w) >= 3:
                    derived.append("".join(x[0] for x in w))
        d.terms.extend(x for x in dict.fromkeys(derived) if x not in d.terms)
        for t in d.terms:
            d.hashes.add(hashlib.sha256(t.encode()).hexdigest())
        d.max_words = max([len(t.split()) for t in d.terms] + [4 if hashed else 1])
        return d

    @property
    def active(self) -> bool:
        return bool(self.terms or self.hashes)

    def hits(self, line: str) -> list[str]:
        """Return labels like 'denylist#3 (whole word)' -- an index, never the term."""
        low = line.lower()
        # A multi-word name split by punctuation -- ("First", "Second") in a Python tuple -- is still the name.
        # Found on the first public push: the plain matcher missed three such pairs that the hashed n-gram
        # matcher in CI caught. Both matchers now read the line with every non-alphanumeric run as one space.
        flat = " " + re.sub(r"[^a-z0-9]+", " ", low) + " "
        out = []
        for i, t in enumerate(self.terms):
            if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", low):
                out.append(f"denylist#{i} (whole word)")
            elif " " + re.sub(r"[^a-z0-9]+", " ", t).strip() + " " in flat:
                out.append(f"denylist#{i} (split by punctuation)")
            elif t in low:
                rest = low
                for a in SUBSTRING_ALLOW:
                    rest = rest.replace(a, " ")
                if t in rest:
                    out.append(f"denylist#{i} (substring)")
        if self.hashes and not self.terms:
            words = re.findall(r"[a-z0-9][a-z0-9&.'-]*", low)
            for n in range(1, self.max_words + 1):
                for k in range(len(words) - n + 1):
                    g = " ".join(words[k:k + n]).strip(".'-")
                    if hashlib.sha256(g.encode()).hexdigest() in self.hashes:
                        out.append("denylist (hashed, whole word)")
                        break
                if out:
                    break
        return out


# --------------------------------------------------------------------------- pii
def _load_pii():
    spec = importlib.util.spec_from_file_location("_vl_pii_gate", str(HERE / "pii_gate.py"))
    if spec is None or not (HERE / "pii_gate.py").is_file():
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def path_hits(line: str) -> list[str]:
    out = []
    for m in HOME_PATH_RE.finditer(line):
        name = m.group(1).strip("/").lower()
        if name in PLACEHOLDER_USERS or name.startswith(("{", "$", "<")) or "{" in name:
            continue
        out.append("personal path (a real user's home directory)")
    return out


# --------------------------------------------------------------------------- git helpers
def _git(*args, check=True) -> str:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit(f"leak_gate: git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def _added_lines(diff: str):
    """(file, line_no, text) for every added line of a unified diff."""
    f, n = None, 0
    for ln in diff.splitlines():
        if ln.startswith("+++ "):
            f = ln[6:] if ln.startswith("+++ b/") else None
        elif ln.startswith("@@"):
            m = re.search(r"\+(\d+)", ln)
            n = int(m.group(1)) if m else 0
        elif ln.startswith("+") and not ln.startswith("+++"):
            if f is not None:
                yield f, n, ln[1:]
            n += 1
        elif not ln.startswith("-"):
            n += 1


# --------------------------------------------------------------------------- scanning
class Gate:
    def __init__(self, deny: Denylist, pii):
        self.deny, self.pii, self.findings = deny, pii, []

    def add(self, where: str, check: str):
        self.findings.append({"where": where, "check": check})

    def scan_line(self, where: str, text: str, rel: str = ""):
        if Path(rel).suffix.lower() in BINARY_SUFFIXES:
            return
        if rel == "tools/leak_gate.py" or rel.startswith("voidloom/tests/test_leak_gate"):
            return  # this gate's own placeholders and synthetic fixtures
        for h in self.deny.hits(text):
            self.add(where, h)
        for h in path_hits(text):
            self.add(where, h)
        if self.pii is not None and rel != "tools/pii_gate.py":
            try:
                for (_n, kind, _tok) in self.pii.scan_text(text):
                    self.add(where, f"pii ({kind})")
            except Exception as e:  # a crashing check is a finding, never a pass
                self.add(where, f"pii check crashed: {type(e).__name__}")

    def scan_identity(self, label: str, name: str, email: str, require_noreply: bool):
        for h in self.deny.hits(f"{name} {email}"):
            self.add(label, f"identity: {h}")
        if require_noreply and not NOREPLY_RE.search(email or ""):
            self.add(label, "identity: not a GitHub noreply address (voidloom.requireNoreply is set)")

    def scan_blob(self, rel: str, text: str):
        for i, ln in enumerate(text.splitlines(), 1):
            self.scan_line(f"{rel}:{i}", ln, rel)


def _require_noreply() -> bool:
    return _git("config", "--get", "voidloom.requireNoreply", check=False).strip().lower() == "true"


def run_gitleaks(mode: str, rng: str | None) -> list[dict]:
    exe = shutil.which("gitleaks")
    if not exe:
        return [{"where": "(gitleaks)", "check": "secrets: gitleaks not installed -- NOT CHECKED", "warn": True}]
    if mode == "staged":
        cmd = [exe, "protect", "--staged", "--no-banner", "--redact", "-v"]
    elif mode == "range":
        cmd = [exe, "detect", "--no-banner", "--redact", f"--log-opts={rng}", "--source", str(ROOT)]
    else:
        cmd = [exe, "detect", "--no-banner", "--redact", "--no-git", "--source", str(ROOT)]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if p.returncode == 0:
        return []
    return [{"where": "(gitleaks)", "check": "secrets: gitleaks reported a leak (run it with -v to see where)"}]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--staged", action="store_true")
    g.add_argument("--range", metavar="A..B")
    g.add_argument("--tree", action="store_true")
    ap.add_argument("--require-denylist", action="store_true",
                    help="exit 2 if no denylist (plain or hashed) is configured")
    ap.add_argument("--no-gitleaks", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    deny = Denylist.load()
    if a.require_denylist and not deny.active:
        print("leak_gate: --require-denylist, but neither $VOIDLOOM_DENYLIST(_SHA256) nor "
              "~/.voidloom/org_denylist.txt is configured", file=sys.stderr)
        return 2
    gate = Gate(deny, _load_pii())
    mode = "staged" if a.staged else "range" if a.range else "tree"

    if mode == "staged":
        for f, n, t in _added_lines(_git("diff", "--cached", "--unified=0", "--no-color")):
            gate.scan_line(f"{f}:{n}", t, f)
        name = _git("var", "GIT_AUTHOR_IDENT").rsplit("<", 1)
        cname = _git("var", "GIT_COMMITTER_IDENT").rsplit("<", 1)
        rq = _require_noreply()
        gate.scan_identity("author (next commit)", name[0], name[1].split(">")[0] if len(name) > 1 else "", rq)
        gate.scan_identity("committer (next commit)", cname[0], cname[1].split(">")[0] if len(cname) > 1 else "", rq)
    elif mode == "range":
        rq = _require_noreply()
        for sha in _git("rev-list", *a.range.split()).split():
            meta = _git("show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce%x00%B", sha).split("\x00", 4)
            an, ae, cn, ce, msg = (meta + [""] * 5)[:5]
            gate.scan_identity(f"{sha[:8]} author", an, ae, rq)
            gate.scan_identity(f"{sha[:8]} committer", cn, ce, rq)
            for i, ln in enumerate(msg.splitlines(), 1):
                gate.scan_line(f"{sha[:8]} message:{i}", ln)
            diff = _git("show", "--format=", "--unified=0", "--no-color", sha)
            for f, n, t in _added_lines(diff):
                gate.scan_line(f"{sha[:8]} {f}:{n}", t, f)
    else:
        files = _git("ls-files").split()
        if not files:
            print("leak_gate: --tree found no tracked files (UNKNOWN, not clean)", file=sys.stderr)
            return 2
        for rel in files:
            p = ROOT / rel
            if p.suffix.lower() in BINARY_SUFFIXES or not p.is_file():
                if p.suffix.lower() == ".db" and p.is_file():
                    gate.scan_blob(rel + " (strings)", p.read_bytes().decode("latin-1"))
                continue
            gate.scan_blob(rel, p.read_text(encoding="utf-8", errors="replace"))

    findings = gate.findings + ([] if a.no_gitleaks else run_gitleaks(mode, a.range))
    hard = [f for f in findings if not f.get("warn")]
    report = {"mode": mode, "denylist": deny.source, "denylist_terms": len(deny.terms),
              "denylist_hashes": len(deny.hashes), "findings": hard,
              "warnings": [f for f in findings if f.get("warn")], "verdict": "REFUSED" if hard else "CLEAN"}
    if a.json:
        print(json.dumps(report, indent=1))
    else:
        if not deny.active:
            print("leak_gate: no denylist configured -- names on a private list are NOT checked", file=sys.stderr)
        for f in report["warnings"]:
            print(f"leak_gate: warning: {f['check']}", file=sys.stderr)
        for f in hard:
            print(f"leak_gate: {f['where']}: {f['check']}", file=sys.stderr)
        print(f"leak_gate: {report['verdict']} ({mode}; {len(hard)} finding(s); denylist: {deny.source})",
              file=sys.stderr)
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
