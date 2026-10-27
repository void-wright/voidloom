# What this will do on your machine the first night

> The brain is not a database you query. It is a **database plus eighteen
> packaged scheduled jobs that can change it while you sleep — of which the
> installer arms nine, and only when you ask.** If you install this and walk
> away, things happen. This page says exactly what, when, and what it costs —
> because the honest answer to "what does it do the first night" is the
> difference between a tool and a surprise.

## The short version

| | |
|---|---|
| installs to | `$HOME/.claude/memory/` |
| brain file | `$HOME/.claude/memory/claude_memory.db` (SQLite, grows with use) |
| sidecar dir | `$HOME/.voidloom/` (env file, config; the overnight thinker's own credential in `thinker/`, mode 700) |
| scheduled jobs | **18** packaged systemd user timers; `install.sh --with-crons` (or `install/install_crons.sh`) arms **9** of them — the set `install/cron_roster.json` marks `armed-by-default` — and none are armed by `install.sh` alone |
| network | none, except the jobs marked **LLM** below |
| GPU | used only for embedding + reranking; the device is **declared, never guessed**: on a multi-GPU host with nothing declared the installer ASKS you which card (or takes `--display-gpu`/`--gpu`), and with nobody to ask it REFUSES |

**Nothing is scheduled by `install.sh` alone.** Timers are armed by an explicit
step (`--with-crons`, or `install/install_crons.sh` later). If you never run it,
the brain is inert between your own commands — which is a fine way to start.
Which timers that step arms is declared ONCE, in `install/cron_roster.json`:
every packaged timer is either `armed-by-default` or `ships-as-template`, each
with a reason, and `voidloom/tests/test_cron_roster.py` fails if a timer is
shipped without a classification. `sleep-loop` — the only job that writes
`consolidation_sources`, the table that proves the brain consolidated — is
armed by default; until 2026-09-18 it was in neither installer's table, so a
stranger's brain never consolidated.

## The eighteen packaged jobs, and when they fire

Derived from `cron/*.timer` — schedules are the units' own `OnCalendar` lines,
not a global interval. `voidloom/tests/test_first_night_is_derived.py` re-derives
this table from that directory and fails if the two disagree, because the last
version of this page said *"derived from `cron/*.timer`"* above a count of 13, a
heading of 13 and a table of 12, for a directory that held 17.

**⚠ Read the "what actually installs" section below before you act on this table.**
These eighteen files are what the package *ships*; the installers arm the
eight the roster marks `armed-by-default`.

| unit | fires | what it does | LLM? |
|---|---|---|---|
| `pre-cognitive` | every 15/45 min, 08:00–21:00 | pre-warms a recall cache | no |
| `dreaming` | daily 22:00 | consolidates the day's memories | **yes** |
| `sleep-loop` | daily 22:30 | summarises and links | **yes** |
| `anti-recency` | Wed 08:00 | rebalances against recency bias | no |
| `gap-mapping` | Mon 08:00 | finds knowledge gaps | no |
| `gap-research` | Mon 08:30 | researches those gaps | **yes** |
| `whisper-digest` | Fri 08:00 | weekly subjective digest | **yes** |
| `skill-graduation` | Fri 08:30 | promotes provisional skills | no |
| `cognitive-map` | Sun 18:00 | rebuilds the concept map | no |
| `self-eval` | 1st of month 08:00 | monthly self-evaluation | **yes** |
| `arcane-manifest` | quarterly, 1st 09:00 | the manifest of the quarter that just ended | **yes** |
| `counterfactual-revisit` | quarterly (four `OnCalendar` lines) | revisits old decisions | **yes** |
| `affect-probe` | daily 09:00 | scores affective salience | no |
| `belief-scan` | daily 22:45 | retires contradiction links it drew with an end not written through store/update (the brain's own reports, or a memory whose writer is unknown); proposes none for now: only pairs written through store/update would be judged, and those are not queued until a detector can tell a real contradiction apart | no |
| `dream-daytime` | daily 14:00 | the daytime dream phases | **yes** |
| `organ-run-log` | hourly, :05 | records every organ run's verdict | no |
| `organ-yield` | daily 09:15 | flags organs producing nothing | no |
| `embed-backfill` | daily 09:40 | gives a vector to every memory stored without one | no |

Two things to notice before you enable any of them:

1. **Eight of the eighteen call an LLM** — the ones marked **yes** above
   (`sleep-loop` only when your own sleep-loop config asks for its `llm` mode; the
   packaged config is heuristic). WHICH model they call is your answer to the
   installer's question *"Should your brain think while you sleep, and with which
   model?"* (a Claude, b Codex, c Kimi, d Gemini, e Not now, f Other), proven with one test
   question before it is recorded in `THINKER_RECORD` beside the brain; the
   credential is the thinker's own, in `$HOME/.voidloom/thinker/` (never your
   everyday sign-in). `install/install_crons.sh` refuses to arm them while no answer
   is recorded. A paid model costs money; with **e) Not now**, or when the model
   cannot be reached, they take their heuristic path and say so. The one step for
   each model is in [`OVERNIGHT_THINKER_SETUP.md`](OVERNIGHT_THINKER_SETUP.md);
   `voidloom thinker status` shows what is recorded.
2. **`Persistent=true` is set on every timer.** A missed window becomes a
   *late* run at boot, so a machine that was off overnight fires several jobs
   at once when it comes back. That is a catch-up storm, not a malfunction:
   every job that writes the brain waits up to 60 seconds for another job's
   write to finish, so a storm's jobs queue instead of failing. A job that
   still meets a write held longer than that fails loudly, in its journal
   (`journalctl --user -u wizard-library-<job>`); start it again by hand with
   `systemctl --user start wizard-library-<job>.service` rather than waiting for
   its next window (a quarter away, for the quarterly jobs).

## ⚠ What actually installs — and it is not these files

This section used to read *"Eight of the thirteen units are DEAD as shipped"* and
told you to *"treat `cron/` as reference material, not as something to install."*
**Re-measured 2026-09-16, every one of those sentences is now false**, and the
reason they went stale is the finding, not the numbers: nothing re-derived them
when `cron/` was repaired.

Measured on the packaged `cron/` directory:

| claim, as it stood | measured now |
|---|---|
| 8 of 13 `ExecStart` lines point at a script that does not exist | **0 of 18.** Every packaged unit's target ships in the package |
| the packaged units hardcode one machine's absolute paths | **none do.** All 18 use systemd's `%h` home specifier |
| `affect-probe`, `dream-daytime`, `organ-yield` have no packaged counterpart | **all three are packaged** |
| the packaged set has 13 units, a live install runs 20, seven have no packaged counterpart | **18 in the directory**, one per job in `install/cron_roster.json`, and **every one ships**. A machine may run more units of its own; the package neither ships nor touches them |

**What is still true, and it is the thing worth knowing: the installers do not
install these files.** They *generate* their own units, from one template and
one roster, for the nine jobs `install/cron_roster.json` marks
`armed-by-default`:

```
anti-recency dreaming embed-backfill gap-mapping organ-run-log self-eval skill-graduation sleep-loop whisper-digest
```

So the package ships eighteen unit files, the installers arm nine, and **the
nine they arm are generated from the packaged units' own `ExecStart` and
`OnCalendar` lines, and carry the packaged unit's full directive set** — every
`Key=Value` line, `Nice=`, `Environment=PATH=`, `RandomizedDelaySec=` included;
only the description, the absolute paths (`%h` rendered) and the interpreter
line differ (`test_cron_roster.py` renders all eighteen through the template
and fails if a rendered body and its packaged unit disagree). `install.sh` lays
`cron/*` into `~/.claude/memory/cron/` so the
files are there to read and to adapt. The other nine ship as **templates**, each
with its reason in the roster's `why` field — most call a billed LLM, one loads
the embedding model every thirty minutes — and you arm one deliberately:

```bash
WIZARD_CRON_ONLY=gap-research bash ~/.claude/memory/install/install_crons.sh
```

### Verify before enabling — and use this loop, not the old one

The previous version of this page shipped a check that reported **every** unit as
dead, because it compared a literal `%h/...` against the filesystem without
expanding it. A verification loop that fails open is bad; one that fails *closed*
on everything is worse, because it teaches you to ignore it.

```bash
cd ~/.claude/memory
for u in cron/*.service; do
  s=$(grep -m1 '^ExecStart=' "$u" | tr ' ' '\n' | grep -m1 '\.py$' | sed "s|%h|$HOME|")
  [ -e "$s" ] || echo "DEAD: $u -> $s"
done
```

On the shipped tree this prints nothing.

### The sidecar env file

Every packaged unit carries `EnvironmentFile=-%h/.voidloom/wizard-library-env`.
The leading `-` means an absent file is not fatal — which also means it is
**silent**, so a job that behaves as though it has no `PATH` (systemd's user PATH
omits `~/.local/bin`) is usually this. The file is always
`~/.voidloom/wizard-library-env`, unless `WIZARD_ENV_FILE` names another absolute
path; `install.sh` writes it and prints the path. An env file written by an older
install may hold a Claude token line (`CLAUDE_CODE_OAUTH_TOKEN=`) from before the
thinker had its own folder. That line is
read as a legacy credential until `voidloom thinker migrate` copies it into
`$HOME/.voidloom/thinker/claude.env`; after that you may delete it yourself once `voidloom thinker status`
says it is no longer needed (it says STILL NEEDED, and names the job, while any nightly job still reads that line
itself -- the package's own, or one of yours that loads the same env file and is not proven to use the thinker's copy).

## What the sleep loop lets go of — and how to take it back

Every night the `sleep-loop` job lowers the confidence of each link between two
memories a little (the same small step per day, however often it runs), and
**prunes** a link once it is both weak (below 0.15) and old (more than 45 days).
A pruned link is **retired, never deleted**: its row stays in `memory_edges`
with `valid_until` set to the time of that night's run (UTC), and its
`metadata` replaced by a note that says `"retired_by": "decay-prune"`, when, the
confidence it had, and the metadata it carried before. Recall and every other
reader of the live link graph skip it, exactly as if it were gone. The night's
summary counts it under `edges_pruned`, and a `--dry-run` reports the same
number the real run retires.

To bring every pruned link back, run this one statement against your brain
(with the sleep loop stopped, and after a backup):

```sql
UPDATE memory_edges
   SET valid_until = NULL,
       confidence = json_extract(metadata, '$.retired_confidence'),
       metadata = json_extract(metadata, '$.prior_metadata')
 WHERE valid_until IS NOT NULL
   AND CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.retired_by') END = 'decay-prune'
   AND valid_until = CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.retired_at') END;
```

To bring back only one night's, add `AND valid_until = '<that night's time>'`
before the semicolon. It never touches a link that something else ended (the
belief engine's invalidations and quarantines carry no such note). A restored
link that is still weak and old is retired again by the next night's run on a
later day, unless its confidence is raised (the repair below is the one
way that happens).

### Once, on a brain that ran the old sleep loop: repair before the catch-up

Before round 2 the sleep loop lowered every link by its whole age again
on every night it ran, so a link a few weeks old had been lowered dozens of
times over. The night-by-night lowering is fixed; the damage already done is
repaired once, by hand, **before the new sleep loop runs at all** -- the scheduled night included (stop the
sleep-loop timer first, or repair the same evening you land; once the new loop has written one run, the repair
refuses):

```bash
python3 ~/.claude/memory/organs/temporal_edges.py repair-decay ~/.claude/memory/claude_memory.db           # look first: changes nothing
python3 ~/.claude/memory/organs/temporal_edges.py repair-decay ~/.claude/memory/claude_memory.db --apply   # then write it
```

It replays the old code over the sleep loop's own run history from each
strength a link could have been created with, and repairs a link only when
exactly one starting strength reproduces what is stored today, to the last
digit. A link it cannot vouch for that way (one the dream phase also
strengthened, or whose writer is unknown) is left exactly as it is, and the
report counts it by reason. A repaired link never ends above the strength it
was created with. The report also says how many links the catch-up would
retire without the repair and how many of those it now keeps.

It is reversible until the next sleep run: `--undo` puts back every repaired link the decay has not moved since
(after a night has run it reports those links as moved and leaves them as they are; the previous values are kept
in the brain's `metadata` table under `edge_decay_repair`). Running `--apply` twice changes nothing the second time.
Once the new sleep loop has run, the repair refuses: its replay is only valid
over a history the old code wrote alone.

## What it costs

* **Disk** — the brain grows with what you store. A mature brain of ~17,800
  memories with two embedding tiers is roughly 236 MB. The embedding model
  cache under `${XDG_CACHE_HOME:-~/.cache}/huggingface/hub/` is a separate, larger cost.
* **GPU memory** — only while a job runs. The device is *resolved at install
  time*, never hardcoded and never defaulted: one GPU is used; on a multi-GPU host
  the card is the one the env file DECLARES (`WIZARD_CUDA_DEVICE=<index>` outside
  the managed block) -- and where nothing declares it, the installer **asks you**
  (which card drives the monitor, which card the jobs use) and records your answers
  there, or takes them as `--display-gpu`/`--gpu`, and with nobody to ask it
  **refuses and says so**. It never picks from `display_active`: a monitor that is
  asleep reads Disabled on every card. Taking the display card evicts the desktop
  session and does so silently, which is why "cannot tell" is a question here and
<!-- gpu-pin-gate: prose recording a pin this cut REMOVED; not an instruction and not a setting -->
  not a guess. (Two packaged units used to carry `CUDA_VISIBLE_DEVICES=1` — one
  machine's answer baked into the package. They no longer do; the pin reaches
  them through the env file above, which has exactly one writer.)
* **Money** — only the eight LLM jobs, only with a paid model as your thinker answer
  (e) Not now, or a model on your own computer, costs nothing).
* **Installer footprint outside `~/.claude`** — each `--attach-db` run leaves a
  full pre-image beside the brain (its size again; never pruned for you); Tier 1
  adds the venv (~5.7 GB) plus the Hugging Face (~1.7 GB) and pip (~3 GB)
  caches; `--with-crons` re-renders the nine armed units from the one template
  and re-enables them. `install.sh --print-write-set` lists it all, resolved.

## Turning it off

```bash
systemctl --user list-timers 'wizard-library-*'      # what is armed
systemctl --user disable --now wizard-library-<name>.timer
```

Disabling a timer never touches the brain. Your memories are yours; the
schedule is a separate decision, and it is reversible.
