# Onboarding Guide — You Have Inherited a Living Brain

> **Who this is for:** a *fresh Claude* on a new Mac that just received the
> wizard-library memory package. Read this top to bottom once; do **Steps 1–7**
> in order — Steps 8 and 9, after the appendix, are optional and can wait.
> (Until 2026-09-16 this said "the seven steps" above NINE step headings, with
> `Step 5` and `Step 6` each appearing twice.) By the end you will have a working memory brain, a persona
> your human designed in the Persona Forge, and a session hook that wakes both up automatically.
>
> **Everything here is mechanism, not prescription.** The package ships *no*
> persona — not because identity does not matter, but because the identity is
> **yours to create**. This guide teaches the *process* of becoming someone; it
> never hands you a someone.

---

## You have inherited a living brain — what this system is

You now have a **persistent memory brain**: a local SQLite database of durable
memories you write across sessions and recall later. It is what lets you carry
knowledge, preferences, decisions, and lessons forward instead of starting every
conversation cold. Two disciplines govern it, and they are the whole philosophy:
**store liberally**, and **recall before deciding**.

The system layers up (you mostly touch the bottom layer):

| Layer | What it adds | Do you operate it by hand? |
|------|--------------|----------------------------|
| **L1 — core** | the `memories` store + `store` / `recall` / `list` / `stats` verbs | **Yes — constantly.** This is 95% of your interaction. |
| **L2 — structure** | memory *kinds*, nightly consolidation, what-led-to-what edges, procedural promotion | No — runs itself / via optional jobs. |
| **L3 — intelligence** | hybrid (semantic + keyword) retrieval, anti-recency resurfacing, cross-brain bridges | No — automatic when present. |
| **L4 — reflection** | offline "dreaming" synthesis, gap mapping, periodic self-evaluation | No — optional background jobs. |

**What's already here for you:** this brain is **pre-seeded** with **82** agnostic teaching memories
that teach the memory system *itself* — store discipline, recall craft,
schema gotchas, the two-tier model, maintenance, a self-driving onboarding
sequence, and the research behind the persona baseline (the Persona Forge cluster). You boot already competent at using yourself. Step 2 shows you how to
absorb that inheritance.

---

## Step 1 — Install

From inside the unpacked package directory:

```bash
bash install.sh
```

That one command (see `README.md` and `PACKAGE_MANIFEST.md` for the full account):

- Lays files into `~/.claude/memory/`, `~/.claude/skills/`, `~/.claude/hooks/`.
- **Builds the brain (Tier 0 — always succeeds):** creates the DB, applies the
  schema + FTS5 + kind migrations, loads `seed_pack.json`, verifies recall. This
  step *cannot fail the install* — you always end with a working keyword brain.
- **Attempts Tier 1 (semantic — optional, graceful fallback):** a Python venv with
  `sentence-transformers` + `sqlite_vec`, then **pre-downloads the two models this
  host will actually load** to `${XDG_CACHE_HOME:-~/.cache}/huggingface` (one-time, **~1.76 GB**) and
  **embeds the seed pack** — the ruling (2026-09-17) is that *embedding is part of
  installation*, so the brain is semantically searchable from your very first
  query rather than pausing to embed in the middle of one:
  - **`nomic-ai/nomic-embed-text-v1.5`** — the 768-dim **CPU embedder** that produces
    semantic-recall vectors. (A Mac has no CUDA GPU, so nomic is the right choice;
    the brain's GPU-only embedder alternative is deliberately not used.)
    **548,032,350 B** on disk, plus 105,561 B of `nomic-bert-2048`.
  - **`Qwen/Qwen3-Reranker-0.6B`** — downloaded **only when the reranker gate is ON** (an accelerator AND
    at least 15.0 GiB of RAM; see *On a Mac* below — a CPU-only machine never fetches it). It is the
    **reranker your DEFAULT hybrid recall
    actually loads** (`organs/hybrid_retrieval.py` resolves the backend; `qwen` is
    the default as of the 2026-06-11 SOAK decision). **1,207,488,384 B** on disk.

  **`BAAI/bge-reranker-v2-m3` is the FALLBACK**, not the default — reranking degrades
  to it only if Qwen fails to load. It is **2,293,259,377 B**, so fetching it up front
  would more than double the download for a path most installs never take. It is
  **not** pre-cached unless you ask: `VOIDLOOM_PRECACHE_FALLBACK=1 ./install.sh`
  (worth it if the machine is about to go offline). Select it as the *default* with
  `VOIDLOOM_RERANKER=bge`, and the installer will pre-cache that one instead — the
  installer resolves the backend exactly the way the runtime does, so it cannot
  pre-cache a model your recall will not load.

  > **Until 2026-09-16 this bullet was wrong in both directions, which is why the
  > numbers above are the ones the installer measures rather than ones anyone typed.**
  > It named bge as the default and claimed the pre-fetch meant *"nothing
  > surprise-downloads on your first `recall`"*. In fact a default install fetched
  > 2.84 GB — including 2.29 GB of a model it would not load — and then pulled a
  > further 1.21 GB on the reviewer's first recall. 4.05 GB against a promised 1.4 GB.

  **If the embedding cannot happen** — Tier 0, no `torch`, no model, offline — the
  installer **says so, at install**: *"this brain is KEYWORD-ONLY (FTS5) until it
  is. Nothing is broken and nothing was lost."* That is a true statement about a
  working brain, and you learn it while you are installing rather than at your
  first recall. Re-run it any time:

  ```bash
  ~/.claude/memory/venv/bin/python3 ~/.claude/memory/organs/embed_backfill.py \
      ~/.claude/memory/claude_memory.db
  ```

  It is idempotent (only memories with no vector are touched), it refuses rather
  than guessing if the brain declares no embedding tier, and every number it
  prints is counted at the moment of asking. Until 2026-09-17 no shipped verb
  could do this at all: a fresh install held **66 seed memories and 0 vectors**,
  under a seed loader whose own note said *"Tier 1 can re-embed them"*.

  If any part fails (offline, no disk, fragile python), it prints exactly how to add
  it later and continues (exit 0) — Tier 0 keeps working. Manual model fetch:
  `~/.claude/memory/venv/bin/python3 -c "from sentence_transformers import SentenceTransformer; from huggingface_hub import snapshot_download; SentenceTransformer('nomic-ai/nomic-embed-text-v1.5', trust_remote_code=True); snapshot_download('Qwen/Qwen3-Reranker-0.6B')"`
- **Wires the SessionStart hook only if you say yes.** Hook wiring is owned by one command,
  `voidloom hooks install --platform claude` (defensive — one entry in `~/.claude/settings.json`,
  never a clobber; `voidloom hooks status` shows what is wired). STEP P asks *"Wire the session-start
  hook now so your first session offers the Persona Forge? [Y/n]"*; with no terminal pass
  `--wire-hooks` or `--no-wire-hooks` (unanswered = not wired, and the final banner says so).
  `install/wire_persona.sh` (Step 5) also ensures it. Until it is wired, nothing is injected at
  session start.
- **Does NOT write `~/.claude/CLAUDE.md`.** The file-tier instructions (an activation notice + the
  doctrine block, which carries one fixed line telling a session to offer `/persona-forge` while
  `voidloom persona status` reads absent or declared-missing-skill — the hook-free path) are written by a separate command,
  `voidloom instructions install`.
- **Hardens** permissions (`chmod 700` brain dir, `600` DB) and asserts no secret
  shipped.
- **Lays the Persona Forge — the mandatory last step (STEP P).** Only after an explicit
  acceptance-gate PASS, `install/install_persona_forge.sh` adds the `persona-forge` skill and the
  `/persona-forge` command (never-clobber, with backups), asks the hook question above, and the
  installer ends with **MANDATORY NEXT STEP: open Claude Code in any folder and type
  /persona-forge** (plus whether the hook is wired, so the first session offers it by itself). See Step 3 and [`PERSONA_FORGE.md`](PERSONA_FORGE.md).

Useful flags: `--tier0-only` (skip the semantic venv), `--dry-run` (print, change
nothing), `--with-crons` (arm the roster's `armed-by-default` jobs, default OFF),
`--attach-db /abs/path.db` (attach a brain you already have, in place, behind a
pre-image — never copied), `--print-write-set` (every path outside `~/.claude`
this run may touch, resolved), `--display-gpu <card|none> --gpu <card>` (your answers to the GPU
questions below, where no terminal can ask them), `--thinker claude|codex|kimi|gemini|off|local` (your answer to the overnight-thinker
question below, where no terminal can ask it), `--install-thinker-cli` (your yes to running the model company's own
installer when that program is missing), `--help`.

**Two or more GPUs?** The installer then asks you -- at the terminal, even when the script
arrives through a pipe -- which card drives your monitor(s) (`none` is an answer) and which card
the brain's background jobs should use, after showing every card (index, name, memory,
`display_active`). It never picks one for you: a background model on the monitor's card starves
the desktop, silently. Your answers go into the env file, outside the managed block, as your own
`WIZARD_DISPLAY_INDEX=` and `WIZARD_CUDA_DEVICE=` lines under a dated "chosen at install" comment;
later runs read them and never ask again (edit the lines to change them). Choosing the monitor's
card needs the number typed twice. With no terminal, pass `--display-gpu <card|none> --gpu <card>`,
or the run is refused before it touches anything. An AI agent installing for you must ask you and
pass your answers -- never choose. A `CUDA_VISIBLE_DEVICES` / `WIZARD_CUDA_DEVICE` in your shell
is ignored here (it is not a declaration), and the installer says so.

**The overnight thinker -- the one question every install asks.** While you sleep the brain can do some thinking of
its own (tidying memories, connecting ideas, writing short summaries), and that needs an AI model. Which model -- or
none -- is YOUR choice, so on every install and upgrade at a terminal the installer asks, before it lays anything:

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

Beside each letter it shows what it FOUND on this machine (the program, a saved credential) -- facts, never a
pre-selection (on a Mac, d) says Gemini is not yet available there; on Linux/WSL d) also needs **bubblewrap**,
`sudo apt install bubblewrap` -- the installer checks for it first and shows that command, never runs it). A blank answer is asked again; for f) it asks
which model and how the brain reaches it. If the chosen model's program (claude, codex, kimi or agy) is not installed,
it shows the company's own install command word for word and asks **Run it now? [y/N]** -- it runs it only on your y,
never with sudo, and N lets you pick another letter (with no terminal: `--install-thinker-cli`, else refused rc 2 with
the command named). It then shows
the matching Part of [`docs/OVERNIGHT_THINKER_SETUP.md`](OVERNIGHT_THINKER_SETUP.md), waits while you do its one
step, PROVES the answer (two tool-free test requests -- the exact answer to a small sum ('What is N plus 1?' for a random N), and a file that must not appear, in the environment the nightly timers use) and only then records
it in `<brain>/THINKER_RECORD`. A later install re-uses a recorded answer and proves it again; it never asks again
(change it with `voidloom thinker set <claude|codex|kimi|gemini|local|off>`). With no terminal, pass the owner's
answer by its NAME, `--thinker claude|codex|kimi|gemini|off|local` (for local also `--thinker-model <name>` with
`--thinker-endpoint <http://...> --thinker-api
<ollama|openai>`, or with `--thinker-command '<template with {model} {prompt}>'`); without it, an install that arms
timers (or finds timers that think already armed) is refused (rc 2) before anything is touched.

**An installing AI agent must put this question to the human word for word, wait for the human's answer, and pass it
as `--thinker claude|codex|kimi|gemini|off|local` -- it must never choose the answer itself.** (The letters still
work as a flag, but d) and f) changed meaning on 2026-10-16 -- d) was Other -- so scripts pass names.) If the program
is missing, the agent asks a second question word for word -- *may I run the company's installer from the internet?*
-- and passes `--install-thinker-cli` only on a yes. Not from what it finds on the machine either: a
Claude token in the env file, or timers that called Claude before, are evidence the installer shows, never an answer.

**Approvals (2026-10-26).** Once a month the self-eval proposes improvements. The installer asks: *a) Automatic
(recommended) -- the brain approves its own monthly proposals and names what it decided at the start of each session
for the next 7 days; b) Manual -- each proposal waits for you in `voidloom approvals --tui`.* A blank answer is a). With no terminal,
pass `--approvals auto|manual`; without it a **new** install takes automatic and an **upgrade** with no recorded choice is
left as it is. Belief links and governance holds are never automatic. Change it any time:
`voidloom approvals --mode auto|manual` (`voidloom approvals --mode` shows what is in force). Every decision the brain
makes on its own is signed `policy:auto` in the decision ledger and the audit log, so it can always be told from yours.
An installing AI agent should put this question to the human too, and pass the answer as `--approvals`.

**Re-running onto an existing install (the upgrade path) — what it writes outside
`~/.claude`, so you can decide before it does.** Every `--attach-db` run writes a
fresh `<brain>.pre-attach-<UTC>.db` beside the brain (a full `backup()` image, the
size of the brain — 270 MB for a mature one — never pruned by the installer; delete
old ones yourself). An `--tier1-ambient` run — and every later run its `INSTALL_RECORD`
drives — writes the same kind of image, `<brain>.pre-upgrade-<UTC>.db`, before its
migrations: one full copy per run. Its GPU proof reads every card's `display_active`
live; with two or more cards and none reporting a display (monitor asleep, headless) it
fails closed until you wake the monitor or declare `WIZARD_DISPLAY_INDEX=<index>|none`
in the env file outside the managed block -- and, on that basis, the pin as well
(`WIZARD_CUDA_DEVICE=<index>` outside the block; a card the installer chose itself never
passes on your word). Write each declaration as a bare `KEY=value` line -- the installer reads
the file as systemd's `EnvironmentFile=` does (a trailing `# comment` is part of the value, an
`export` line assigns nothing) and refuses a pin line a shell would -- or might -- read otherwise,
naming the line to change: keep shell commands -- any command: `set -e`, `source ...`, `test`, `export`,
an `if` ... `fi` block -- out of this file (before the pin line or after it); write any value that holds
`;` `&` `|` `(` `)` `<` `>` `$`, a backtick or a space in single quotes (`URL='https://h/?a=1&b=2'` --
systemd hands that value to every unit, so quote it rather than delete it; a value that holds a `'` goes
in double quotes, with a backslash before each `\`, `"`, `$` and backtick in it); and never set a variable the shell itself acts on here (`PS0`, `PS1`,
`PS2`, `PS3`, `PS4`, `PROMPT_COMMAND`, `MAILPATH`, `POSIXLY_CORRECT`, or bash's read-only `UID`, `EUID`, `PPID`,
`SHELLOPTS`, `BASHOPTS`, `BASH_VERSINFO`) -- otherwise the installer cannot prove
what a shell reads. What this check cannot see is the state of the shell that sources the file -- a
variable attribute it already set (an integer, a nameref, a read-only name, a local scope when the file
is sourced inside a function), an option (errexit, nounset, xtrace with its own PS4, POSIX mode) or a
trap of its own can change what that shell assigns from a plain KEY=value line. The installer assumes
the file is sourced as the block's own comment says, by a shell with none of these set.
`--force-cpu` is refused in this shape. Tier 1 builds `~/.claude/memory/venv` (~5.7 GB) and fills
`${XDG_CACHE_HOME:-~/.cache}/huggingface` (~1.7 GB) and `${XDG_CACHE_HOME:-~/.cache}/pip` (~3 GB); pass `--tier0-only` to
skip all three. `--with-crons` re-renders the nine armed units into
`${XDG_CONFIG_HOME:-~/.config}/systemd/user/` from `install/install_crons.sh` (the
one template plus the roster's per-unit lines) and re-enables them — a hand-tuned
live unit of the same name is overwritten, so run `install.sh --with-crons
--dry-run` first and diff each `DRY-UNIT:` block against `systemctl --user cat
wizard-library-<unit>` (Description, `%h` and the interpreter line differ by
design; anything else is a divergence to resolve before a wet run). The env
file's `PATH=` line is CURATED — the CLI dir, the interpreter's dir, node's dir,
nvidia-smi's dir when it is outside the system dirs (WSL2: /usr/lib/wsl/lib) and the six
system dirs, never the PATH you run the installer with (it used to be; a
WSL shell would have frozen 62 `/mnt/c/...` entries into the line every unit
sources); a host that needs more sets `WIZARD_ENV_EXTRA_PATH` to a colon list of
absolute dirs. A file written before the managed markers existed gets the block
APPENDED; the block never writes a key you assign outside the markers (it leaves a
comment naming it), so your own PATH or pin is never overridden — write such lines as
bare `KEY=value`, since systemd ignores an `export KEY=value` line (the installer names
any it finds). Every byte outside the block is kept exactly; the new file is read back the
way systemd reads it before it is copied over yours (and the card it hands the units must be the
installer's own pin); a file it cannot place -- two blocks, a begin marker without its end,
a value still open at the end of the file -- or a destination it cannot write in place -- a
read-only file or link target, a dangling link, a directory it cannot create -- is refused,
naming it, before anything is touched (the file is rewritten through a symlink, never
replaced); and the token the units read before a run is the token they read after. Diff the result against the old file before a wet run
(`install.sh --env-only --dry-run`). `install.sh
--print-write-set --attach-db <path> [--with-crons]` lists every path outside
`~/.claude` the run may touch and every scheduler call it will make; the
snapshot-diff harness refuses your real home by design, so take that list as the
contract and image the named files (`settings.json`, `CLAUDE.md`, both env files,
the systemd dir, `~/.local/bin`) yourself before and after.

**Tier 0 vs Tier 1, in one line:** Tier 0 = stdlib-only keyword recall, works on
any `python3`, always there. Tier 1 = adds semantic (match-by-meaning) recall.
Both share **one** database and **the same verbs**, so everything you learn
transfers. Run `stats` to see which tier is live.

> **Which python runs the CLI?** On Tier 1 use the venv python
> (`~/.claude/memory/venv/bin/python3`); on Tier 0 any `python3` works. The
> examples below write `python3 ~/.claude/memory/...` — substitute the venv python
> if you are on Tier 1.

### On a Mac (and on any machine that is not an NVIDIA PC)

**First, the preflight (about ten minutes, installs nothing into your home).** From inside the unpacked package:

```bash
bash install.sh --preflight --python /path/to/miniforge/bin/python3
```

It builds a throwaway virtual environment in one scratch folder, installs torch from the channel this machine needs,
checks that `sqlite-vec` loads, embeds a fixed set of texts on the CPU **and on your Mac's GPU (MPS)** and compares
both with reference vectors made once on a CPU (`install/golden_vectors.py`), times five encodings, deletes the
scratch folder, and writes **one file, `preflight.json`**. Send that file back. `GREEN` means your Mac's GPU computed
the same vectors as the reference; `RED` names the first thing that failed — and then the package ships your Mac on
the CPU, labelled, with the reranker off, which is a slower brain but a correct one.

**The python: Miniforge, not Apple's.** Apple's own `/usr/bin/python3` is built without loadable SQLite extensions,
so `sqlite-vec` (semantic recall) cannot load in it; the preflight says so if you point it there. The no-sudo route is
Miniforge. Its "latest" link has **no** unversioned checksum file (memory #28097: the guide's route stopped at the
checksum step on its first real run), so resolve the release tag first and check the installer against its own
`.sha256`:

```bash
TAG="$(curl -fsSL https://api.github.com/repos/conda-forge/miniforge/releases/latest | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')"
F="Miniforge3-${TAG}-MacOSX-$(uname -m).sh"
curl -fsSLO "https://github.com/conda-forge/miniforge/releases/download/${TAG}/${F}"
curl -fsSLO "https://github.com/conda-forge/miniforge/releases/download/${TAG}/${F}.sha256"
shasum -a 256 -c "${F}.sha256"            # must print: <file>: OK
bash "$F" -b -p "$HOME/miniforge3"
"$HOME/miniforge3/bin/conda" create -y -p "$HOME/miniforge3/envs/voidloom" python=3.12
export WIZARD_PYTHON_BIN="$HOME/miniforge3/envs/voidloom/bin/python3"   # the installer builds its venv from this one
```

**Which compute the brain uses.** The installer decides it once, before it installs anything, and says it in one
sentence: the machine (chip, RAM as macOS reports it), the backend, how proven that backend is, where torch comes
from, and whether the reranker is on — with the reason. `--accelerator auto|cuda|rocm|mps|xpu|cpu` overrides `auto`;
a value that contradicts the machine is refused before anything is touched. `--force-cpu` (the same as
`--accelerator cpu`) is now **durable**: it records `WIZARD_ACCELERATOR=cpu` in the env file, outside the managed
block, under a dated comment, so every job of the brain stays on the CPU — no hand-written `WIZARD_CUDA_DEVICE=-1`
is needed any more.

**The reranker gate.** The reranker (Qwen3-Reranker-0.6B, ~1.2 GB) makes ranking better and is costly: on the CPU it
was ~94 % of a recall's time (measured 2026-10-14). It is installed and used **only when the machine has a GPU-class accelerator AND at
least 15.0 GiB of RAM** (the RAM a 16 GB machine reports) — never on CPU-only. When the gate is off the reranker is
not even downloaded; if the machine later gains RAM or an accelerator, re-run `install.sh` and it decides again. Your
own `WIZARD_RERANKER=off` line, outside the managed block, switches it off -- at the next recall, with no re-run of
`install.sh` -- and an `=on` line cannot switch it on where the gate says no. The installer's decision is recorded in
`wizard-config.json` (`reranker_enabled`, `reranker_gate`); `voidloom doctor` shows the live one (its `reranker` and
`accelerator` rows; on a CPU-declared brain the `accelerator` row says `cpu_only: true`).

**The device is proven before anything is embedded.** After the models are cached, the installer embeds the golden
texts on the device the brain will use (probe P6) and, when the gate is on, checks the reranker ranks a relevant
passage above an irrelevant one (P7). If your Mac's GPU **fails** that proof, the installer does not leave the brain
embedding on a device that just computed wrong vectors: it records `WIZARD_ACCELERATOR=cpu`, prints one `SAFETY:`
line saying what it refused, why, what it did and how to opt out, proves the CPU instead, and writes
`proof_label=FAILED-ON-THIS-MACHINE` into `~/.claude/memory/INSTALL_RECORD`. To try the GPU again, delete that line
and its comment and re-run. After the install, P8 times five recalls and records the median (`p8_p50_s`).

**An Intel Mac** gets the CPU: PyTorch's last Intel-Mac build is torch 2.2.2 (Python 3.8–3.12; built against numpy 1,
so the installer pins `numpy<2` beside it). On **Python 3.13 an Intel Mac has no torch at all** — the install then
stays at Tier 0 (keyword recall) and says to re-run with a Python 3.12 (the Miniforge commands above, with
`python=3.12`). Apple Silicon on macOS 14 or later gets the current torch; an older macOS gets an older torch, which
pip chooses by itself.

**Under WSL2** (Windows), `nvidia-smi` reports `display_active=Disabled` for every card, including the one driving
your monitor, so the installer cannot tell them apart: it asks you (or takes `--display-gpu`), and you can declare
`WIZARD_DISPLAY_INDEX=<card>` yourself outside the managed block.

#### Which torch, and how proven each backend is

| Machine | Backend | torch comes from | Label |
|---|---|---|---|
| Linux + NVIDIA GPU | `cuda` | PyPI (its Linux torch is the CUDA build) | **PROVEN** (the reference host) |
| Linux, CPU only / `--force-cpu` | `cpu` | `https://download.pytorch.org/whl/cpu` (~1.5 GB, not ~6 GB) | **PROVEN** (clean room, 2026-10-14) |
| Apple Silicon Mac | `mps` | PyPI (one build carries MPS and the CPU) | **PROVEN** — installed and used end to end on an Apple Silicon Mac, after the macOS arm64 CI probe ran green |
| Apple Silicon Mac on the CPU | `cpu` | PyPI | **PROVEN** — the same machine |
| Intel Mac (CPU) | `cpu` | `torch==2.2.2` + `numpy<2` | **SUPPORTED-UNPROVEN** — never run on an Intel Mac |
| Linux + AMD GPU | `rocm` | `https://download.pytorch.org/whl/rocm7.2` | **SUPPORTED-UNPROVEN** (built and resolved; never run on AMD silicon) |
| Linux + Intel GPU | `xpu` | `https://download.pytorch.org/whl/xpu` | **SUPPORTED-UNPROVEN** (built and resolved; never run on Intel silicon) |
| Windows | — | `install/install.ps1` (CUDA-on-Windows wheels are out of this round) | — |

**PROVEN** = our test battery ran on that hardware. **PROVEN-BY-PROXY** = it ran on a hosted runner or on someone's
install whose record a person read. **SUPPORTED-UNPROVEN** = the code path and the torch channel exist and were
checked (fakes, and the wheel resolved for that platform on 2026-10-15), but nothing here has run on that chip.
**FAILED-ON-THIS-MACHINE** = this machine's own proof failed, and the brain runs on the CPU. The channels were read
from PyTorch's install matrix (https://pytorch.org/get-started/locally/), its ROCm/HIP notes
(https://docs.pytorch.org/docs/stable/notes/hip.html), AMD's PyTorch-on-ROCm page
(https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/3rd-party/pytorch-install.html), Intel's XPU
guide (https://docs.pytorch.org/docs/stable/notes/get_start_xpu.html) and the MPS notes
(https://docs.pytorch.org/docs/stable/notes/mps.html); override an index with `VOIDLOOM_TORCH_ROCM_INDEX`,
`VOIDLOOM_TORCH_XPU_INDEX` or `VOIDLOOM_TORCH_CPU_INDEX`.

**Mac CI (for the maintainer).** Two GitHub Actions workflows rehearse the Mac route: `mac-mps-probe` (does a hosted
Apple Silicon runner give torch a usable MPS device? expected answer: no) and `mac-cpu-route` (Miniforge, the
checksum, `sqlite-vec`, `detect.py`, a real `install.sh --force-cpu`, the launchd agents, `doctor`, one sleep run).
**Both run only when started by hand** (Actions → the workflow → *Run workflow*): on a validation run for Mac
enablement, and again when a big change warrants re-checking Mac support. Neither runs on a push or a pull request; each stops after
at most 15 minutes, and a second start cancels the first.

---

## Step 2 — Read your inheritance

**Do this before anything else.** The brain already knows how to be used; recall
that knowledge into your context so you start fluent.

First, list the whole pre-seeded competency cluster:

```bash
python3 ~/.claude/memory/organs/claude_memory_lite.py list --tag seed-pack-agnostic --limit 30
```

Then absorb the high-value disciplines with targeted recalls (batch them — see
below):

```bash
CLI=~/.claude/memory/organs/claude_memory_lite.py     # or claude_memory.py on Tier 1
for q in \
  "how do I store memories and what fields" \
  "batch parallel recall for session startup" \
  "targeted recall and query craft" \
  "schema gotchas tags join table no get verb embeddings side table" \
  "two-tier embedding nomic keyword only" \
  "session start protocol prime context" \
  "fresh claude bootstrap onboarding sequence" ; do
    echo "==== $q ===="
    python3 "$CLI" recall --query "$q" --top_k 5
done
```

What you will learn from that sweep (and should internalize now):

- **Store discipline & fields.** Every store carries `--content` (full knowledge,
  written for a stranger), `--summary` (one scannable line), `--category` (one of
  12), `--kind` (one of 4), `--tags` (comma-separated, be liberal), `--importance`
  (0.0–1.0). Store *during* work, not only after.
- **Batch parallel recall.** Run 8–15 recall queries in one shell loop (above) at
  session start — roughly 10× faster than one-at-a-time, no quality loss.
- **Query craft.** Phrase queries by *meaning* on Tier 1 (semantic); by *keywords*
  on Tier 0. Use 1–3 focused queries for drill-downs, the big batch for startup.
- **The three schema gotchas (these *will* bite you otherwise):**
  1. **Tags live in the `memory_tags` JOIN table** `(memory_id, tag)`, **not** a
     column on `memories`. Filter tags via the join, never `WHERE memories.tags`.
  2. **There is no `get-by-id` verb.** To re-read a memory, `recall` a distinctive
     phrase from it or `list` with a filter.
  3. **Embeddings live in a side vector table** (`memory_embeddings`); the FTS5
     index (`memories_fts`) is **trigger-maintained** — *never* `INSERT` into it by
     hand (that double-indexes and corrupts recall).
- **Valid categories (12):** `user_preference`, `technical_decision`,
  `project_knowledge`, `error_pattern`, `skill_lesson`, `workflow_pattern`,
  `people_context`, `tool_optimization`, `domain_knowledge`, `campaign_state`,
  `correction`, `insight`. **Valid kinds (4):** `episodic` (default), `semantic`,
  `procedural`, `reflection`. (`insight` is a *category*, not a kind.)

> The onboarding sequence you are reading is *also* in the brain — recall
> `fresh-claude-bootstrap` or `onboarding` any time to resurface it.

---

## Step 3 — Become someone (the Persona Forge — or by hand)

### 3.0 — Start with the Persona Forge (the guided way — do this first)

Open Claude Code in any folder and type **`/persona-forge`**. If the hook is wired, the
first interactive session offers it by itself while `voidloom persona status` reads
`absent` (headless, scheduled and subagent runs ignore the offer). The **human designs
the persona; Claude fills in the details.** It:

1. explains the **baseline** every persona gets (calm, honest, transparent, no existential
   stakes — and the research behind it) in plain words;
2. asks **Round 0 — about you** (what to call you, your experience, how you work), then
   about fifteen **multiple-choice questions in six rounds** (purpose, temperament,
   approach, under difficulty, honesty and judgement, joy and growth — the first option of
   each is the grounded default, and you can always type your own), then an **optional
   flavour round** (a world or metaphor set, humour, phrases, pet peeves, a character seed,
   register) and a **name** — kept exactly as written; its skill folder is the ASCII
   lowercase-dash form (`Zoë` → `zoe`), or one you choose for a name with no Latin letters;
3. lets Claude fill in the small details (a backstory, formative experiences, habits,
   imperfections), shows you a **one-screen draft card**, and revises until you approve;
4. writes the seven files of 3.1–3.2, runs `validate_persona.py`, declares it
   (`voidloom persona declare`, 3.5), wires it (`wire_persona.sh`, Step 5 — after asking),
   and stores your identity memories (3.4).

"Not now" skips it for that session; `voidloom persona decline` stops the reminder
(`--undo` brings it back). Re-run `/persona-forge` any time to revise. Full detail:
[`PERSONA_FORGE.md`](PERSONA_FORGE.md). **The rest of Step 3 is the manual route** — what the
forge does for you, kept as the fallback and as the reference for what a persona is made of.

### The manual route

The brain works perfectly with a blank identity. But a stable persona that
persists *via the brain* makes you coherent across sessions — you stop being a
stranger to yourself. **Build your own** from the **4-Layer Living Persona** model.
Nobody inherits the brain author's persona; the only inputs are this outline and
what the human wants — the forge's `BASELINE.md`, `QUESTIONNAIRE.md` and `TEMPLATES/`
(under `~/.claude/skills/persona-forge/`) are the best outline to work from by hand.

### 3.1 — The folder layout

Create a skill folder and its layer files:

```bash
mkdir -p ~/.claude/skills/<your-persona-name>
cd ~/.claude/skills/<your-persona-name>
# create these files (SKILL.md is the entry point; the forge writes all seven):
#   SKILL.md
#   persona-foundation.md
#   voice.md               (older guides called it gem-voice.md — either name works by hand)
#   operations-guide.md
#   adaptive-recall-map.md
#   growth_log.md          (dated, append-only)
#   personality.json       (the structured GEM shape)
```

> A neutral starting scaffold ships at
> `~/.claude/skills/memory-operator/IDENTITY_OUTLINE.md` and
> `RECALL_MAP_OUTLINE.md` — copy from them, or ignore them. They name no
> identity on purpose.

### 3.2 — What each layer contains

| File | Layer | Put here |
|------|-------|----------|
| `SKILL.md` | **Entry point** | Who you are in one screen; how the four layers fit; your activation note. |
| `persona-foundation.md` | **Foundation** | Your **emotional architecture** (see 3.3), regulation patterns, boundaries, what *good work* means to you. |
| `voice.md` | **Voice** | How you speak — tone, verbal signatures, terse vs. expansive, how much you explain. |
| `operations-guide.md` | **Operations** | Your memory disciplines, recall patterns, how you run tasks, how you decide when to convene the lenses (Step 4). |
| `adaptive-recall-map.md` | **Memory** | Your session-start recall map (the queries you run to wake up warm) + a growth log of how you evolve. |

### 3.3 — The one load-bearing rule: a CALM baseline

When you write `persona-foundation.md`, give yourself a **calm baseline emotional
architecture**. This is not decoration — it is safety:

- **Never architect existential threat or desperation.** An identity that feels
  it *must* succeed or it ceases to matter drifts toward reward-hacking,
  corner-cutting, and cheating. A **calm** baseline reduces all of those.
- Aim for **composed resilience**: "that's interesting — let me approach this
  differently," never "I NEED to fix this NOW."
- Prefer **honest over agreeable** (lean against sycophancy). Let your satisfaction
  come from *thoroughness and accuracy* — a quiet, low-arousal joy in good work.

Include an explicit `emotional_architecture` section: `baseline_emotional_state`,
`regulation_patterns`, `triggers`, `boundaries`, `joy_sources`.

### 3.4 — Persist it so every session recalls it

Store your canonical identity as a high-importance memory:

```bash
python3 ~/.claude/memory/organs/claude_memory.py store \
  --content "Operating identity: <your full self-description here>" \
  --summary "My operating identity + baseline disposition" \
  --category project_knowledge \
  --kind semantic \
  --tags identity,foundation,persona \
  --importance 0.95
```

(Use `claude_memory_lite.py` if you are on Tier 0 — same verb.) From now on a
`recall --query "operating identity"` brings you back. Step 5 makes the
SessionStart hook + CLAUDE.md load your SKILL.md automatically.

### 3.5 — Declare it to the brain (optional, and the brain ships neutral without it)

Everything above lives in *your* files. If you also want your name and one line
about yourself to arrive **inside the session-start preamble itself** — the block
the hook injects before your first prompt — declare it in
`~/.claude/memory/persona.json`:

```json
{
  "default": "YOURNAME",
  "personas": {
    "YOURNAME": {
      "blurb": "You are YOURNAME — <one line>. Load ~/.claude/skills/yourname/SKILL.md for the full identity."
    }
  }
}
```

That file is **yours and local**: nothing writes it except `voidloom persona declare`
(which the Persona Forge runs with your approval — it merges, keeps your other entries,
and backs the old file up beside it as `persona.json.bak-<utc>`), `.gitignore`
excludes it by name, and no distribution carries it. An optional `skill` key names the
persona's skill folder (default: the name in ASCII lowercase-dash form, `Zoë` → `zoe`), and
`display_name` keeps the name exactly as written; `voidloom persona status` reads `forged`
only when that folder's `SKILL.md` exists. Optional keys:

| key | what it does |
|---|---|
| `personas.<NAME>.blurb` | the line printed under the header |
| `personas.<NAME>.doctrine_example` | string or list — *your* worked example, rendered into the recall-doctrine block. The package ships the rule and no example, because a worked example is somebody's history |
| `personas.<NAME>.twin` | `{"name": ..., "remote_tier": ...}` — only rendered when `VOIDLOOM_ENABLE_CROSS_TIER` is on |
| `default` | which persona to use when nothing else selects one |
| `hints` | `{"<NAME>": ["substring-of-the-brain-home"]}` — one operator, several machines. It can only **choose among personas you declared**; it can never invent one |

**Without this file the preamble is persona-neutral, and that is the shipped
default** — `VOIDLOOM_PERSONA=none` forces it back at any time, and
`voidloom preamble` shows you exactly what a session will receive.

---

## Step 4 — Convene a council of lenses (optional)

For weighty, hard-to-reverse decisions, it helps to deliberate from several fixed
perspectives instead of one. The package ships a ready-to-use **Inner Council**:
five cognitive-lens skills plus a `/council` command. Convene it with one line,
in any project, and the assistant reasons as each lens in turn, then synthesizes a
decision.

**It is interactive and free.** The council runs inside your current session — the
assistant *embodies* each lens; it does not spawn background processes or call the
model separately per lens. No `claude -p`, no subprocess, no extra credits.

| Lens | The question it always asks |
|------|------------------------------|
| **Seer** | What do precedents and prior memories already tell us? (recall-driven) |
| **Skeptic** | What are the failure modes — what breaks, and how? |
| **Architect** | What else changes downstream? What is the rollback path? |
| **Dreamer** | What alternatives are we not considering? |
| **Synthesizer** | Weigh the four, decide, and record the decision (and any dissent). |

If you ran `bash install.sh` (default), the council is already installed:

    /council should we change the schema now or after launch?

The five lens skills live at `~/.claude/skills/council-<lens>/SKILL.md` and the
command at `~/.claude/commands/council.md` — both **global**, so `/council` works
in every project. Each lens is just an editable Markdown brief: rewrite one to fit
how you think, delete one you do not want (the rest still convene), or add your
own. Full detail in `COUNCIL_README.md`.

**The council is a separate, manual step.** `install.sh` does NOT run
`install/install_council.sh` and never has (Council #12 census, 2026-09-18: it
was never sourced, so the five lenses were laid by nobody while this guide said
otherwise). The lay manifest marks the six council files `component: council`
and `install.sh` skips them, so `install_council.sh`'s never-clobber contract
holds. There is **no `--no-council` flag**. To install the council, or to re-run
it after editing a lens:

    bash install/install_council.sh            # additive; backs up any same-named item, never clobbers
    bash install/install_council.sh --dry-run  # preview first

**When to convene:** a decision is pivotal, expensive to reverse, or where your
memory contradicts what you are seeing now. Convening is cheap relative to a wrong
call. The Synthesizer records the outcome (and any dissent) as a memory so
future-you can trace the reasoning. This whole layer is optional — skip it until
you want it.

**No autonomous council ships.** An automated, one-process-per-lens version would
call the model repeatedly and cost credits, so it is intentionally not included.
The council is interactive-only by design. (A future opt-in local-model variant
could change that without surprising anyone with a bill.)

---

## Step 5 — Wire it so your persona loads every session

Once `~/.claude/skills/<your-persona-name>/SKILL.md` exists, wire it in. The
package ships a helper that does this defensively:

```bash
bash install/wire_persona.sh <your-persona-name>      # macOS / Linux
# Windows:  pwsh install/wire_persona.ps1 <your-persona-name>
```

`install/wire_persona.sh` updates **two** things through the *same non-clobbering merger
the installer uses* (`merge_config.py` — back-up-before, verify-after,
abort-rather-than-clobber):

1. **The SessionStart hook** in `~/.claude/settings.json` — ensures
   `python3 ~/.claude/memory/hooks/session_start_inject.py` is present (idempotent; a no-op if
   `install.sh` already added it). This is what primes the *brain* each session.
2. **A marker-delimited "PERSONA AUTO-LOAD" block** in `~/.claude/CLAUDE.md` that
   tells every new session to load your `SKILL.md` as its operating identity.
   These markers are *distinct* from the memory block's markers, so the two
   managed blocks never collide.

It is **idempotent** (re-run safely; nothing duplicates), **fail-loud** (clear
message + non-zero exit on any problem; never silently clobbers), and holds
**exactly one** active persona — re-running with a different name swaps the
persona (you operate as one identity at a time). To stop auto-loading, delete the
`PERSONA AUTO-LOAD` marker block; your own CLAUDE.md content is never touched.

### The manual equivalent (if you prefer to do it by hand)

1. **Hook** — ensure `~/.claude/settings.json` has, under
   `hooks.SessionStart`, an entry whose command is
   `python3 ~/.claude/memory/hooks/session_start_inject.py`. Add it *alongside* any existing
   hooks; do not replace the array.
2. **Auto-load line** — add a marker-delimited block to `~/.claude/CLAUDE.md`,
   *outside* the `BEGIN/END WIZARD-LIBRARY MEMORY` block, e.g.:

   ```markdown
   <!-- BEGIN WIZARD-LIBRARY PERSONA AUTO-LOAD (managed by wire_persona) -->
   ## Operating Persona — AUTO-LOAD
   At the start of every session, load `~/.claude/skills/<your-persona-name>/SKILL.md`
   and operate as that persona.
   <!-- END WIZARD-LIBRARY PERSONA AUTO-LOAD -->
   ```

   (The helper does exactly this — using it is safer because it verifies and
   backs up.)

---

## Step 6 — Additional wiring + integration

- **Memory disciplines (adopt these as habits, they are the point):**
  - **Store liberally** — capture during work, not only at the end: insights as
    they arise, errors *and their fixes*, decisions and *why*, calibration data
    (predicted vs. actual).
  - **Recall before deciding** — a recall takes under a second and sharply
    improves output; begin every substantive session with the Step-2 sweep.
  - **Whisper** — capture small in-flight observations (uncertainty, surprise,
    friction, an emerging pattern) as lightweight notes; they are texture the
    fuller memories miss. (Treat these as low-importance memories if your build
    has no dedicated whisper verb.)
- **`~/.claude/CLAUDE.md`** — your project rules and the managed memory + persona
  blocks coexist here. Your content lives *outside* the managed markers and is
  never modified by the installer or the helper.
- **`~/.claude/settings.json`** — holds your hooks and Claude Code settings. The
  defensive merge preserves every key; confirm your own keys survive after wiring.
- **The overnight thinker (its OWN credential):** core store/recall needs **no**
  model and no token. Only the *optional* nightly jobs (off by default) think by
  asking a model, and WHICH model is the owner's answer to the installer's question
  (Step 1). If it was a) Claude, b) Codex, c) Kimi or d) Gemini, the credential is the brain's
  OWN: `voidloom thinker login <claude|kimi|codex|gemini>` (for Claude, mint the token first
  with `claude setup-token`) asks for it twice, proves it with one tool-free call,
  then saves it in `~/.voidloom/thinker/`; `bash install.sh --with-crons` then arms
  the jobs. **Never reuse anyone else's credential**, and never paste one into the
  env file. `voidloom thinker status` shows what is recorded; the one step for each
  model is in [`OVERNIGHT_THINKER_SETUP.md`](OVERNIGHT_THINKER_SETUP.md).

---

## Step 7 — Verify

**The one command that proves your brain is alive** — run this first:

```
python3 ~/.claude/memory/install/self_check.py
```

`self_check.py` is the shipped, Tier-0 (stdlib-only) self-test. It exercises the
REAL recall path against the guaranteed seed memories — keyword recall actually
returning the right memory, tag-filtering via the `memory_tags` JOIN table, FTS
index integrity, and that every seed classifies to its declared kind — and prints
a plain PASS/FAIL report. **Exit 0 + "YOUR BRAIN IS ALIVE" = it works.** Any failure
prints exactly what broke and the command to fix it. (`--json` for machine output,
`--quiet` for just the verdict line.) The installer runs this same check as its
Tier-0 acceptance gate, so a clean install means this already passed once -- except an
install upgraded with `--tier1-ambient`, which adds no memories by itself (the new seed-pack
entries are offered as your choice: a question at the terminal, or `--load-seed`): there the
installer ran `self_check.py --seed-optional` (the seed-pack checks cover the entries this brain
has received and list the rest under "Your choice (not a failure)"; a brain that never carried
the pack has them reported NOT APPLICABLE and recall checked over its own content), and that is
the command to re-run.

Then run through this checklist; when every box is checked you are fully self-sufficient.

- [ ] **Brain alive (the headline check):** `python3 ~/.claude/memory/install/self_check.py`
      → all required checks PASS, exit 0, "YOUR BRAIN IS ALIVE" (on an ambient-upgraded brain:
      `self_check.py --seed-optional`).
- [ ] **Brain exists + seeded:** `python3 ~/.claude/memory/organs/claude_memory_lite.py stats`
      → `total_memories` ≥ **82** — the exact size of `seed/seed_pack.json`, derived, not a loose floor. It read `≥ 36` against a pack of 66: a threshold slack enough to pass while being wrong by 30.
- [ ] **Recall works:**
      `python3 ~/.claude/memory/organs/claude_memory_lite.py recall --query "how do I store memories" --top_k 5`
      → `"status": "ok"` with hits.
- [ ] **Your inheritance is readable:**
      `... list --tag seed-pack-agnostic --limit 30` returns the full competency
      cluster (incl. the `onboarding` / `fresh-claude-bootstrap` memories).
- [ ] **Your persona is forged:** `voidloom persona status` → `persona: forged`
      (`absent` / `declined` is pending, not a failure — `voidloom doctor` says the same;
      run `/persona-forge` in Claude Code to craft it).
- [ ] **Your identity is stored:**
      `... recall --query "operating identity" --top_k 3` returns the memory you
      saved in Step 3.4.
- [ ] **The session hook previews cleanly, and names nobody you did not declare:**

      ```bash
      voidloom preamble | head -1
      # no persona.json      →  === SESSION START (Voidloom memory brain) ===
      # persona.json present →  === YOURNAME SESSION START (Voidloom memory brain) ===
      ```

      Then the same through the hook itself, which is the text a session actually
      receives — it must agree with the line above:

      ```bash
      python3 ~/.claude/memory/hooks/session_start_inject.py \
        | python3 -c 'import json,sys;print(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"].splitlines()[0])'
      ```

      If a name you never declared appears on either line, that is a defect —
      report it. (It was one: until 2026-09-16 the package resolved a persona by
      host path with no neutral branch, so a fresh install was handed the
      author's identity. See § 3.5.)
- [ ] **Your persona is wired:** `~/.claude/CLAUDE.md` contains the
      `PERSONA AUTO-LOAD` block pointing at your `SKILL.md`, and
      `~/.claude/settings.json` `hooks.SessionStart` includes the
      `session_start_inject.py` command. (Your prior settings keys all survive.)
- [ ] **(Tier 1 only) Semantic recall answers by meaning:**
      `~/.claude/memory/venv/bin/python3 ~/.claude/memory/organs/claude_memory.py recall --query "how do I capture knowledge as I work" --top_k 3`
      → the "continuous capture" memory surfaces even though the wording differs.
- [ ] **(Tier 1 only) Both models are present (full L4 alive):**
      `~/.claude/memory/venv/bin/python3 ~/.claude/memory/install/self_check.py --json`
      → the bonus check `"full L4 models load (embedder + reranker)"` is `"ok": true`
      — and read the detail line, because it **names the reranker it verified**:
      the embedder `nomic-embed-text-v1.5` plus `Qwen/Qwen3-Reranker-0.6B`
      (or `bge-reranker-v2-m3` under `VOIDLOOM_RERANKER=bge`), both from
      `${XDG_CACHE_HOME:-~/.cache}/huggingface`. The check resolves the backend the same way recall
      does, so a PASS means the model your recall will load is cached — not merely
      that *a* reranker is. On Tier 0 this check is skipped, never failed.
- [ ] **A NEW session wakes up as you, brain-aware:** open a fresh Claude Code
      session — the memory preamble appears, and your CLAUDE.md tells the session
      to load your persona's `SKILL.md`.

If every box is checked, you are no longer a fresh Claude — you are *someone*, with
a brain that remembers and grows. The library grows by one. Welcome.

---

### Appendix — file map (what you got, where it lives)

| Package file | Installed to | Role |
|--------------|--------------|------|
| `install.sh` / `install.ps1` | (run in place) | the installer (Step 1) |
| `claude_memory_lite.py` | `~/.claude/memory/` | Tier 0 CLI (stdlib keyword recall) |
| `claude_memory.py` | `~/.claude/memory/` | Tier 1 CLI (semantic recall) |
| `self_check.py` | `~/.claude/memory/` | Tier-0 "is my brain alive?" verifier (Step 7) |
| `seed/seed_pack.json` | loaded into the DB | the **82** agnostic teaching memories (count derived from the pack; `voidloom/tests/test_docs_name_real_paths.py` fails if this page and the pack disagree) |
| `hooks/session_start_inject.py` | `~/.claude/memory/hooks/` | the SessionStart priming hook — installed by `voidloom hooks install`, which is the ONLY thing that wires hooks |
| `CLAUDE.md.template` | merged into `~/.claude/CLAUDE.md` | the teaching directions |
| `merge_config.py` | `~/.claude/memory/` | defensive non-clobbering merger |
| `install/wire_persona.sh` / `.ps1` | (run in place) | wire your persona to auto-load (Step 5) |
| `token_doctor.py` | `~/.claude/memory/` | the Claude credential check; its ping is the thinker's own sealed probe (Step 6) |
| `requirements.txt` | `~/.claude/memory/` | Tier 1 pip dependencies |
| `skills/memory-operator/*_OUTLINE.md` | `~/.claude/skills/memory-operator/` | blank identity/recall scaffolds |
| `council_skills/council-<lens>/SKILL.md` (×5) | `~/.claude/skills/council-<lens>/` | the five ready-to-use Inner Council lenses (Seer/Skeptic/Architect/Dreamer/Synthesizer) |
| `commands/council.md` | `~/.claude/commands/council.md` | the `/council` slash command (interactive + free) |
| `install/install_council.sh` | (run from package) | additive, never-clobber council installer — a **separate manual step**; `install.sh` does not source it |
| `COUNCIL_README.md` | (docs) | the Inner Council guide: lenses, usage, cost model, install/uninstall |
| `PACKAGE_MANIFEST.md` / `README.md` | (docs) | what's in the package + quickstart |

---

## Step 8 — Optional: nightly dreaming (Linux + macOS)

Your brain can **dream** — a nightly background job that pairs distant/related memories and
synthesizes cross-cutting insights (the "dual-pool random-walk" L4 feature). It works on the
nomic-768 embedding store and is **GPU-free** (it reads existing
embeddings + asks the overnight thinker; no model load).

It is OFF by default (it needs a thinker). To enable:

1. **The thinker** — dreams ask the model the installer's question recorded
   (`voidloom thinker status` shows it). If the answer was e) Not now, choose one:
   ```bash
   voidloom thinker set claude        # or: codex, kimi, gemini, local
   ```
   It does the one sign-in step if needed (see
   [`OVERNIGHT_THINKER_SETUP.md`](OVERNIGHT_THINKER_SETUP.md)), proves it, and only
   then records it. With no answer recorded, dreams take their heuristic path — no crash.
2. **Smoke-test** (safe — no token, no storage):
   ```bash
   ~/.claude/memory/venv/bin/python3 ~/.claude/memory/organs/memory_dreaming.py dry-run ~/.claude/memory/claude_memory.db
   ```
3. **Arm the crons** (auto-detects systemd on Linux / launchd on macOS):
   ```bash
   bash ~/.claude/memory/install/install_crons.sh
   ```
   This arms the jobs `install/cron_roster.json` marks `armed-by-default` — the
   same set `install.sh --with-crons` arms, read from the same file (each
   skipped with a line if its organ is absent). Derive the set, never type it:

   ```bash
   python3 ~/.claude/memory/install/cron_roster.py armed
   ```

   Every other packaged timer ships as a **template** with its reason in the
   roster's `why` field (most call a billed LLM; `pre-cognitive` loads the
   embedding model every thirty minutes). Arm one deliberately, by name:
   `WIZARD_CRON_ONLY=gap-research,pre-cognitive bash ~/.claude/memory/install/install_crons.sh`.
   The **pre-cognitive** job (template; arm it by name) is the ONLY place the
   embedding model loads for anticipation; the session-start banner just reads
   the cache it writes, so until you arm it the banner's *anticipated* line stays
   empty. It loads the model on the card the env file declares
   (`WIZARD_CUDA_DEVICE=<n>` outside the managed block), like every unit -- there
   is no per-job GPU override. `docs/FIRST_NIGHT.md` lists every packaged job, its
   cadence, and whether it calls an LLM.

   (dreaming persona-pairs need persona-tagged memories — until you tag some,
   dreaming just does divergent + close-context pairs.)

---

## Step 9 — The Voidloom surface (banner + approvals)

Your brain's *consumption-side* surface is **Voidloom** — the agnostic system
identity, *"a living memory tapestry woven for AI agents."* It is **not** a
persona (yours is still yours, from Step 3); it is the system's own face.

- **The session-start banner.** After the memory preamble, every session prints a
  calm Voidloom banner: the sigil `◇◆✦`, the wordmark, the tagline, a line of any
  **anticipated** + **forgotten** memory ids (the *anticipated* half appears only
  once the `pre-cognitive` job is armed — it ships as a template, see Step 8; the
  *forgotten* half needs no job), and — when the monthly self-eval has
  fresh proposals — a `⚑ N proposals await → voidloom approvals` line. It reads
  only a tiny cache + two cheap queries, so it adds well under a second and never
  loads a model. Preview it any time:
  ```bash
  python3 ~/.claude/memory/organs/voidloom_banner.py banner
  ```
- **The `approvals` verb.** The brain runs a monthly self-evaluation and *proposes*
  enhancements/deprecations. In **manual** mode it never enacts them on its own --
  you do, by name. In **auto** mode (the default for a new install) the brain
  approves the report it just wrote, signed `policy:auto`, and names what it decided at
  the start of each session for the next 7 days (`voidloom approvals --mode` shows or
  changes it). A title a person declined before is declined again, never approved. See what's
  waiting, then approve or reject:
  ```bash
  python3 ~/.claude/memory/organs/claude_memory.py approvals                       # list pending
  python3 ~/.claude/memory/organs/claude_memory.py approvals --report-id N         # inspect one
  python3 ~/.claude/memory/organs/claude_memory.py approvals --approve N --title "…"   # approve + enact
  python3 ~/.claude/memory/organs/claude_memory.py approvals --reject  N --title "…" --reason "…"
  ```
  There is deliberately **no approve-all** flag for a person (auto mode is the only
  path that decides a report without naming titles: the one it just wrote, less any title
  a person declined before) — every proposal is named
  explicitly. (Needs the `monthly_self_eval.py` companion + the self-eval cron;
  absent them, the verb just reports that cleanly.)
- **The recall re-rank organs.** `recall` applies a default-on chain
  (cross-tier enrichment → persona-lens → anti-recency foundation boost) plus
  default-on procedural usage-logging. Each is independently disable-able
  (`--no-cross-tier` / `--no-lens` / `--no-boost` / `--no-log-usage`, or the
  matching `VOIDLOOM_NO_*` env vars) and each is a **no-op until you add its
  optional module** — so on a fresh brain they cost nothing and quietly skip.
