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
- **The nightly centroid is stamped.** Consolidation averages member vectors, so no embedder
  stamps the result: it wrote that vector with no provenance. It now reads through the same search
  set as the other vector readers and stamps the row as a centroid, naming its members and a
  checksum over their vectors. On a locked brain every member must carry the lock's checksum; a
  cluster that does not is refused and rolled back whole, and the night goes on. Rows written
  before the lock (`unresolved`, or unstamped) are searched, but they do not enter a centroid: on
  a pinned brain they wait for `re-embed`, and the sleep log says how many are waiting. A brain with
  no lock keeps writing, never a refusal: the checksum the members agree on, otherwise `unresolved`.
- Migrations 042–045: provenance stamps can name the weights checksum; the re-embed log states
  its model and width; seed and origin columns on memories; derived-layer stamps.
- **`COMPAT.json`**, at the top of the package: what this package produces and accepts, so a
  program that installs it can refuse a mismatch before writing anything. It names the version,
  the migrations and the sha256 of a fresh brain's normalised schema, the oldest schema it can
  migrate (a brain carrying only `migrations/schema.sql`), the Python range (`>=3.10`, the
  installer's own floor; no ceiling is declared), the CLI surface, the installers' option
  surface (`install_verb_contract`, a digest of every option `install.sh` and `install.ps1`
  accept), and the scheduled units of `install/cron_roster.json`. It names no commit (a file
  cannot hold the hash of the commit it sits in) and does not describe what an install lays: a
  program that installs this package names the commit it pinned and measures its own install.
  `tools/build_compat.py --emit` derives it and `--check` compares; the test suite rebuilds a
  fresh brain and checks the schema hash every run.
- **`voidloom --version --json`** prints the version and `cli_surface_version`, a digest of
  every verb and option the CLI accepts; `COMPAT.json` carries the same value. Plain
  `voidloom --version` is unchanged.
- **`voidloom status`, `voidloom inspect <id>`, `voidloom export --ids 1,2,3`.** Read-only, and none
  of them loads a model. `status` shows the lock, how many memories a search can reach, the derived
  layer and the seed numbers on one page. `export --all` is refused, exit 3 `EXPORT_ALL_REFUSED`,
  naming what a full copy would still need.
- **The derived layer says which model it came from.** Link strengths, community membership and
  community summaries are stamped with the lock's checksum, and `doctor`, `status` and `stats` report
  how many rows are stale, unstamped and current. A link a person asserts stays unstamped, and rows
  written earlier stay unstamped. `doctor` and `status` also say how many episodic memories wait
  outside consolidation until they are re-embedded.
- **`load-seed --limit N`** loads part of a pack and can be resumed.
- **The install runs the seed self-test** after the seed embeds, and prints its number.

### Fixed

- **Sidecar files are no longer found by scanning `$HOME`.** The env file, the JSON config and
  the credential files were looked up in every hidden directory under your home folder, and the
  first one holding a file of that name was used. Two installs under one user account could
  read each other's credentials that way. The order is now `VOIDLOOM_ENV_DIR`, then the
  directory of `WIZARD_ENV_FILE` (the env file the installer wrote), then `~/.voidloom`. The
  scan runs only with `VOIDLOOM_LEGACY_DISCOVERY=1`, and even then it uses a directory only if
  it holds a `.voidloom-owner` marker naming this install (`python3 organs/voidloom_env.py
  --mark <dir>` writes one). A named directory that carries ANOTHER install's marker is
  refused (`ENV_DIR_FOREIGN`); an unmarked one is used as before. **If your sidecars live in an
  older, differently named directory,** set `VOIDLOOM_ENV_DIR` or `WIZARD_ENV_FILE` to it, or
  opt in and mark it.
- **Upgrading: an install whose env directory was found only by the old hidden-`$HOME` scan
  must now say where it is.** Set `WIZARD_ENV_FILE` or `VOIDLOOM_ENV_DIR`, or mark the directory
  with `python3 organs/voidloom_env.py --mark <dir>` and set `VOIDLOOM_LEGACY_DISCOVERY=1`.
  Until then its sidecars are not found. Nothing changes for an env directory at
  `~/.voidloom`.
- **The installer marks the sidecar directory it creates, and only that one**, so a stranger
  never marks anything by hand. The marker names the brain directory. A directory that was
  already there is never marked, so every install made before this stays unmarked and
  unprotected until its directory is created anew. An install meeting a directory marked for
  another install refuses before it writes anything (`ENV_DIR_FOREIGN`, exit 2), and says how to
  use a directory of its own. `uninstall.sh` removes the marker if it names this install and
  keeps the directory, so a later install elsewhere is not refused; a directory released this
  way stays unmarked. A marker is a label between installs, not a lock: another process of the
  same user can copy it. Code run from a checkout is not the installed brain, so it refuses a
  marked directory too: point `VOIDLOOM_ENV_DIR` at a directory of its own, or for one command
  set `VOIDLOOM_INSTALL_ROOT=<brain directory>`. The test suite does the first by itself.
- **`voidloom recall` and `store` keep the engine's refusals.** With the model's files missing
  they printed `unhandled` and lost the fix. They now answer from keyword recall, stamped
  `degraded` with the reason; an integrity refusal such as `WEIGHTS_MISMATCH` returns its own
  code and gets no keyword answer.
- **One reader of the Hugging Face cache location.** It now follows the library's own order
  (`HF_HUB_CACHE`, `HUGGINGFACE_HUB_CACHE`, `HF_HOME`, `XDG_CACHE_HOME`, `~/.cache`), which it did
  not for `XDG_CACHE_HOME`. The provenance stamp's model reference, the reranker's offline check
  and `voidloom bootstrap`'s model census each read their own subset and could look in a
  different cache from the one the models load from. The census now reports that one cache.
- **`install.ps1` writes its env file where the organs look.** Its default was
  `.wizard-library`, which the organs found only by the `$HOME` scan. A new Windows install now
  uses `.voidloom`; one that already has the older file keeps it, and the installer says to set
  `WIZARD_ENV_FILE`.
- **The installer reads the brain's embedding table from the install lock first**, at every
  site that used to read the metadata row directly: the tier gate and post-condition of an
  ambient upgrade, the install record, the brain sanity check, Step C's declaration and the
  Tier-1 verify gate. A lock that cannot be used is reported by its code and is never replaced
  by the metadata row. One function does the reading (`organs/embed_resolver.py`,
  `declared_table`).
- **`install.ps1` stamps the vectors it writes.** Its post-install embed of the seed pack
  inserted vectors with no provenance stamp, and it imported the engine from the wrong
  directory. It now uses `organs/embed_backfill.py`, the same writer `install.sh` uses.
- Measuring coverage took 13 to 14 seconds on a brain with 28,063 vectors, because each count
  looked up the vector table once per row. The same counts now take about 0.1 seconds.
- Six more queries looked up the vector table once per memory: the backfill count and its row
  query, `seed-shape`, `tier set`'s pending set and switch coverage, and `tier rollback`'s gap.
  On a 31,904-memory brain they took 4.2 to 12.8 seconds; they now read the same values in
  0.016 to 0.11 seconds.
- `claude_memory_lite.py stats` reported "not present (Tier 0 / keyword-only)" on brains that
  hold vectors: it counted the nomic table whatever the tier, and its stdlib connection cannot
  read a vector table at all. It now counts the brain's own table (the install lock first) and
  names it. The self-check's "nothing to verify" probe counts the same way.
- The belief engine read its neighbours' vectors from the jina table by name, whatever model the
  brain used, so on a nomic or qwen brain its similarity gate compared vectors from another
  model's table. It now reads through the search set.
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
- **A hard delete now finishes, and the memory's text goes with it.** Under
  `VOIDLOOM_ALLOW_HARD_DELETE`, `claude_memory.py delete --hard` removes the memory, every tier's vector and every
  stamp, then settles everything that names it in the same transaction: log and lineage rows go, and
  the three rows others hang from (a monthly report, an enacted proposal, a whisper digest) stay with
  the reference cleared. Copies of the text are replaced by `[removed]` wherever a run of 47 characters
  or more, or the whole text from 12 characters up, is found, in any table and in the memories written
  from this one. The keyword index is merged and freed pages are zeroed, so the words leave the file.
  The receipt prints counts per table and no content. **What it does not reach:** a separate memory
  that says the same thing is counted in `other_memories_sharing_text` and left alone; words a model
  reworded are not a copy it can see; a consolidated memory's vector still averages the deleted one
  until its next rebuild; and backups are yours to handle. Without the variable nothing changes: a
  soft archive. `seed delete-one` follows the same rule under the variable, and without it still
  refuses `HARD_DELETE_REFERENCED`.
- **A deleted lock no longer reads healthy.** Every lock write leaves a receipt beside it, so a
  missing lock with such a receipt means the brain was locked: `doctor`, `status` and the reader report
  `LOCK_MISSING`, name the tier and the command that writes the lock again, and nothing searches the
  whole table in its place. No receipt and no lock stays a legal older brain, as does a brain a
  `tier rollback` took back to having none. A lock that exists and cannot be read refuses
  `LOCK_MALFORMED` instead of reading as absent.
- **A read never reaches the network.** A recall on a machine without the model's files used to
  download them at the pin. Query encoding now runs with the hub closed, so it refuses `SNAPSHOT_ABSENT`
  with the command that fetches them, and the front door answers by keyword and says why. Recall no
  longer fetches a reranker either, and a recall with none says so in `coverage.reranker`. `install`,
  `tier set`, `re-embed` and a store still fetch.
- **`voidloom doctor` judges the brain by what the search reads.** Coverage is matched/total by stamp,
  not a row count, and `reason` names what failed. `host_healthy` answers for the hook settings
  separately, so a bad settings file no longer reads as a sick brain. A refused lock or stamps under
  another model read unhealthy. A brain with no lock stays healthy.
- **`models_present` answers for this brain's model,** not any cached tier, and `onboarded` follows it.
- **The hardware detector names Qwen3-Embedding-0.6B on GPU hosts.** Its block's keys are now
  `install_time_embedding_choice` and `install_time_tier_map`.
- **`init` adds `skill_tier`,** so the weekly skill-graduation job runs on a clean install, and both
  installers add it to a brain built at Tier 0, which never reaches `init`.
- **Both installers stop on a typed `init` refusal or a seed pack that fails its scrub,** and show the
  cause. The Tier-1 check counts the vectors a search will actually use.
- **Nightly dedup compares only vectors in the search set.** A refusal skips dedup and is reported in
  `dedup_refused`; the rest of `maintain` still runs.
- **Keyword recall at Tier 0 carries coverage,** and `recall --bm25-only` needs no embedding model.
- **`update` reports `re_embedded` from the stamp it wrote.**
- **`stats` reports matched of total, verified and legacy, and the derived layer.** On a brain with no
  lock it now counts active rows only, so its figure can drop by your archived rows.
- **On a brain with no lock, coverage counts what a search can reach,** not stamped rows, so the figure
  on every answer rises.
- **A write through a connection to another brain embeds under that brain's model,** not the one the
  process started on.
- **A consolidated memory keeps its centroid** through `re-embed`, `tier set` and the backfill, and the
  pending count no longer treats it as embedding work: a text vector put it in the top 10 for 62 of 240
  member queries, the centroid for 235.
- **A community summary left stale by a model change regenerates on the next run.**
- **`embedding_upgrade.py re-embed` on an older nomic or qwen brain uses that brain's own tier,** not
  jina. `embedding_upgrade.py` exits non-zero on failure, reports coverage in `stats`, and its `test`
  no longer needs jina; `full` refuses on a brain that is not jina.
- **`tier set` and `re-embed` get through an out-of-memory batch.** A memory too long for the device is
  embedded from its head, and the receipt counts it.
- **`embed_backfill` counts stamps on a locked brain,** so it agrees with `re-embed`.
- **The default reranker's bytes are verified at load.**
- **`website_memory` stamps its vectors and reads through a search set.**
- **Two locked-brain refusals read `STAMP_INCONSISTENT`** where they read as something else.
- **`sleep_loop.py --unit-tests` forces offline mode** and counts what it could not run as `refused`.
- **Everything that writes or reads systemd units follows `XDG_CONFIG_HOME`:** the run ledger, the yield
  sentinel, `cron/setup-*.sh`, `cron/platform-detection.sh` and `tools/gen_cron_audit.py`. The sentinel
  stops watching an organ that does not ship.

### Changed

- `memory_write.provenance_filter` returns a `ProvenanceFilter` object (it returned a
  `(table, model, dim)` tuple), and `memory_write` gains the reader the vector readers can take:
  `search_set`, `read_vector`, `read_vectors`, `knn`, `coverage_envelope`, and one count of the
  memories still to embed (`pending_embeddings`). On a brain with an install lock the searchable
  set is the vectors in the lock's table whose provenance stamp carries the lock's checksum, plus
  the brain's **legacy** vectors: those written before the lock existed, whose stamp reads
  `unresolved` or which have no stamp. A vector stamped under a different checksum, or kept in
  another model's table, is never searched. Every answer carries `legacy_unverified: N of M` (how
  many of the M searchable vectors have not been checked against the lock) and, when N is above
  zero, the command that checks them: `python3 organs/embedding_upgrade.py --db-path
  <brain>/claude_memory.db re-embed`, or `voidloom tier set` with the tier the brain already
  uses. This matters for a brain that `init` pins after it already holds vectors: without the
  legacy rule its vector recall would have gone empty until a re-embed. A brain with no lock is
  searched as before, whole. Each answer names which rule it used (`search_rule`).
- The vector readers outside recall now take their vectors from that same search set: the
  dream cycle (novelty, the eligible pool, divergent pairs), the diversity re-rank, the belief
  engine's neighbour scan, temporal edges (Tier-3 candidates and the LLM candidate search), the
  anticipation fallback, and the three memory-dreaming pair selectors. On a locked brain none of
  them can read a vector stamped under another model's checksum.
- Every recall answer now carries `coverage`: the reader's figures for that call (`basis`,
  `search_rule`, `matched` of `total` active memories and, on a locked brain,
  `legacy_unverified`), plus `vector_arm` (`ran`, `refused`, `off` or `empty`) and `degraded`. It
  is on the `recall` envelope, on each `batch-recall` record (an empty one too) and on
  `anticipate`. When the vector arm cannot run (the lock cannot be read, its table is gone, or
  nothing in it can be searched), recall answers from keywords with `degraded: true` and the
  refusal's code instead of failing or looking normal. In Python, `recall_hybrid_with_coverage`
  returns `(rows, coverage)`; `recall_hybrid` still returns the rows, each carrying `coverage`.
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
- **The test suite is no longer copied into a user's brain directory.** A brain laid before this
  change keeps its old `voidloom/tests/` folder after an upgrade, and `uninstall.sh` works from the new
  manifest so it will not remove it. It is safe to delete: `rm -rf <brain>/voidloom/tests`.
- The README documents `NO_ROWS_FOR_LOCK`, the `WIZARD_EMBED_MODEL` rule and the `re-embed` route.

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
