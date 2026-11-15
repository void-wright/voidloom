# Changelog

All notable changes to Voidloom are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.3] — 2026-11-15

### Changed

- **A missing embedding runtime is reported, typed, at the top of every surface.** When the interpreter can't
  import what runs the verified model bytes, the loader refuses `EMBEDDER_UNAVAILABLE`.
  - `recall` and `anticipate` degrade at the top (`status: ok`, `degraded: true`, answered by keyword).
  - `doctor` and `status` no longer read healthy.
  - `status` and `inspect` name a lost lock or a dropped vector table at the top level.
  - Every failure that reaches the engine's callers carries a typed code.
- **A locked brain whose embedder can't load refuses to store**, typed, and writes nothing. Before, `store`
  and `batch-store` answered ok while writing a memory with no vector. A keyword-only (Tier-0) brain keeps
  storing.
- **A dream night with a failed phase reads `degraded`.** The summary carries `degraded: true` and names the
  failed phases in `degraded_phases`, and both dream entry points exit 1 so a scheduler records it.
  - A declined thinker (`unconfigured`) and a dry run read as before.
  - A starved night with a failed phase reads degraded.
- **On a host with two or more GPUs, nothing declared, and no card reporting an active display** (for example,
  a monitor asleep), no card is chosen and `doctor` warns, naming `WIZARD_CUDA_DEVICE` and
  `WIZARD_DISPLAY_INDEX`. Before, the fallback took card 0, which may drive the display. A one-card host and
  a host that declares its cards are unchanged.

### Fixed

- **A missing lock file is never read as a brain that never had one, at install time too.**
  - `brain_is_new.py`, `install.sh`'s model reader and the bootstrap's model detector first ask the record a
    lock leaves behind.
  - A lost lock keeps the tier its record names.
  - One whose record names none reads `LOCK_MISSING`, which both installers refuse by name.
- **A lock written without the brain's record of it is answered, typed.**
  - `init` says `degraded: true` with the code `init_degraded`.
  - A `tier set` whose record can't be written is refused inside its transaction.
  - Running the verb again when the brain is free writes only the record.
- **The installer's self-check never loads an embedder whose bytes weren't checked.** On a brain with no lock,
  a model the shipped catalogue carries is verified against its checksum; one it doesn't carry is refused
  `LOCK_NOT_SHIPPED`.
- **An orphan lock stamp no longer makes a table the lock's own.** A stamp counts only when its memory holds
  a vector in that table.
- **A brain whose tier can't be read** answers `TIER_UNRESOLVED`, not a lost lock.
- **The readers that age the brain's UTC rows use the brain's clock:** the heatmap window, the recency
  boost, procedural memory, skill graduation, the dream digest, the assayer and the engine's maintenance
  decay.
- **The engine hands the embedder each text exactly as `sentence-transformers` 5.1.2 tokenizes it**
  (edge whitespace stripped, batches grouped as 5.1.2 groups them). Vectors are bit-identical under the
  pinned 5.1.2, and a later library version can't move them through that difference.
- **The `memory-operator` skill has a conforming `SKILL.md`**, and a test holds every shipped skill folder
  to the same standard.

### Known issues

- The assayer still writes its `scored_at` stamp in local time.
- `inspect` stays ok when a brain's only vectored rows are archived.
- The run ledger's own unit still records its runs as `ended-unobserved`. systemd resets a service's result
  when it starts, so a run cannot read its predecessor's outcome from the inside, and the ledger never guesses
  a success.
- `sentence-transformers` stays at 5.1.2. See the 1.1.2 Security note: the advisory's listed fix (5.6.0)
  only warns, and the real fix is 6.0.

## [1.1.2] — 2026-11-14

### Changed

- **`export` checks each record's text against the stamp its vector was embedded from.** Each record
  carries `text_matches_stamp` (`true`, `false`, or `null` when there is no text stamp to check), and the
  envelope carries `text_check` with the counts and the ids that do not match. A dream is checked against
  the text its vector is computed from, its body without the header. When any record does not match,
  `status` stays `ok` but the envelope adds `degraded: true` and the code `export_text_unverified`
  instead of `export_ok`.

### Fixed

- **A lost install lock is no longer read as a brain that never had one.** When `embedder.lock.json` was
  written and is now gone, every reader refuses with `LOCK_MISSING` and the way out, including `tier status`,
  `tier set --plan`, `dream backfill --plan`, `embedding_upgrade stats`, writes with the caller's own
  encoder, and both installers. The nightly backfill and the dream cycle exit 1 on a lost lock, so a
  scheduler does not record the night as a success. A plain `init` on a lost lock refuses and names
  `init --tier cpu`. A brain that never had a lock reads exactly as before.
- **A lock re-pointed at another tier's table finds no rows and refuses**, instead of serving that table's
  unstamped vectors as its own legacy. An unstamped vector now counts as legacy only in the lock's own
  table, or on a brain that holds no stamp under another checksum.
- The hint beside a legacy-vector count says only what the brain recorded: the vectors carry no stamp
  under this lock.

### Security

- **`sentence-transformers` stays at 5.1.2** in this release, although advisory
  [GHSA-jhr6-gm9c-rqjv](https://github.com/advisories/GHSA-jhr6-gm9c-rqjv) names 5.6.0 as patched:
  - **5.6.0 does not close the hole.** It only warns when it loads a local model's custom code; the refusal
    arrives in 6.0.
  - **Moving to 5.6.0 changes vectors.** It stops trimming whitespace at the edges of input text, so the
    Qwen tier's vectors move for those inputs (1 of 30 recalls changed in testing).
  - **It loosens the `transformers` ceiling**, so a fresh install would pull a release under which the
    nomic tier no longer loads.
  - **What limits the exposure:** the embedding models load from pinned revisions whose files are checked
    against the shipped checksums before loading, and the model garden loads from the local cache only.
    Don't point Voidloom at model folders you did not fetch yourself.

### Known issues

- A few install-time readers (`install.sh`'s embed-model probe and `install/brain_is_new.py`) still read a
  missing lock file as "never locked". `init`'s weights check stops a wrong model from being written.
- A non-lock refusal inside one dream phase can still end a night reported as ok.

## [1.1.1] — 2026-11-14

### Changed

- **A degraded recall says so at the top of its answer.** When the vector arm is out and only
  keywords answered, `recall` and `anticipate` keep `status: ok` but add `degraded: true` and the code
  `recall_degraded` / `anticipate_degraded`; `voidloom recall` answers `recall_degraded` instead of
  `recall_ok`, and a `batch-recall` envelope answers `batch_recall_degraded` when any item was answered
  by words alone. A script that checks `status` keeps working; one that matches `code == "recall_ok"`
  will now see the degraded code on those answers. On those answers `degraded` is now `true`, and the
  details that the 1.1.0 `degraded` block carried (reason, meaning, way back) are under `degradation`.
  A read-only brain still reports `degraded: "readonly_db"`, as before.
- **What the policy decided is shown where you look.** In auto mode nothing waits in the queue, so every
  approvals face used to read empty while the policy approved whole self-eval reports, and the only
  notice expired after 7 days. `voidloom approvals --list`, `--present` and the TUI now carry a "decided
  by the policy" section for 30 days, and the session line uses the same window.
- **An approved self-eval proposal is open work.** `approvals --list` shows enacted titles as approved,
  awaiting work, with every report they were enacted on, and the policy no longer enacts the same title
  again on a later report.
- **RAPTOR keeps one synthesis node per group** and updates it in place, instead of writing a new node
  every run. Its own nodes are never members of a group, a node is embedded when it is written, and the
  summary reads the newest members. Nodes written by earlier versions are left as they are.
- **Community detection keeps the newest seven epochs** and deletes older epochs, their communities
  and their summaries in one savepoint. On a brain that has run nightly for months, the first run
  deletes most of those rows. It does not run `VACUUM`, so the file shrinks only when you vacuum it.
- **The organ run ledger has two new results:** `running` for a run seen while still going, and
  `ended-unobserved` for one that ended before a later look could read its result. Neither counts as
  a failure.

### Fixed

- **A machine that loses its compute card no longer computes on its display card.** When the driver
  counts more cards than it can list, the fallback card 0, an ambient pin onto the display card, the
  installer's single-card exemption and `gpu_guard`'s single-card grant are all refused, and the install
  stops naming the driver's error. A genuine one-card machine is unchanged, including a laptop whose
  owner declared its only card the display, and a declared index is honoured.
- **A CPU answer hides every card** from the process that got it and from what it starts, and
  `model_garden` applies the card pin before it imports torch.
- **A crashed `nvidia-smi`, or a declared compute card the driver does not list, reads degraded** with a
  `doctor` warning, never as "no accelerator". `doctor`'s degraded line and its flag now agree.
- **`load-seed --embed` is one transaction.** A refused or killed load leaves the brain as it was, and
  the error carries the memory count before the load and after the rollback.
- **The model garden's cross-encoder and GLiNER load from the local cache only**, so a missing model is
  reported as unavailable instead of being fetched.
- **The organ run ledger records each run's own result.** A run seen while still going is recorded as
  running and settled when it ends, so a failed run is no longer filed as the previous run's success.
  `doctor` and the yield sentinel name failed runs.
- **The dream pipeline ages rows and cuts its windows in UTC**, in the brain's own stamp format. On a
  host west of UTC the activity profile aged rows against local time, and the co-recall window dropped
  the cutoff's whole date. A new `brain_clock` organ is the one clock.
- **Decision times, the calibration log, the approvals snooze and the belief watermark are stamped in
  UTC**, and read back on the same clock, so a one-hour snooze lasts one hour.
- The qwen-tier end-to-end test no longer inherits the brain location from your shell. With
  `WIZARD_LIBRARY_HOME` exported it wrote the seed pack into that brain, and with
  `WIZARD_LIBRARY_DB` exported it skipped without saying why. It now runs in its own box either
  way, and fails loudly if the seed pack does not land there. It also no longer leaves a
  temporary folder in your home directory when it cannot run.
- The dream-lead differential test no longer fails at random on a host at or east of UTC. Its two
  brains were stamped and read by the wall clock seconds apart, so a time-weighted reader could
  differ in the fourth decimal; both brains and every reader now share one fixed instant.
- Tests that keep a child process off every card declare the CPU instead of passing an empty
  `CUDA_VISIBLE_DEVICES`.

### Known issues

- The run ledger observes its own unit while that unit is running, so its own runs read
  `ended-unobserved`, never `success`. The same happens to a unit that runs more often than hourly and
  is still running when the ledger looks.
- On a machine with two cards, nothing declared, and both cards reporting no active display (a monitor
  asleep), card 0 can still be chosen. This is unchanged from 1.1.0; declaring `WIZARD_DISPLAY_INDEX`
  avoids it.
- Some organs outside the dream pipeline still age UTC rows against local time. The effect is
  day-granular and small.

## [1.1.0] — 2026-11-13

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
- **`tier set` owns the whole switch.** The `WIZARD_EMBED_MODEL` line in the env file's managed block
  moves with the lock, and `tier rollback` puts it back, so a scheduled job keeps working after a
  switch. A shell that loaded the old line is told to reload. A line you wrote outside the block
  refuses `LOCK_ENV_PINNED` and names its line number. `doctor` is not healthy while the env and the
  lock disagree.
- **Nothing else re-points a brain.** `--force-cpu`, a shell `WIZARD_EMBED_MODEL` or `init --tier` on
  a brain that holds vectors under another model refuses, lock or no lock, and names `tier set`.
- **An upgrade embeds nothing unasked.** A plain `bash install.sh` on a brain you have written to
  prints how many memories have no vector, how many are legacy, and the command for each. Only a new
  brain has its seed embedded at install. `embed_backfill.py --only-missing` embeds what has no vector
  and leaves a legacy vector alone, and the nightly backfill job now runs that way.
- **`tier set` batches by length.** Each call to the encoder holds similar lengths up to a padded
  character budget, a text past 16,000 characters is embedded from its head on an accelerator, progress
  prints every 500 rows, and a batch that stalls stops typed and names `--batch-size`.
- **Every repair command runs where it is printed:** absolute paths under the install's own interpreter.
- **A lost lock is known only from what the lock left behind:** its receipt in `embedder.lock.history.jsonl`,
  and the row `embedder_lock_written` it writes into the brain's own `metadata`, so deleting both files is
  still seen. On a brain that lost its lock, `recall` and `anticipate` refuse `LOCK_MISSING` with the way out, as a store
  does; `claude_memory_lite.py` answers by keyword, flagged. A rollback to a brain with no lock clears that row. No record means no lock: a shipped checksum
  in a stamp proves nothing, because a `tier set` build, a website store and a plain store write them with no
  lock in sight, so a jina brain keeps serving through all three.
- **A malformed lock prints its way out:** move it aside and write it again, under the tier the records name.
- **`doctor` reads the brain as recall does:** the lock's model absent from this disk, a lockless brain's own
  model absent while it holds vectors, a dropped keyword index and a metadata table nothing builds all read
  unhealthy, each with the fault named.
- **A plain re-run of an attached install keeps its brain, on every re-run.** Before, it built a blank brain in a new
  file and pointed everything at it. An attached file that is missing stops the run, however many runs have passed
  since the attach; nothing is built in its place. The record says `attached=1` and names the file itself, so a folder
  copied to a new home is not taken for an attached one.
- **Windows:** a fresh install completes (`Copy-Item` now lays folders), the semantic stack comes from
  `install\requirements.txt`, the cause of a refused embedding is printed, a failed run keeps its venv, a machine
  whose reranker gate is off fetches no reranker, models download one file at a time so a missing symlink privilege
  cannot lose one, a brain on another tier keeps it instead of stopping the install, a re-run updates the `voidloom`
  CLI, and every printed repair command runs in PowerShell as printed.
- **`maintain` recomputes a similarity link stamped under another model** when it compares the pair, however far apart
  the pair now reads and whichever way round the link was stored. A pair it cannot compare keeps its old strength, and
  `status` counts it.
- `model_fetch.py` fetches a jina brain's model at the revision the engine pins, and the installers do the same instead
  of loading it by name, so the printed fix works there and the hub's moving `main` is never what runs. Its remote code
  asks for its adapters with no revision, so the fetch also leaves a `refs/main` naming the pin when none is there.
- **`doctor` exits 4 on an unhealthy brain.** `status` stays 0. Its reason carries the refusal's way out, and a model
  folder an interrupted download left no longer reads as a model.
- **A degraded answer says so where a person reads it.** `recall`, `batch-recall` (per item), `status`, `stats` and
  `anticipate` carry a top-level `degraded` block beside `coverage`, and the human output prints the banner. A failed
  `batch-recall` item carries its typed `code` and `coverage` beside its message, and prints them.
- **`--brain-db` embeds nothing unasked,** as a plain re-run does not: a snapshot with memories of its own reads as an
  existing brain before it is copied, and keeps its own tier.
- **Running an install twice leaves the brain byte-identical.** The lock's row in `metadata` is written only when its
  value changes, and `init` on a brain with no memory keeps no `.pre-hybrid-retrieval.bak`.
- **`--dry-run` on every armed unit:** it reads what the unit would work over, prints it and writes nothing.
- **`voidloom tier set <nomic|qwen>`** moves a brain to another embedding model by re-embedding
  every memory under it while the old model keeps serving recall. It switches only when every
  memory has a vector under the new model, replacing the lock in one step and writing a
  receipt to `embedder.lock.history.jsonl`. `--plan` counts the work first and changes nothing.
  The old vectors are kept. **To roll back,** run `voidloom tier rollback`: it restores the
  previous lock and table and reports how many memories have no vector there, whenever they
  were written. When the old tier is shipped, `voidloom tier set <old tier>` fills that gap,
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
  looked up the vector table once per row. On a 31,904-memory brain the same counts now take 0.12
  seconds with no lock; with a lock, 0.29 seconds for `doctor` and `status`, and 0.18 seconds on the
  recall path, which leaves out the hash of every memory's text.
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
  the reference cleared. Then the text goes, from a declared list of free-text columns and no others:
  `metadata`, stamps, provenance keys and config tables are never edited, and a text column the list does
  not know is left alone and named in the receipt. In a row tied to this memory (a memory written from it, a
  row that names it or one of those, a summary of a community it sat in) the whole text from 8 characters up
  and any shared run of 47 characters or more become `[removed]`. In any other row only an exact copy of a
  substantial text does: the memory's whole text of 47 characters or more, or a cell that is itself 47 or more
  characters cut from it. A template or a phrase a row merely shares stays. The keyword index is merged, the memory's vectors are overwritten with zeros before
  they are removed, and the file is rebuilt (`VACUUM`), so an older copy an earlier edit left in a
  page's free space goes too, then the write-ahead log is emptied. The receipt prints counts per table
  and no content, and says `compacted` and `wal_emptied`: another process on the brain can block
  either, and it says why. **What it does not reach:** a separate memory holding the same text is
  counted in `other_memories_sharing_text` and left alone, and rows unrelated to the deleted memory then
  keep their copies of that text and only that text, because it is not the memory's alone
  (`unrelated_rows_left_as_shared_text`): a summary two memories share does not protect the content only one
  of them held; a near
  copy in an unrelated row, one character off, stays; words a model reworded are not a copy it can see; a consolidated memory's vector still averages the deleted one until its next rebuild; a vector
  replaced before this version may have left its old bytes in the table's storage; and copies are yours
  to handle: the receipt names any beside the brain in `other_copies_not_reached`. A hard delete under
  an interpreter that cannot reach the vector refuses `HARD_DELETE_VECTOR_UNREACHABLE` before any row
  goes. Without the variable nothing changes: a soft archive. `claude_memory_lite.py seed-delete-one`
  follows the same rule under the variable, and without it still refuses `HARD_DELETE_REFERENCED`. A
  deleted seed memory stays deleted: the next load of the pack leaves it out.
- **A deleted lock no longer reads healthy.** Every lock write leaves a receipt beside it, so a
  missing lock with such a receipt means the brain was locked: `doctor`, `status` and the reader report
  `LOCK_MISSING`, name the tier and the command that writes the lock again, and nothing searches the
  whole table in its place. No receipt and no lock stays a legal older brain, as does a brain a
  `tier rollback` took back to having none. A lock that exists and cannot be read refuses
  `LOCK_MALFORMED` instead of reading as absent.
- **Only an install, `tier set` and `re-embed` reach the network for a model.** A recall or a store
  on a machine without the model's files used to download them at the pin. Encoding now runs with the
  hub closed: a recall answers by keyword, flagged `degraded` with `SNAPSHOT_ABSENT`, on the engine and the
  front door alike, and `--vector-only` refuses with the command that fetches them; a store on a locked brain
  refuses the same way, and stores nothing it could not find again. An interrupted download counts as absent, not as a changed file, and the
  same fetch completes it; `doctor` reads it as a fault. Recall no longer fetches a reranker either,
  and a recall with none says so in `coverage.reranker`.
- **A recall's side channels read only the search set.** A dream or playbook row whose vector carries
  another model's checksum no longer reaches a channel by similarity.
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
- **Keyword recall at Tier 0 carries coverage,** and `claude_memory.py recall --bm25-only` needs no embedding model.
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
  `anticipate`. When the vector arm cannot run because its table is gone, or the brain holds no
  vector yet, recall answers from keywords with `degraded: true` and the refusal's code instead of
  failing or looking normal. A lock that was lost, that cannot be read, that no row answers to or
  that names a checksum the package does not ship is a refusal: typed, with its coverage and the way out, and
  no keyword answer. In Python, `recall_hybrid_with_coverage`
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

[Unreleased]: https://github.com/void-wright/voidloom/compare/v1.1.3...HEAD
[1.1.3]: https://github.com/void-wright/voidloom/releases/tag/v1.1.3
[1.1.2]: https://github.com/void-wright/voidloom/releases/tag/v1.1.2
[1.1.1]: https://github.com/void-wright/voidloom/releases/tag/v1.1.1
[1.1.0]: https://github.com/void-wright/voidloom/releases/tag/v1.1.0
[1.0.0]: https://github.com/void-wright/voidloom/releases/tag/v1.0.0
