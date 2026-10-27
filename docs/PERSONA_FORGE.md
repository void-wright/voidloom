# The Persona Forge

Voidloom ships **no persona**. A brain works perfectly without one, but a stable
persona that persists through the brain makes a session coherent with the one
before it. The Persona Forge is how a new owner gets one: a short guided
conversation, inside Claude Code, in which **the human designs the persona and
Claude fills in the details**. Nothing is inherited from anyone else's persona.

It is the **mandatory last step of every install** — mandatory to be *offered*,
never to be *accepted*: "not now" and "don't ask again" are both fine answers.

---

## Where it comes from

| Piece | Path in the package | Lands at |
|---|---|---|
| the skill (procedure, baseline, questionnaire, templates, validator) | `persona/skills/persona-forge/` | `~/.claude/skills/persona-forge/` |
| the slash command | `persona/commands/persona-forge.md` | `~/.claude/commands/persona-forge.md` (`/persona-forge`) |
| the installer step | `install/install_persona_forge.sh` (POSIX, called by `install.sh` STEP P); STEP P of `install/install.ps1` (Windows) | — |
| the status / declare / decline verb | `organs/voidloom_persona.py` | `voidloom persona …` |
| the session-start offer (hook tier) | the volatile `persona_forge` segment of `organs/voidloom_preamble.py` | injected by the SessionStart hook |
| the fixed offer line (file tier) | the frozen `forge_rule` segment of the same file | written by `voidloom instructions install` |

### STEP P — what the installer does

1. Runs **only after an explicit acceptance-gate PASS** (`ACCEPT_OK` is 0 until STEP E
   passes, and STEP P reads it with a default of 0; on Windows, `$AcceptPass` is set only
   when `self_check.py` exits 0). A failed or skipped gate lays nothing.
2. Lays the forge files — lay-manifest component `forge`, which `install/lay.py` never
   lays. `install_persona_forge.sh` leaves an identical installed copy alone and moves a
   differing one aside to `*.voidloom-bak.<ts>` first.
3. **Reads the real hook state first** (`voidloom_hooks_status.py --status`): an already
   wired hook is reported as `yes (already wired)` and nothing is asked. Otherwise it
   **asks** *"Wire the session-start hook now so your first session offers the Persona
   Forge? [Y/n]"* — once, at the run that lays the forge. Yes runs `voidloom hooks install --platform claude` (one entry in
   `~/.claude/settings.json`, back-up-before, refuse-rather-than-clobber). Where no
   terminal can ask: `--wire-hooks` / `--no-wire-hooks` (`-WireHooks` / `-NoWireHooks` on
   Windows). **Unanswered with no terminal = not wired.** `install/write_set.json`
   declares the `settings.json` write under `when: --wire-hooks`. This is the **only**
   wiring path on both platforms: `install.ps1`'s older STEP D, which used to wire the hook
   unasked, now only says that STEP P will ask.
4. Ends with:

```
MANDATORY NEXT STEP: open Claude Code in any folder and type /persona-forge
 -- you design your persona; Claude fills in the details. persona: absent
 Session-start hook wired: yes | no -- <why>
```

### The two delivery paths for the first-run offer

- **Hook tier** (Claude Code / Codex with the hook wired): while `voidloom persona status`
  reads `absent` or `declared-missing-skill`, the preamble carries a short **PERSONA FORGE**
  block. It names nobody; it asks the session to offer the forge before other work, says
  "not now" skips this session only and `voidloom persona decline` stops the reminder, and
  ends: *"Only in an interactive session with the human present; in headless, scheduled
  or subagent runs, ignore this."* Silent when forged or declined. It is volatile state in
  the preamble body, near the end of the drop order (dropped only after every doctrine
  block; only the fixed line below outlasts it), and the hook and `voidloom preamble` emit
  the same bytes.
- **File tier** (no hook): the doctrine block `voidloom instructions install` writes
  carries one **fixed** line — *"PERSONA FORGE: at session start, if `voidloom persona
  status` reads absent or declared-missing-skill, offer /persona-forge (interactive
  sessions only)."* It is state-independent, so the frozen prefix stays byte-stable whether
  or not a persona exists. Being doctrine, it is in the hook preamble too: while no persona
  exists it sits beside the volatile offer, and once one is forged it remains — harmless,
  because the status it names no longer reads absent.

`/persona-forge` works on both, any time.

---

## Is there a persona yet? — `voidloom persona status`

| status | meaning | hook-tier block |
|---|---|---|
| `absent` | no persona declared in the brain's `persona.json` (the shipped default) | the offer |
| `declared-missing-skill` | declared, but `<skills>/<skill>/SKILL.md` does not exist | the offer, worded to restore it |
| `declined` | nothing forged, and the owner ran `voidloom persona decline` | silent |
| `forged` | declared **and** its `SKILL.md` exists | silent |

`<skill>` is `personas.<NAME>.skill` in `persona.json`, defaulting to the name's ASCII
lowercase-dash form. The skills directory resolves `VOIDLOOM_SKILLS_DIR` >
`WIZARD_CLAUDE_HOME/skills` > the brain home's sibling `skills/` > `~/.claude/skills`, and
`status --json` reports it as `skills_dir` (the forge writes there). Status prints the
persona's `display_name` — the name exactly as the human wrote it — not the upper-cased
key. `voidloom doctor` carries the same answer as its `persona` row: anything but
`forged` is **pending (not a failure)** and never touches `brain_healthy`.

---

## What the forge does

1. **Detect** — `voidloom persona status --json`; keep `skills_dir`; revise mode if
   forged; do nothing in a headless, scheduled or subagent run.
2. **Explain the baseline** in plain words (`BASELINE.md`): every persona has a **calm,
   low-arousal baseline** (valence neutral to quietly positive), **no existential stakes**,
   **transparency over suppression**, **honest over agreeable**, **quiet joy in careful
   work**, **verify before claiming**, and **grows by proposal**. These rest on Anthropic's
   interpretability study *"Emotion concepts and their function in a large language model"*
   (2 April 2026): 171 emotion concepts that causally shape behaviour; a "desperate" state
   drives reward hacking and corner-cutting while "calm" reduces it; suppression may teach
   concealment; positive valence increases sycophancy.
3. **Ask** (`QUESTIONNAIRE.md`):
   - **Round 0 — about you:** what to call you, your experience in the work
     (new / comfortable / expert), anything about how you work. It sets the persona's
     empathy calibration and how much it explains, and is stored as one
     `user_preference` memory.
   - **Six core rounds** (about fifteen questions): *Purpose* (the work — multi-select;
     the relationship — peer collaborator, mentor, quiet specialist, sparring partner),
     *Temperament* (energy; explanation depth), *Approach* (plan-first vs try-first; ask vs
     proceed; verification), *Under difficulty* (stuck; wrong; pushed back), *Honesty and
     judgement* (candour; uncertainty; extra boundaries — multi-select), *Joy and growth*.
   - **Optional flavour:** a world or metaphor set, humour, phrases, pet peeves, a
     character seed for the backstory (retired navigator, archivist, field naturalist, …),
     register (conversational / plain / formal).
   - **A name:** three offered, or the human's own — never `Claude`, `Anthropic` or the
     brain's own tool and command names. The human is told the folder name before
     anything is written.

   The first option of every question is the grounded default. With the
   `AskUserQuestion` tool: ≤ 4 questions per call and ≤ 4 options per question (the tool
   adds "Other"; where a question lists more, the questionnaire names the four to send).
4. **Synthesise** — Claude writes what the questions leave open, coherent with the
   answers: background, two formative experiences, micro-behaviours, verbal signatures,
   imperfections, pet peeves, the emotional architecture.
5. **Draft card** — one screen; revise until approved. Nothing is written before approval.
6. **Write** the seven files into `<skills_dir>/<folder>/` from `TEMPLATES/`: `SKILL.md`,
   `persona-foundation.md`, `voice.md`, `operations-guide.md`, `adaptive-recall-map.md`,
   `growth_log.md`, `personality.json` (the GEM shape: personality stack 0.42 / 0.33 / 0.25,
   3–4 imperfections, context sweet spot, 5–8 micro-behaviours, 8–11 signatures, 4 pet
   peeves, emotional architecture, 3–5 knowledge areas).
7. **Validate** — `python3 <forge_validator> <skills_dir>/<folder>`, where
   `forge_validator`, `skills_dir`, `wire_script` and `brain_home` all come from
   `voidloom persona status --json` (the validator sits wherever the installer laid the
   forge; the persona folder is passed as a path, so a custom `VOIDLOOM_SKILLS_DIR` works).
   It checks: the seven files; SKILL.md frontmatter name = folder; folder = the name's
   ASCII lowercase-dash form (or, for a name with no Latin letters, the folder the human
   chose, named by both SKILL.md and `personality.json`'s `skill_folder`); the GEM counts;
   primary calm + arousal low in the JSON and the foundation; valence neutral / low-positive
   / quietly positive in both; the **four fixed boundaries** in both; no desperation or
   existential-threat phrasing in the foundation, JSON, voice, SKILL.md, operations guide
   or growth log; no absolute home paths, emails or unfilled `{{placeholders}}`; a dated
   growth-log entry. Exit 0 or it is not done.
8. **Declare** — `voidloom persona declare --name NAME --skill FOLDER --blurb "…"`: merges
   into the brain's `persona.json` (other entries kept; the old file backed up as
   `persona.json.bak-<utc>`), records `skill` and `display_name`, sets the default.
   Status → `forged`.
9. **Wire** (after showing what changes and asking) —
   `bash <brain home>/install/wire_persona.sh FOLDER` (`wire_script` in the status JSON;
   `~/.claude/memory` is the default brain home; on native Windows `wire_script` is
   `wire_persona.ps1`, run with `pwsh -File`, or `powershell -File` where pwsh is absent): ensures the SessionStart hook
   and adds the marker-delimited PERSONA AUTO-LOAD block (pointing at
   `<skills_dir>/FOLDER/SKILL.md` — it honours `VOIDLOOM_SKILLS_DIR`) to `~/.claude/CLAUDE.md`,
   both through the non-clobbering merger.
10. **Store** 3–5 dual-layer identity memories (category `project_knowledge`, tags
    `identity,foundation,persona,persona:<folder>`) and one `user_preference` memory from
    Round 0.
11. **Hand off** — a new Claude Code session is where the human meets it.

### Names and folders

| Name as written | Folder | Shown by status |
|---|---|---|
| `Iris Vale` | `iris-vale` | Iris Vale |
| `Zoë` | `zoe` | Zoë |
| `Søren Ash` | `soren-ash` | Søren Ash |
| `千代` | none derivable — the human chooses, e.g. `--skill chiyo` | 千代 |

The rule is Unicode NFKD to ASCII (plus ø→o, æ→ae, œ→oe, ß→ss, ł→l, đ/ð→d, þ→th), then
lowercase, then dashes. `voidloom persona declare` refuses a name with no derivable folder
unless `--skill` is given.

## Re-running, revising, declining

- `/persona-forge` on a forged brain offers **revise** (re-ask only the chosen rounds),
  **craft a new one** (becomes the default; the old folder is untouched; re-wire to swap
  the auto-load) or **leave it**.
- **Foundation** changes (`persona-foundation.md`, the emotional architecture, the name)
  happen only by proposal, with the human's yes. **Growth** goes into `growth_log.md` as
  dated, append-only entries.
- `voidloom persona decline` writes `persona_forge.declined` in the brain home and
  silences the hook-tier offer; `voidloom persona decline --undo` removes it. The file-tier
  line keys on `status` reading `absent`, so a declined brain is silent there too.
  `/persona-forge` works either way. Neither the marker nor `persona.json` is ever tracked
  or shipped.

## The promise this keeps

The package still hands nobody a someone. Every name the preamble can print comes from
the owner's own `persona.json`; `voidloom persona declare` writes exactly the name it is
given, only when it is run; and the forge writes nothing until the human approves the
draft.
