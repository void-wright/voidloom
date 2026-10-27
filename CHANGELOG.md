# Changelog

All notable changes to Voidloom are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-10-27

The first public release, under the Apache License 2.0.

### Added

- **The brain.** One local SQLite database (`~/.claude/memory/claude_memory.db`)
  with full-text search (FTS5) and vector search (sqlite-vec), built from a base
  schema plus additive migrations. A local-filesystem guard refuses a brain on a
  network or cloud-synced path, and a read-only brain still answers reads.
- **Hybrid retrieval.** Vector and keyword search fused by reciprocal rank, then
  an optional reranker. Embedders: `Qwen/Qwen3-Embedding-0.6B` on a GPU
  machine, `nomic-ai/nomic-embed-text-v1.5` on a CPU-only one. Reranker:
  `Qwen/Qwen3-Reranker-0.6B` (with `BAAI/bge-reranker-v2-m3` as fallback), used
  only with a GPU-class accelerator and at least 15.0 GiB of RAM.
- **Tier 0.** A standard-library-only keyword engine
  (`organs/claude_memory_lite.py`) that works on any Python 3 and shares the
  same database and verbs; the CLI falls back to it when the semantic stack is
  absent.
- **The `voidloom` command.** 18 verbs, including `store`, `recall`,
  `batch-store`, `batch-recall`, `context`, `doctor`, `bootstrap`,
  `approvals`, `hooks`, `instructions`, `preamble`, `thinker`, `persona` and
  `dream`. Every verb can emit a structured JSON envelope, and a policy refusal
  has its own exit code (3).
- **Session integration.** A SessionStart hook for Claude Code and Codex
  (`voidloom hooks`), and an instruction block for tools without hooks
  (`voidloom instructions`), both previewing before they write.
- **A seed pack** of 82 memories, so a fresh brain already knows how to be used.
- **Installers.** `install.sh` for Linux (including WSL2) and macOS, and
  `install/install.ps1` for Windows. `install.sh` chooses the compute and labels
  how proven it is, checks the device against reference vectors before
  embedding, declares every path it may touch outside `~/.claude`
  (`--print-write-set`), previews with `--dry-run`, asks the owner's questions
  at a terminal and accepts them as flags otherwise (refusing, with nothing
  touched, when neither is available), can attach an existing brain in place
  behind a verified backup image, and finishes with a self-check.
- **An uninstaller,** `uninstall.sh`: a preview by default; `--yes` removes
  Voidloom but keeps your memories; `--yes --remove-brain` also deletes them
  after a typed confirmation.
- **Scheduled organs, off by default.** 18 packaged jobs (systemd user timers on
  Linux, launchd agents on macOS); `--with-crons` arms the nine marked
  armed-by-default: nightly consolidation, dreaming, anti-recency resurfacing,
  a weekly digest, skill graduation, gap mapping, a monthly self-evaluation, a
  run ledger and an embedding backfill. The rest ship as templates you arm by
  name.
- **The overnight thinker.** The owner chooses which model, if any, the
  scheduled jobs think with: Claude, Codex, Kimi, Gemini, a model on their own
  machine, or none. The brain gets its own credential in `~/.voidloom/thinker/`
  and each answer is proven before it is recorded.
- **The approvals queue.** The monthly self-evaluation proposes changes, decided
  automatically (each decision signed `policy:auto`) or by hand in
  `voidloom approvals --tui`. Belief links and governance holds always wait for
  a person.
- **Dreams.** Unverified leads the brain synthesises, shown beside recall
  results and always labelled as not a recorded fact.
- **The Persona Forge.** A guided `/persona-forge` conversation in Claude Code
  for designing the agent's persona; the package ships none.
- **A council of lenses** (optional): five perspective skills and a `/council`
  command, run inside the current session.
- **Documentation:** a getting-started walkthrough, a page on what the
  scheduled jobs do on the first night, model-by-model thinker setup, the
  Persona Forge, the repository layout, a restore manifest and a file
  inventory.
- **Tests and gates:** a pytest suite, a runner for the slower release gates,
  `tools/release_check.py` (GPU-pin and personal-data gates), a leak gate with
  git hooks and a CI workflow, a clean-room install workflow, and two Mac
  workflows started by hand.

### Proof labels

- **PROVEN:** Linux with an NVIDIA GPU, Linux on the CPU, and an Apple Silicon Mac with
  the GPU (MPS) or on the CPU (installed and used end to end on a real machine, after the
  macOS arm64 CI probe ran green). **SUPPORTED-UNPROVEN:** an Intel Mac, AMD (ROCm) and
  Intel (XPU) GPUs on Linux, and Windows.

### Fixed (found while preparing this release)

- `recall --top-k K` now returns the K most relevant memories. The diversity step placed its
  results in "lost-in-the-middle" order (strongest at both ends) *before* the list was cut to
  `--top-k`, so every answer kept ranks 1, 3, 5, ... and silently dropped 2, 4, 6, ... — the
  second-best result included. The cut now happens first; placement is applied to the final
  list. Regression test: `voidloom/tests/test_recall_topk_after_placement.py`.
- A fresh clone installs with plain `bash install.sh`. With no brain on the machine and none
  bundled, the installer used to stop with "no brain snapshot found" after laying its files;
  it now starts a new brain (the same as `--init-blank`) and says so.
- `install.sh --help` named the wrong default embedding model for a GPU machine; it now names
  the one the code chooses (`qwen`, 1024 dimensions).
- On a machine whose `python3` cannot load `sqlite_vec` (a stock Linux Python), the brain is
  first built keyword-only and the vector tables are skipped; the installer now adds them with
  the semantic environment's own Python once that environment exists. Before, the brain lacked
  them and the self-check failed "schema complete" when run through that environment.
- The self-check no longer reports a missing reranker as a failure on machines where the
  installer correctly decides not to use one (CPU-only, or under 15 GiB of RAM).
- Several tests assumed the maintainer's machine (an NVIDIA driver, a working bubblewrap
  sandbox, a CUDA card); they now skip with the reason where those are absent.

### Known issues

- `voidloom capabilities` exits with `no_organs` on a keyword-only (`--tier0-only`)
  install, although the session preamble mentions it.
- `--tier0-only` on a machine with two or more NVIDIA GPUs still asks the GPU
  questions (or needs `--display-gpu/--gpu` without a terminal).
- `install.sh --help` says `--accelerator` does not change which torch wheel is
  installed; on Linux CPU, AMD and Intel machines it does (it picks PyTorch's own
  index for that backend, as the README's network table says).
- The built-in self-test in `organs/memory_dreaming.py` predates a schema column and
  fails on its own scratch database; the pytest suite does not depend on it.

### Notes

- The public history starts with this release. Earlier development happened
  privately and is not part of this repository.
- Some schema fields and built-in labels carry names from the system's first
  installation; [`NOTICE`](./NOTICE) explains why they are kept.

[1.0.0]: https://github.com/void-wright/voidloom/releases/tag/v1.0.0
