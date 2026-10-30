# Changelog

All notable changes to Voidloom are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `WIZARD_GAP_RESEARCH_FOCUS` (in the env file the scheduled jobs read) points gap research at
  your own field; the default stays generic. A `research_focus` key in a `--config` file wins
  over it.
- **An install lock.** `claude_memory.py init` writes `embedder.lock.json` beside the brain. It
  names the embedder (model, revision, its remote-code repository's revision, a checksum of the
  bytes, width and table) from the shipped catalogue `organs/embedders.lock.json`. The model is
  loaded only after its files on disk match that checksum, and every new vector's provenance
  stamp records the checksum the loaded model computed. Brains installed before this keep
  working: they are read against the catalogue entry for the tier they already use, and their
  older stamps read `unresolved` until re-embedded. A tier the catalogue does not ship (jina)
  gets no lock and works as before.
- **`voidloom tier set <nomic|qwen>`** moves a brain to another embedding model by re-embedding
  every memory under it while the old model keeps serving recall. It switches only when every
  memory has a vector under the new model, replacing the lock in one step and writing a
  receipt to `embedder.lock.history.jsonl`. `--plan` counts the work first and changes nothing.
  The old vectors are kept. **To roll back,** run `voidloom tier rollback`: it restores the
  previous lock and table and reports how many memories written after the switch have no
  vector there. When the old tier is shipped, `voidloom tier set <old tier>` fills that gap,
  reusing every vector already present; for a jina brain the rollback is the only way back.
  `voidloom tier cleanup --confirm <old table>` deletes the old vectors, writes its own receipt,
  and ends the rollback. A late write during the switch is embedded inside it, at most 32
  memories per attempt and 10 attempts (`FLIP_GAP_CAP`, `FLIP_ATTEMPTS` in `organs/tier_set.py`).
  **Both bounds are invented, not tuned**: nothing was measured to choose them.
- Migrations 042–045: provenance stamps can name the weights checksum; the re-embed log states
  its model and width; seed and origin columns on memories; derived-layer stamps.

### Fixed

- Keyword recall now matches any content word of a query, ranked by BM25. It used to require
  every word ("when" and "did" included), so a natural question almost never found a keyword
  match: 2 of 152 benchmark questions on one test conversation. "Hybrid" recall was in
  practice vectors plus the reranker. Common English function words are left out of the
  match, and a query made only of them still searches.
- The keyword-only engine (Tier 0) no longer fails on a question mark or an apostrophe.
  Those raised an FTS5 syntax error, the fallback then searched for the whole question as one
  phrase, and a natural question found nothing.
- The match uses at most the first 32 content words, so a pasted page costs about what a
  question does instead of seconds.
- Two signals change as a result, and neither is retuned here.
  - **The diversity cap.** It widens the result pool when the keyword signal is weak, and now
    sees a keyword score for questions. That score usually clears the cap's "strong match"
    threshold, so for most questions the cap now equals top-k instead of twice top-k: on one
    test set, 393 of 446 questions at k=10, where 440 of 446 sat at 2 × k before. Its
    thresholds need recalibrating for any-word matching.
  - **The lexical-miss log.** It suggests when a sparse lane might help, and now counts a miss
    only when no content word of the query appears anywhere in the brain. Such misses are
    rare, so the suggestion will seldom fire. Its rolling window moves to the new meaning
    within 500 recalls.
- `voidloom --help` no longer imports the approvals backend, the overnight thinker or numpy to
  print the verb list. The approval-ref pattern is still taken from the module that owns the
  vocabulary, on first use. Measured on the development machine: median 196 ms before, 46 ms
  after (20 runs each, back to back).
- An install lock that names a vector table this package does not build, a shipped table at
  another width, or another tier's table is refused as `LOCK_MALFORMED` when it is read, for
  every reader. Before, a lock naming the jina table under the cpu tier was accepted, and the
  engine then selected the jina model.
- A brain the installer creates is readable by its owner only (mode 600), and so are its
  `-wal`/`-shm` files, whatever the umask. It was 644 under the default umask and under umask
  000. A brain that was already in place keeps the mode its owner gave it; a mode looser than
  600 is reported with the command that restricts it.

### Changed

- `memory_write.provenance_filter` returns a `ProvenanceFilter` object (it returned a
  `(table, model, dim)` tuple), and `memory_write` gains the reader the vector readers can take:
  `search_set`, `read_vector`, `read_vectors`, `knn`, `coverage_envelope`, and one count of the
  memories still to embed (`pending_embeddings`). On a brain with an install lock the searchable
  set is the vectors whose provenance stamp carries the lock's checksum; vectors without one are
  left out and the reader says how many. A brain with no lock is searched as before, whole. Each
  answer names which of the two it used.
- The GPU tier's two models, Qwen3-Embedding-0.6B and Qwen3-Reranker-0.6B, now load a pinned
  HuggingFace commit, as nomic and jina already did. Results no longer depend on when the
  model was downloaded, and a warm cache loads offline. A test fails if either pin is removed.
- **Qwen3-Embedding now loads only from files already on disk.** Its bytes are checked against
  the install lock first, and the catalogue's pin and the engine's pin must agree. Fetch it
  before first use with `python3 organs/model_fetch.py --db <brain>/claude_memory.db`, or let
  `voidloom tier set qwen` fetch it at the pin while online.
- **The installers no longer pre-cache the unpinned BGE reranker or the fallback reranker.**
  Only a reranker the catalogue pins is downloaded, at its revision, and checked. The fallback
  loads only from a cache you fill yourself.
- The installers write the lock before pre-caching, then fetch exactly the files it names (the
  Windows installer now does too). The self-check verifies the embedder by checksum and loads
  nothing.
- Every component that chooses a vector table reads the install lock first and the metadata
  rows only for a brain without one: the dream pipeline (which used to send a qwen brain to
  the nomic table), the Tier-1 probe, `voidloom doctor` and `embedding_upgrade.py stats`. If
  `tier set` is interrupted after the lock moved, `voidloom doctor` says so and names the
  command that finishes it. When the lock cannot be read, recall's vector arm and the diversity
  re-rank no longer fall back to the jina table: the vector arm is off for that call (keyword
  recall still answers) and the re-rank drops its signal.
- `organs/website_memory.py` and the whisper network's fallback encoder load nomic at the
  catalogue's pinned model and code revisions, checksum-verified, instead of by name.
- `embedding_upgrade.py re-embed` on a locked brain re-embeds under that brain's own lock.

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
