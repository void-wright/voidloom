# Getting started with Voidloom

This walkthrough assumes you know nothing about Voidloom. It takes you from a
fresh machine to a working memory brain, then through your first day and first
week of use. Each step says what to type and what you should see. Deeper
detail lives in the other documents, which are linked where they help.

**In one sentence:** Voidloom is a memory for AI coding agents that lives on
your own computer — you store what was learned, recall it later by meaning, and
a session-start hook hands the agent its relevant memories automatically.

---

## Before you start

### What you need

| | |
|---|---|
| **Operating system** | Linux (including WSL2 on Windows) or macOS. Native Windows has a separate installer, `install/install.ps1`. |
| **Tools** | `bash`, `git`, and Python 3.10 or newer. |
| **On a Mac** | A Python from Miniforge, not Apple's built-in `python3` (Apple's cannot load the SQLite extension that semantic recall needs). See [On a Mac](#on-a-mac) below. |
| **Optional** | Claude Code and/or Codex, if you want memories loaded into each session automatically. An account with a model provider, only if you want the brain to think overnight. |

### How much disk, and how long

There are two tiers, and you choose by flag:

| Tier | What you get | Disk (the installer's own estimates) |
|---|---|---|
| **Semantic** (default) | Recall by meaning, plus everything below | About 6 GB for the Python environment on Linux with an NVIDIA GPU, or about 1.5 GB CPU-only; plus about 1.8 GB of models, downloaded once; plus pip's download cache (a few GB, which you may delete afterwards) |
| **Keyword-only** (`--tier0-only`) | Recall by matching words; no models, no downloads | About 10 MB, plus the brain (about 1 MB to start) |

Building the Python environment takes a few minutes CPU-only and roughly 5–15
minutes with the CUDA build, plus the time to download the models. If you
choose a model for overnight thinking, allow about five more minutes to give it
a credential.

### On a Mac

Apple's `/usr/bin/python3` is built without loadable SQLite extensions, so
install Miniforge, create a Python 3.12 environment, and tell the installer to
use it. The exact commands — including checking the Miniforge download against
its published checksum — are in
[`ONBOARDING_GUIDE.md`, *On a Mac*](./ONBOARDING_GUIDE.md#on-a-mac-and-on-any-machine-that-is-not-an-nvidia-pc).
They end with:

```bash
export WIZARD_PYTHON_BIN="$HOME/miniforge3/envs/voidloom/bin/python3"
```

Optionally, prove the Mac first. This builds a throwaway environment, checks the
GPU against reference results, writes one file (`preflight.json`) and installs
nothing:

```bash
bash install.sh --preflight --python "$HOME/miniforge3/envs/voidloom/bin/python3"
```

Apple Silicon Macs are proven, with the GPU (MPS) and on the CPU; Intel Macs
are supported but not yet proven on real hardware. If a Mac's GPU fails the
installer's own check, the installer moves the brain to the CPU and says so.

---

## Step 1 — Install

```bash
git clone https://github.com/void-wright/voidloom.git
cd voidloom
bash install.sh
```

On a machine with no brain yet, the installer starts a new one. The new brain is loaded with a seed
pack of 82 memories that teach an agent how to use the brain. (Already have a
brain file? Use `bash install.sh --attach-db /abs/path/to/brain.db` instead; it
is used in place, behind a verified backup image.)

For keyword-only, add `--tier0-only`. To keep everything on the CPU even though
a GPU is present, add `--force-cpu`.

**Preview first, if you like.** Neither of these changes anything:

```bash
bash install.sh --dry-run                  # every step it would take
bash install.sh --print-write-set          # every path outside ~/.claude it may touch
```

### The questions, in the order they appear

Nothing is pre-selected. Where a question has a default, it says so.

**1. The overnight thinker** (the first install, and any later one with no recorded answer). Before anything is written, the
installer prints the question with what it found on your machine beside each
letter:

> **Should your brain think while you sleep, and with which model?**
>
> a) Claude  
> b) Codex  
> c) Kimi  
> d) Gemini (Google; runs through Google's Antigravity program)  
> e) Not now — no model; keep the simple nightly routines  
> f) Other (which model?)  
>
> The model you chat with can be a different one: this answer is only about the thinking your brain does on its own, overnight.

Then: `Your answer (a, b, c, d, e or f):`

- **Not sure? Type `e`.** Nothing thinks overnight, nothing is billed, and you
  can choose later with `voidloom thinker set`.
- **a–d:** if that model's program is not installed, the installer shows the
  company's own install command word for word and asks
  `Run it now? [y/N]` — it runs it only on your `y`, never with `sudo`. Then it
  shows the matching part of
  [`OVERNIGHT_THINKER_SETUP.md`](./OVERNIGHT_THINKER_SETUP.md), waits
  (`Press Enter when ready:`) while you give the brain its **own** credential,
  proves it with a small test request, and only then records your answer.
  Gemini is not yet available on macOS; on Linux it also needs `bubblewrap`
  (`sudo apt install bubblewrap`).
- **f:** it asks for the model's name and how to reach it (an ollama server, an
  OpenAI-compatible server, or a command).

**2. Approvals** (every new install). Once a month the brain reviews itself and
proposes improvements.

- `a` — **Automatic** (recommended; a blank line chooses this): the brain
  approves its own monthly proposals and names what it decided at the start of
  each session for the next 7 days.
- `b` — **Manual**: each proposal waits for you in `voidloom approvals --tui`.

**3. Which GPU** (only with two or more NVIDIA GPUs and nothing declared yet).
The installer lists every card and asks:

- `Which card drives your monitor(s)? (a card number, or 'none')`
- `Which card should the brain's background jobs use? (a card number)`

A background model on the monitor's card can starve your desktop without any
error message, so the installer never guesses. If you pick the monitor's card
anyway, it asks you to type the number a second time. (The driver's own
"display active" flag is not trusted for this: a monitor that is asleep reads as
inactive.) Your answers are written to `~/.voidloom/wizard-library-env`, and
later runs read them instead of asking.

**4. The session hook** (at the end):
`Wire the session-start hook now so your first session offers the Persona Forge? [Y/n]`.
Yes adds one entry to `~/.claude/settings.json`, backing the file up first.

### What a successful install looks like

The installer prints what it decided about this machine before it changes
anything (for example `Compute: cuda [PROVEN]` and whether the reranker is on,
with the reason), runs its own self-check, and ends with:

```
MANDATORY NEXT STEP: open Claude Code in any folder and type /persona-forge
```

If the semantic tier could not be completed (offline, no disk space, missing
packages), the installer says so, explains how to add it later, and still
finishes with a working keyword-only brain.

### Installing for someone else (scripts and AI agents)

When there is no terminal to ask, the installer refuses with exit code 2 and
touches nothing until the answers arrive as flags: `--thinker
claude|codex|kimi|gemini|off|local`, `--approvals auto|manual`,
`--display-gpu <card|none> --gpu <card>` and `--wire-hooks` or
`--no-wire-hooks`. An AI agent installing for a person must put each question
to that person — the thinker question word for word — and pass the person's
answers; it must never choose them itself.

---

## Step 2 — Validate

First make sure the command is reachable:

```bash
voidloom --version
```

If the shell says `command not found`, add `~/.local/bin` to your `PATH`
(`export PATH="$HOME/.local/bin:$PATH"`, and add that line to your shell's
startup file).

Then run these checks in order.

| # | Run | Success looks like |
|---|---|---|
| 1 | `python3 ~/.claude/memory/install/self_check.py` | Exit code 0 and `VERDICT: YOUR BRAIN IS ALIVE. Store + recall work end-to-end.` Any failure prints what broke and the command that fixes it. |
| 2 | `voidloom doctor` | `"brain_healthy": true`. It also reports the session hook, the reranker (on or off, and why) and the compute in use. |
| 3 | `voidloom persona status` | `persona: absent` until you run the Persona Forge — that is pending, not a failure. |
| 4 | `voidloom hooks status` | Claude Code shows as installed if you answered yes to the hook question. |

**Check that recall works by meaning** (semantic tier). Store a memory, then ask
for it in different words:

```bash
voidloom store --content "The deployment checklist lives in the ops runbook; always run the smoke test before tagging a release." \
  --category workflow_pattern --tags "release,checklist" --importance 0.8

voidloom recall --query "what should I do before I cut a release?" --top-k 5
```

The memory should come back, even though the question shares almost no words
with it. On a keyword-only brain, query with words that appear in the memory
instead (for example `--query "smoke test release"`).

On the semantic tier you can also confirm that both models are cached:

```bash
~/.claude/memory/venv/bin/python3 ~/.claude/memory/install/self_check.py --json
```

and look for the check named `full L4 models load (embedder + reranker)` with
`"ok": true`. It is a bonus check, not a required one: on a machine where the
reranker is off (CPU-only, or less than 15.0 GiB of RAM) it reads `false`,
because the reranker is deliberately not downloaded there.

---

## Step 3 — Your first day

### Design a persona (optional, offered once)

Open Claude Code in any folder and type `/persona-forge`. In a short
conversation you answer a few questions about yourself and how you like to
work, choose from multiple-choice rounds, optionally add flavour, and pick a
name; Claude fills in the details and shows you a one-screen draft before
anything is written. "Not now" is fine, and `voidloom persona decline` stops
the reminder. Details: [`PERSONA_FORGE.md`](./PERSONA_FORGE.md).

### Connect your agent

If you did not wire the hook during install, do it now. Without `--yes` these
only preview.

```bash
voidloom hooks install --platform claude --yes    # Claude Code
voidloom hooks install --platform codex --yes     # Codex
```

**Codex only:** Codex runs a new hook only after you trust it. Open Codex, run
`/hooks` once and trust the Voidloom SessionStart entry; until then Codex skips
it without saying so.

For a tool with no hook support, write the instruction block instead (it goes
into `~/.agents/AGENTS.md`, Codex's `AGENTS.md`, and an import line in
`~/.claude/CLAUDE.md`):

```bash
voidloom instructions install           # preview
voidloom instructions install --yes     # write it
```

See exactly what a new session will receive:

```bash
voidloom preamble
```

### The two habits that make it work

**Store as you go**, not only at the end. Good memories are decisions and why,
preferences, errors together with their fixes, conventions that were hard to
find.

```bash
voidloom store --content "Use UTC everywhere in the billing service; local time caused the March bug." \
  --category technical_decision --tags "billing,time" --importance 0.8
```

`--category` must be one of: `user_preference`, `technical_decision`,
`project_knowledge`, `error_pattern`, `skill_lesson`, `workflow_pattern`,
`people_context`, `tool_optimization`, `domain_knowledge`, `campaign_state`,
`correction`, `insight`. `--importance` runs from 0.0 to 1.0 (default 0.5).

**Recall before deciding.**

```bash
voidloom recall --query "time zone rules for billing" --top-k 5
voidloom recall --query "time zone rules for billing" --explain    # why each result surfaced
```

When you have several questions, batch them — the model loads once instead of
once per call:

```bash
voidloom batch-recall --items '["billing time zones", "release checklist", "staging schedule"]' --top-k 5
voidloom batch-store --items @memories.json      # a JSON array of {"content": ..., "category": ...}
```

Two things you may notice:

- **Dreams.** Beside its memories, `recall` sometimes lists a *dream*: a lead
  the brain synthesised, always labelled as unverified. It never replaces a
  memory. If you confirm or refute it, store what you found with
  `--answers <dream_id>`.
- **The seed pack.** The brain already knows how it works. Try
  `voidloom recall --query "how should I write a good memory"`.

### A council for hard decisions (optional)

```bash
bash install/install_council.sh --dry-run    # preview
bash install/install_council.sh              # add the lenses and /council
```

Then, in Claude Code: `/council should we change the schema now or after launch?`
It runs inside your current session and makes no extra model calls. See
[`COUNCIL_README.md`](./COUNCIL_README.md).

---

## Step 4 — Your first week

### Decide about the scheduled jobs

Nothing runs on a schedule until you arm it. Before you do, read
[`FIRST_NIGHT.md`](./FIRST_NIGHT.md): it lists every job, when it fires, what
it changes and whether it calls a model.

The default set is nine jobs (nightly consolidation, dreaming, weekly
resurfacing, digests, gap mapping, a monthly self-evaluation, a run ledger and
an embedding safety net). Three of them ask your chosen overnight thinker; with
**e) Not now** they take a simple, model-free path. To arm them:

```bash
bash ~/.claude/memory/install/install_crons.sh --dry-run    # shows every unit it would write
bash ~/.claude/memory/install/install_crons.sh
```

(`bash install.sh --with-crons`, re-run from your clone, does the
same and leaves your existing brain in place.)

If you want a model to think overnight but chose **e** at install time:

```bash
voidloom thinker status
voidloom thinker set claude        # or codex, kimi, gemini, local
```

It walks you through the one sign-in step, proves it, and only then records it.

Check and stop timers on Linux:

```bash
systemctl --user list-timers 'wizard-library-*'
systemctl --user disable --now wizard-library-<name>.timer
```

### Review approvals

After the monthly self-evaluation runs, the session banner tells you when
proposals are waiting.

```bash
voidloom approvals                 # what is waiting
voidloom approvals --tui           # decide interactively
voidloom approvals --mode          # who decides now; change with --mode auto or --mode manual
```

In automatic mode every decision the brain makes itself is signed
`policy:auto`, so it can always be told apart from yours. Belief links and
governance holds always wait for a person.

### Keep an eye on health

```bash
voidloom doctor
```

Run it whenever something seems off. It checks the database, that the brain is
on a local disk, the engine, the models, whether the session hook has actually
fired, and whether the brain's vectors match the model that will query them.

---

## Troubleshooting

**`no such module: vec0` from the `sqlite3` shell.** Expected, and not
corruption. The vectors live in sqlite-vec `vec0` tables, which only Voidloom
(which loads the extension) can read. Inspect ordinary tables such as
`memories` freely; reach the vectors through `voidloom`.

**The installer stops with `REFUSED` and exit code 2, mentioning that no
terminal is reachable.** It needs one of your answers and could not ask (a
script, a CI job or an AI agent's tool call). Pass the answer as the flag the
message names — see [Installing for someone else](#installing-for-someone-else-scripts-and-ai-agents).
Nothing was changed.

**`This host has 2 GPUs` … `REFUSED`.** Same cause on a multi-GPU machine: run
the installer in a terminal and answer the two GPU questions, or pass
`--display-gpu <card|none> --gpu <card>`. Run
`nvidia-smi --query-gpu=index,name,display_active --format=csv` to see your cards.

**Starting over with a new brain.** `bash install.sh --init-blank` creates a new
brain only where none exists; it never overwrites one. To use a brain file you
already have, pass `--attach-db /abs/path.db`.

**The install says the brain is `KEYWORD-ONLY (FTS5)`.** The seed pack could not
be embedded (offline, models missing, or no semantic tier). Nothing is lost.
When the cause is fixed, re-run the embedding step:

```bash
~/.claude/memory/venv/bin/python3 ~/.claude/memory/organs/embed_backfill.py ~/.claude/memory/claude_memory.db
```

It only touches memories that have no vector yet, so it is safe to repeat.

**On a Mac: semantic recall will not load.** You are probably using Apple's
`python3`. Install Miniforge, set `WIZARD_PYTHON_BIN` (see [On a Mac](#on-a-mac))
and re-run the installer. On an Intel Mac, use Python 3.12: PyTorch has no
Intel-Mac build for Python 3.13, so the install stays keyword-only there.

**The persona offer never appears / no memories arrive at session start.** The
hook is not wired. Check `voidloom hooks status`, then
`voidloom hooks install --platform claude --yes`. Headless, scheduled and
sub-agent runs ignore the persona offer by design.

**Codex ignores the hook.** Run `/hooks` in Codex and trust the Voidloom
SessionStart entry. A changed hook needs trusting again.

**Arming the scheduled jobs is refused.** The jobs that think need a recorded
answer to the thinker question. Run `voidloom thinker set off` (no thinking) or
choose a model with `voidloom thinker set <name>`, then arm again.

**Choosing Gemini (d) on Linux stops at bubblewrap.** Gemini's program needs
it; install it (`sudo apt install bubblewrap`) and choose `d` again.

**You moved a brain between a GPU machine and a CPU-only one.** Each brain
records which embedding model made its vectors. `voidloom doctor` reports
whether the vectors and the query model agree (`embedding_coherence`); see
[`ONBOARDING_GUIDE.md`](./ONBOARDING_GUIDE.md) and
[`RESTORE_MANIFEST.md`](./RESTORE_MANIFEST.md) before moving one.

Still stuck? Open an issue with the output of `voidloom doctor --json` (check it
for anything private first).

---

## Back up and upgrade

Your memories are one file, `~/.claude/memory/claude_memory.db`. Back it up with
the one-line command in the README's
[Back up and upgrade](../README.md#back-up-and-upgrade) section before every upgrade;
an upgrade is `git pull`, then `bash install.sh` from your clone, which keeps your brain
and reuses the answers you gave the first time.

## Uninstall

Run these from your clone of the repository.

```bash
bash uninstall.sh
```

Preview only: it lists everything it would remove and changes nothing.

```bash
bash uninstall.sh --yes
```

Removes the scheduled jobs, the session hooks and instruction blocks, the
`voidloom` command and the installed code. **Your memories (the brain database)
and your env file are kept**, so a later install picks up where you left off.

```bash
bash uninstall.sh --yes --remove-brain
```

Also deletes the brain and `~/.voidloom` — your memories, your env file and any
saved model credential — after you type the confirmation phrase it shows you.
This cannot be undone.

Downloaded model caches (`~/.cache/huggingface`), a persona you forged, and the
AI tools themselves are never removed by the uninstaller.
