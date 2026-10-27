# Repository layout — how to find anything in one screen

> Read this before you read code. **419 tracked files, 10 of them at the
> top level.** Every count on this page is derived from the tree, and
> `voidloom/tests/test_docs_name_real_paths.py` re-derives them and fails if
> this page and `git ls-files` disagree — because the previous version said
> "derived ... never typed" above three figures that had all gone stale.

## The shape, and the one rule that produces it

**The repository root IS the runtime directory.** `install.sh` reproduces this
tree under `$HOME/.claude/memory/` — file by file, from `install/lay_manifest.json`,
which is derived from the git index and bound to it by
`voidloom/tests/test_lay_manifest.py` — so what you read here is what runs.
Ten front-door files (README, LICENSE, NOTICE, CHANGELOG, CONTRIBUTING,
SECURITY, CODE_OF_CONDUCT, install.sh, uninstall.sh, .gitignore) ship but are not laid; `persona/skills/*` lands under `~/.claude/skills/`, the
council under the same root only when `install/install_council.sh` is run, and
the Persona Forge (`persona/skills/persona-forge/` + `/persona-forge`, component
`forge`) by `install/install_persona_forge.sh`, which `install.sh` calls as its
last step after the acceptance gate (see `docs/PERSONA_FORGE.md`).

Two things live at the root and nothing else does:

* **data** — `claude_memory.db` and its caches (git-ignored; the brain's
  *contents* are never in this repo, only its *machinery*)
* **the front door** — `README`, `LICENSE`, `NOTICE`, `install.sh`

Everything else is in a directory named for what it does.

### The rule that decides where a file goes

> **Does the module import its siblings, or is it imported by them?**
> Yes → `organs/`. No → the directory named for its job.

`organs/` is a single flat namespace **on purpose**. The 71 tracked modules there
import each other by bare name (`import hybrid_retrieval`), and ~25 of them
self-heal that with `sys.path.insert(0, Path(__file__).parent)`. Splitting them
into `core/` + `organs/` would break every cross-directory import — and the
self-insert would hide it, because each module would still find *its own* new
directory and fail only on the sibling. **The flat namespace is the contract;
the directory around it is the tidying.**

## The map

```
.
├── README.md              ← start here: what it is, install, first recall
├── LICENSE  NOTICE        ← Apache License 2.0 + copyright, third-party and model attributions
├── install.sh             ← the installer (POSIX). Windows: install/install.ps1
├── voidloom_manifest.json ← per-machine hardware/model manifest (installer-written)
│
├── organs/                ← ★ THE ORGANISM — 71 modules, one flat namespace
│   ├── claude_memory.py        ★ THE ENGINE. store / recall / stats / init
│   ├── claude_memory_lite.py   ★ Tier-0 engine: keyword-only, stdlib, no model
│   ├── wizard_paths.py         ★ the tree-root vs module-dir resolver
│   └── …                       the L2–L5 organ suite
│
├── voidloom/              ← the CLI facade (`voidloom` on your PATH)
│   ├── _paths.py               ★ brain_dir (data) + organ_dir (code), env-first
│   ├── cli.py  _facade.py  _guard.py  _bootstrap.py
│   └── voidloom                the shell shim symlinked onto PATH
│
├── migrations/            ← schema.sql + 31 additive migrations
├── install/               ← install.ps1, cron/council/persona wiring, self_check,
│   │                        requirements (pinned + hash-pinned), CLAUDE.md.template
│   ├── lay_manifest.json       ★ WHERE EVERY TRACKED FILE LANDS — derived from
│   │                             `git ls-files --stage` by tools/build_manifest.py --lay
│   ├── lay.py                  ★ the one executor both installers call (stdlib, py>=3.8)
│   ├── tier1_probe.py          ★ the ambient Tier-1 shape's PROOFS, run by the candidate
│   │                             interpreter before anything is laid (install.sh --tier1-ambient;
│   │                             the brain's INSTALL_RECORD then names the shape for later runs)
│   ├── env_file.py             ★ THE ONE READER of the env file every unit loads: systemd's own
│   │                             EnvironmentFile= rules (no inline comments, no `export`, no
│   │                             expansion), bound to systemd's loader by test_env_file_reader.py;
│   │                             install.sh reads the pin, the display declaration, the block rule
│   │                             and the read-back through it, and refuses what a shell reads otherwise
│   ├── cron_roster.json        ★ which packaged timers an installer arms, and why the rest
│   │                             ship as templates, plus each unit's lines beyond the template
│   │                             (service_extra / timer_extra / service_omit); cron_roster.py
│   │                             is its one reader
│   ├── install_crons.sh        ★ the ONE unit template: renders + arms the roster, and the
│   │                             rendered body equals the packaged body (test_cron_roster.py,
│   │                             all 17); what `install.sh --with-crons` calls and what you
│   │                             re-run later; --dry-run prints every body under DRY-UNIT:
│   └── write_set.json          ★ every path an install may touch OUTSIDE ~/.claude, per step,
│                                 and every scheduler call it makes (`verbs`)
├── seed/                  ← seed_pack.json + claude_memory_blank.db (schema-only)
├── hooks/                 ← session-start / precompact / notify / signoff (+ .mjs)
├── cron/                  ← systemd units + timers + platform detection
├── capability/            ← host capability detection (GPU, models, tier)
├── persona/               ← skills/ · council_skills/ · commands/
├── tools/                 ← maintainer-only: package gates + recall harness
└── docs/                  ← this file and its siblings
```

## Where data and code diverge — the thing to know before you move a file

Until 2026-09-07 the modules and the brain DB shared one directory, so
"the brain dir" meant both. They are now separate, and **the two anchors are
not interchangeable**:

| you want | anchor | in `organs/` that is |
|---|---|---|
| the DB, caches, `voidloom_manifest.json`, `archives/` | the **tree root** | `Path(__file__).parent.parent` |
| a sibling module | the **module dir** | `Path(__file__).parent` |
| a migration or `schema.sql` | `migrations/` | `Path(__file__).parent.parent / "migrations"` |

Getting the first one wrong does **not** raise. `sqlite3.connect()` creates a
missing file, so a module that resolves its DB one level too deep opens a
brand-new empty database and reports success. Two resolvers in this tree
(`wizard_paths.get_db_path`, `voidloom_banner.resolve_home`) reached the DB
through a helper rather than an inline `Path(__file__)`, so a regex sweep did
not see them; both were caught by *executing* the resolvers and asserting the
result, which is the only check that can tell those two cases apart.

**Both resolvers PROBE for the layout** (`dir.name == "organs"`) rather than
assuming it, so a flat legacy install still resolves.

## Known limits

* `migrations/` numbering has gaps (025, 026, 034-038): numbers claimed during
  development and never shipped. The migrator applies files by full filename and
  decides "already applied" from the brain's schema, so the gaps are harmless.

* `tools/` is maintainer machinery. `install.sh` lays it down for reference; the
  brain never imports it and nothing breaks if it is absent.
* `eval/` is created on demand by `embedding_upgrade.py`; it is not shipped.
* The brain database (`claude_memory.db`) and everything else a running brain
  writes are git-ignored: this repository carries the machinery, never memories.
