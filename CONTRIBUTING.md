# Contributing to Voidloom

Thank you for considering a contribution. This page covers setting up a
checkout, running the tests, the rules that keep the package consistent, and how
to propose a change.

**Small fixes** (a typo, a clear bug with an obvious fix, a missing test): open
a pull request directly.
**Bigger changes** (a new verb or organ, a schema change, a change to what the
installer writes or asks, anything that touches the network): please **open an
issue first** and describe the problem and the approach, so we can agree on it
before you spend time on code.

---

## Set up a development checkout

You need `git`, `bash` and Python 3.10 or newer.

```bash
git clone https://github.com/void-wright/voidloom.git
cd voidloom
python3 -m pip install --user pytest        # or install pytest into a virtual environment
```

The repository root mirrors what `install.sh` lays into `~/.claude/memory/`, so
you can work and test straight from the checkout; you do not need to install
Voidloom to develop it. [`docs/LAYOUT.md`](./docs/LAYOUT.md) explains where
things live and the one rule for where a new module goes.

Many tests use only the standard library. Tests that need the semantic stack
(PyTorch, sentence-transformers, sqlite-vec) skip themselves when it is
missing. To run those too, install the pinned dependencies into a virtual
environment and run the tests with that interpreter:

```bash
python3 -m venv .venv
.venv/bin/python3 -m pip install -r install/requirements.txt pytest
```

`install/requirements.txt` explains how to get a CPU-only PyTorch instead of
the multi-gigabyte CUDA build.

---

## Run the tests

From the repository root:

```bash
python3 -m pytest voidloom/tests/ -q
```

`voidloom/tests/conftest.py` puts the repository root and `organs/` on the
import path, so this one command is the whole fast suite.

**Read the header the suite prints before quoting its result.** Some test
classes need a real, populated brain. The header names the brain it found and
whether those classes will run or skip — and in pytest's summary line a skip
looks the same as a pass. If you have Voidloom installed, the suite finds your
brain by default; to keep it out of the run, point `WIZARD_LIBRARY_DB` at a
copy. `VOIDLOOM_TESTS_QUIET=1` hides the header.

The slower release gates have their own runner:

```bash
python3 voidloom/tests/harness/run_all.py --list    # what it runs, and what it deliberately does not
python3 voidloom/tests/harness/run_all.py
```

### GPUs: never assume device 0

On many machines GPU 0 drives the monitor, and a model loaded there can starve
the desktop with no error. So:

- Run tests on the CPU unless you are deliberately testing a GPU path:
  `CUDA_VISIBLE_DEVICES="" python3 -m pytest voidloom/tests/ -q`.
- In code, never hard-code `cuda`, `cuda:0` or a card number. Ask the
  resolver in `organs/voidloom_device.py`, which honours
  `CUDA_VISIBLE_DEVICES` and the card the owner declared.
- If you must set `CUDA_VISIBLE_DEVICES` inside a Python script, assign it
  before `import torch` (CUDA reads it once, at initialisation).

`tools/gpu_pin_gate.py` checks the tree for hard-coded pins and runs as part of
the release gates below.

---

## The rules that keep the package consistent

### Adding, removing or renaming a tracked file

The installer lays files from a manifest derived from the git index, and the
tests refuse a tree where the two disagree. After staging the change:

```bash
python3 tools/build_manifest.py --lay --package    # regenerate install/lay_manifest.json and docs/PACKAGE_MANIFEST.md
python3 tools/build_manifest.py --lay --check      # must report clean
```

Commit both regenerated files with your change. If the file is new at the top
level, or changes a count that [`docs/LAYOUT.md`](./docs/LAYOUT.md) states,
update that page too — `voidloom/tests/test_docs_name_real_paths.py` checks it.

### Documentation is tested

Several tests read the documentation: every path a doc tells you to run must
exist, every `install.sh` flag it names must be accepted, the verb list in
[`voidloom/README.md`](./voidloom/README.md) must match `voidloom --help`, and
counts such as the number of seed memories or scheduled jobs are re-derived from
the tree. If you change behaviour, change the words that describe it in the
same pull request.

### The release gates

```bash
python3 tools/release_check.py            # runs every gate and reports; always exits 0
python3 tools/release_check.py --strict   # exits 1 if any gate is red
```

### The leak gate (git hooks)

```bash
bash tools/git-hooks/install.sh
```

This installs `pre-commit` and `pre-push` hooks that run `tools/leak_gate.py`:
it refuses commits that carry personal home-directory paths, personal data,
secrets, or names on a private denylist. Contributors without a denylist still
get the personal-path and personal-data checks, and the secrets check whenever
[gitleaks](https://github.com/gitleaks/gitleaks) is installed. The same gate
runs in CI on every push and pull request, so a hook skipped locally is still
caught there.

Never commit memory contents, tokens, real names or paths from your own
machine; use `~` or `/home/you` in examples.

---

## Style

- **Match the surrounding code.** Each module has its own conventions; follow
  them rather than introducing new ones.
- **Comments explain why**, not what. The code says what it does; a comment
  records the reason, the failure it prevents, or the measurement behind a
  number.
- **Tests state their failure first.** A test's name and docstring should say
  what was broken (or would break) and what the test proves about it. Make
  sure a new test fails without your fix before you rely on it passing with it.
- **Refuse rather than guess.** When the code cannot tell what the owner wants
  (which GPU, which model, whether to overwrite), it asks or refuses and says
  why; it does not pick silently.
- **No new network calls** without an issue first. Anything that reaches the
  network must be listed in the README's "What touches the network".

---

## Licence of contributions

Voidloom is licensed under the [Apache License 2.0](./LICENSE). Contributions
are accepted under the same licence: as section 5 of the licence says, anything
you intentionally submit for inclusion is licensed under its terms, without
additional conditions ("inbound = outbound"). There is no contributor licence
agreement to sign. The pull request template asks you to confirm this.

---

## Security and conduct

Please report security problems privately, as described in
[`SECURITY.md`](./SECURITY.md) — not in a public issue. Everyone taking part
follows the [code of conduct](./CODE_OF_CONDUCT.md).
